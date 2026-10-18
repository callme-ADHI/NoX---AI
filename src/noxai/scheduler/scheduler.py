"""Scheduler loop, job execution, reminders, and catch-up handling per Section 6.7."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from noxai.db import execute, fetch_all, fetch_one
from noxai.events import EventBus, global_event_bus
from noxai.scheduler.delivery import DeliveryRouter
from noxai.scheduler.time_utils import (
    DEFAULT_TIMEZONE,
    compute_next_run,
    get_zoneinfo,
    should_catchup,
)

if TYPE_CHECKING:
    from pathlib import Path

    import aiosqlite

    from noxai.config import Settings
    from noxai.models.router import ModelRouter
    from noxai.tools.registry import ToolRegistry

log = logging.getLogger(__name__)


class JobScheduler:
    """Tick-based job scheduler executing periodic agent tasks and direct reminders."""

    def __init__(
        self,
        router: ModelRouter | None = None,
        registry: ToolRegistry | None = None,
        config: Settings | None = None,
        db_path: Path | None = None,
        delivery_router: DeliveryRouter | None = None,
        event_bus: EventBus | None = None,
        tick_seconds: float = 15.0,
    ) -> None:
        from noxai.config import get_config
        from noxai.models.router import get_default_router
        from noxai.tools.registry import default_registry

        self.config = config or get_config()
        self.router = router or get_default_router(self.config)
        self.registry = registry or default_registry
        self.db_path = db_path
        self.delivery_router = delivery_router or DeliveryRouter()
        self.event_bus = event_bus or global_event_bus
        self.tick_seconds = tick_seconds

        # Concurrency bound: exactly 1 local-model job at a time per Section 6.7
        self._local_job_sem = asyncio.Semaphore(1)
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def catch_up_missed_jobs(self, db: aiosqlite.Connection) -> int:
        """Scan enabled jobs on daemon startup and apply catchup policy per Section 6.7."""
        now_utc = datetime.now(UTC)
        now_iso = now_utc.isoformat()

        rows = await fetch_all(
            db,
            """
            SELECT id, name, schedule_type, schedule_expr, timezone, instruction,
                   config_json, next_run_at
            FROM jobs
            WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ?
            """,
            (now_iso,),
        )

        caught_up = 0
        for r in rows:
            cfg = json.loads(r["config_json"]) if r["config_json"] else {}
            catchup_policy = cfg.get("catchup", "once")
            next_run_str = str(r["next_run_at"])

            try:
                next_dt = datetime.fromisoformat(next_run_str)
                if next_dt.tzinfo is None:
                    next_dt = next_dt.replace(tzinfo=UTC)
            except Exception:
                next_dt = now_utc

            if should_catchup(next_dt, now_utc, catchup_policy):
                log.info("Catching up missed job '%s' (scheduled for %s)", r["name"], next_run_str)
                await self.run_job(r, db, run_context="catchup")
                caught_up += 1
            else:
                # Skip to next occurrence
                new_next = compute_next_run(
                    schedule_type=str(r["schedule_type"]),
                    schedule_expr=str(r["schedule_expr"]),
                    tz_name=str(r["timezone"] or DEFAULT_TIMEZONE),
                    base_dt=now_utc,
                )
                new_iso = new_next.isoformat() if new_next else None
                await execute(
                    db,
                    "UPDATE jobs SET next_run_at = ?, updated_at = datetime('now') WHERE id = ?",
                    (new_iso, str(r["id"])),
                )
                log.info(
                    "Skipped missed job '%s'; rescheduled to %s",
                    r["name"],
                    new_iso,
                )

        if rows:
            await db.commit()
        return caught_up

    async def tick(self, db: aiosqlite.Connection) -> list[str]:
        """Execute a single scheduler tick: find due jobs and run or fire them."""
        now_utc = datetime.now(UTC)
        now_iso = now_utc.isoformat()

        due_jobs = await fetch_all(
            db,
            """
            SELECT id, name, schedule_type, schedule_expr, timezone, instruction,
                   config_json, next_run_at
            FROM jobs
            WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ?
            ORDER BY next_run_at ASC
            """,
            (now_iso,),
        )

        executed_names: list[str] = []
        for job_row in due_jobs:
            name = str(job_row["name"])
            try:
                await self.run_job(job_row, db, run_context="tick")
                executed_names.append(name)
            except Exception as exc:
                log.error("Error executing due job '%s': %s", name, exc)

        return executed_names

    async def run_job(
        self,
        job_row: Any,
        db: aiosqlite.Connection,
        run_context: str = "manual",
    ) -> dict[str, Any]:
        """Execute a single job (reminder or agent task) and schedule its next occurrence."""
        job_id = str(job_row["id"])
        job_name = str(job_row["name"])
        schedule_type = str(job_row["schedule_type"])
        schedule_expr = str(job_row["schedule_expr"])
        tz_name = str(job_row["timezone"] or DEFAULT_TIMEZONE)
        instruction = str(job_row["instruction"])
        cfg = json.loads(job_row["config_json"]) if job_row["config_json"] else {}

        kind = cfg.get("kind", "agent")
        job_run_id = f"jrun_{uuid.uuid4().hex[:12]}"

        # Record job run started
        await execute(
            db,
            """
            INSERT INTO job_runs (id, job_id, started_at, status)
            VALUES (?, ?, datetime('now'), 'running')
            """,
            (job_run_id, job_id),
        )
        await db.commit()

        output_text = ""
        status = "ok"
        error_msg: str | None = None

        if kind == "reminder":
            # Section 6.7: "Reminders fire WITHOUT ANY LLM CALL: deliver the message directly."
            message = cfg.get("message") or instruction
            channels = cfg.get("delivery") or ["ui", "desktop"]
            title = f"Reminder: {job_name}"

            await self.delivery_router.deliver(
                channel_names=channels,
                title=title,
                message=message,
                payload={"kind": "reminder", "job_id": job_id, "job_name": job_name},
                db=db,
            )
            output_text = message

        elif job_name == "memory-consolidation":
            # Section 6.7: Calling the built-in consolidation routine
            async with self._local_job_sem:
                try:
                    from noxai.memory.consolidate import run_memory_consolidation

                    rep = await run_memory_consolidation(
                        db=db,
                        router=self.router,
                        config=self.config,
                    )
                    output_text = (
                        f"Memory consolidation completed: {rep.episodes_summarized} episodes "
                        f"summarized, {rep.facts_merged} facts merged, "
                        f"{rep.episodes_archived} archived."
                    )
                except Exception as exc:
                    log.error("Memory consolidation job failed: %s", exc)
                    status = "failed"
                    error_msg = str(exc)
                    output_text = f"Consolidation error: {exc}"

        else:
            # General agent job: pipeline with complexity gate & DAG / loop
            tz = get_zoneinfo(tz_name)
            today_str = datetime.now(tz).strftime("%A, %d %B %Y")
            formatted_instruction = instruction.replace("{today}", today_str)

            allow_cloud = bool(cfg.get("allow_cloud", False))

            async with self._local_job_sem:
                try:
                    from noxai.agent.gate import evaluate_complexity

                    gate_res = await evaluate_complexity(
                        formatted_instruction,
                        router=self.router,
                    )

                    if gate_res.is_complex:
                        from noxai.agent.executor import DAGExecutor

                        executor = DAGExecutor(
                            router=self.router,
                            registry=self.registry,
                            config=self.config,
                            event_bus=self.event_bus,
                        )
                        exec_res = await executor.execute_plan(
                            goal=formatted_instruction,
                            allow_cloud=allow_cloud,
                            db=db,
                        )
                        output_text = exec_res.final_text
                        status = "ok" if exec_res.status == "done" else "failed"
                    else:
                        from noxai.agent.loop import AgentLoop

                        loop = AgentLoop(
                            router=self.router,
                            registry=self.registry,
                            config=self.config,
                            event_bus=self.event_bus,
                        )
                        loop_res = await loop.run(
                            formatted_instruction,
                            allow_cloud=allow_cloud,
                            db=db,
                        )
                        output_text = loop_res.text
                        status = "ok" if loop_res.status == "done" else "failed"

                    # Deliver output
                    channels = cfg.get("delivery") or ["ui"]
                    await self.delivery_router.deliver(
                        channel_names=channels,
                        title=f"Briefing: {job_name}",
                        message=output_text[:300],
                        payload={"kind": "briefing", "job_id": job_id, "job_run_id": job_run_id},
                        db=db,
                    )

                    # Store in outputs table for Briefings view
                    await execute(
                        db,
                        """
                        INSERT INTO outputs (job_run_id, title, markdown)
                        VALUES (?, ?, ?)
                        """,
                        (job_run_id, job_name, output_text),
                    )

                except Exception as exc:
                    log.error("Agent job '%s' execution error: %s", job_name, exc)
                    status = "failed"
                    error_msg = str(exc)
                    output_text = f"Execution failed: {exc}"

        # Finalize job_runs record
        await execute(
            db,
            """
            UPDATE job_runs
            SET finished_at = datetime('now'), status = ?, output_markdown = ?, error = ?
            WHERE id = ?
            """,
            (status, output_text, error_msg, job_run_id),
        )

        # Reschedule or disable job
        now_utc = datetime.now(UTC)
        if schedule_type == "once":
            # Once jobs disable themselves after firing
            await execute(
                db,
                """
                UPDATE jobs
                SET enabled = 0, last_run_at = datetime('now'), next_run_at = NULL,
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (job_id,),
            )
        else:
            new_next = compute_next_run(
                schedule_type=schedule_type,
                schedule_expr=schedule_expr,
                tz_name=tz_name,
                base_dt=now_utc,
            )
            new_iso = new_next.isoformat() if new_next else None
            await execute(
                db,
                """
                UPDATE jobs
                SET last_run_at = datetime('now'), next_run_at = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (new_iso, job_id),
            )

        await db.commit()
        return {
            "job_run_id": job_run_id,
            "status": status,
            "output": output_text,
            "error": error_msg,
        }

    async def run_job_now(
        self,
        job_name_or_id: str,
        db: aiosqlite.Connection,
    ) -> dict[str, Any]:
        """Trigger immediate on-demand execution of any job by name or ID."""
        row = await fetch_one(
            db,
            "SELECT * FROM jobs WHERE id = ? OR name = ?",
            (job_name_or_id, job_name_or_id),
        )
        if row is None:
            raise ValueError(f"Job '{job_name_or_id}' not found.")
        return await self.run_job(row, db, run_context="manual")

    async def start(self) -> None:
        """Start the background tick loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        log.info("JobScheduler loop started (tick=%.1fs).", self.tick_seconds)

    async def stop(self) -> None:
        """Stop the background tick loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("JobScheduler loop stopped.")

    async def _run_loop(self) -> None:
        """Periodic background tick loop."""
        from noxai.db import get_db

        # Initial startup catch-up
        try:
            async with get_db() as db:
                await self.catch_up_missed_jobs(db)
        except Exception as exc:
            log.warning("Catch-up missed jobs on startup error: %s", exc)

        while self._running:
            try:
                async with get_db() as db:
                    await self.tick(db)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("Scheduler tick exception: %s", exc)

            try:
                await asyncio.sleep(self.tick_seconds)
            except asyncio.CancelledError:
                break
