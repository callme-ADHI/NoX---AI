"""Briefing Scheduler for morning (04:00) and evening (18:00) Asia/Kolkata slots."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import aiosqlite

from noxai.config import Settings, get_config
from noxai.db import execute, fetch_all, fetch_one, get_db
from noxai.tasks.models import Task, TaskLane, TaskPriority
from noxai.tasks.supervisor import (
    TaskHandlerRegistry,
    TaskSupervisor,
    get_task_registry,
    get_task_supervisor,
)

log = logging.getLogger(__name__)

KOLKATA_TZ = ZoneInfo("Asia/Kolkata")
SLOT_TIMES: list[tuple[str, time]] = [
    ("morning", time(4, 0)),
    ("evening", time(18, 0)),
]


async def default_briefing_handler(task: Task, cancel_evt: asyncio.Event) -> dict[str, Any]:
    """Default handler executing BriefingEngine for kind='briefing' tasks."""
    from noxai.briefing.engine import BriefingEngine
    from noxai.config import get_config

    cfg = get_config()
    payload = json.loads(task.payload_json) if task.payload_json else {}
    slot = payload.get("slot", "morning")
    date_str = payload.get("date")

    engine = BriefingEngine(config=cfg)
    run_id = await engine.execute_run(slot=slot, date_str=date_str)
    return {"run_id": run_id, "slot": slot, "date": date_str}


class BriefingScheduler:
    """Manages scheduled daily intelligence briefings (04:00 & 18:00 Asia/Kolkata)."""

    def __init__(
        self,
        config: Settings | None = None,
        supervisor: TaskSupervisor | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], Coroutine[Any, Any, None]] | None = None,
        task_registry: TaskHandlerRegistry | None = None,
    ) -> None:
        self.config = config or get_config()
        self.supervisor = supervisor or get_task_supervisor()
        self.clock = clock or (lambda: datetime.now(KOLKATA_TZ))
        self.sleep_fn = sleep_fn or asyncio.sleep
        self._registry = task_registry or get_task_registry()
        self._running = False
        self._loop_task: asyncio.Task[None] | None = None

        # Ensure handler is registered for 'briefing' tasks
        if not self._registry.has("briefing"):
            self._registry.register("briefing", default_briefing_handler)

    def get_current_time(self) -> datetime:
        dt = self.clock()
        if dt.tzinfo is None:
            return dt.replace(tzinfo=KOLKATA_TZ)
        return dt.astimezone(KOLKATA_TZ)

    async def is_slot_executed_or_queued(
        self, date_str: str, slot: str, db: aiosqlite.Connection | None = None
    ) -> bool:
        """Check (date, slot) uniqueness across briefing_runs and active tasks."""
        query_runs = (
            "SELECT 1 FROM briefing_runs WHERE date = ? AND slot = ? "
            "AND state IN ('running', 'succeeded')"
        )
        query_tasks = (
            "SELECT 1 FROM tasks WHERE kind = 'briefing' "
            "AND payload_json LIKE ? AND payload_json LIKE ? "
            "AND status IN ('queued', 'running', 'done')"
        )
        date_pattern = f'%"{date_str}"%'
        slot_pattern = f'%"{slot}"%'

        async def _check(conn: aiosqlite.Connection) -> bool:
            r1 = await fetch_one(conn, query_runs, (date_str, slot))
            if r1:
                return True
            r2 = await fetch_one(conn, query_tasks, (date_pattern, slot_pattern))
            return r2 is not None

        if db is not None:
            return await _check(db)
        async with get_db() as conn:
            return await _check(conn)

    async def trigger_slot(
        self,
        date_str: str,
        slot: str,
        db: aiosqlite.Connection | None = None,
    ) -> Task | None:
        """Submit a briefing task via TaskSupervisor if not already run for (date, slot)."""
        already_run = await self.is_slot_executed_or_queued(date_str, slot, db=db)
        if already_run:
            log.info("Briefing for %s (%s) already executed or queued; skipping.", date_str, slot)
            return None

        slot_title = "Morning" if slot == "morning" else "Evening"
        title = f"NoX {slot_title} Intelligence Briefing — {date_str}"
        prompt = f"Generate {slot} intelligence briefing for {date_str}"
        payload = json.dumps({"slot": slot, "date": date_str})

        sup = self.supervisor or get_task_supervisor()
        if not sup:
            log.error("Cannot submit briefing: TaskSupervisor is not initialized.")
            return None

        task = await sup.submit_task(
            title=title,
            prompt=prompt,
            lane=TaskLane.LOCAL,
            priority=TaskPriority.HIGH,
            kind="briefing",
            payload_json=payload,
            owner="scheduler",
            db=db,
        )
        log.info("Submitted briefing task %s for %s (%s).", task.id, date_str, slot)
        return task

    async def catchup_on_boot(self, db: aiosqlite.Connection | None = None) -> list[str]:
        """Check on daemon boot if today's elapsed slots missed execution."""
        now_dt = self.get_current_time()
        today_str = now_dt.strftime("%Y-%m-%d")
        now_time = now_dt.time()

        triggered: list[str] = []
        for slot, slot_time in SLOT_TIMES:
            if now_time >= slot_time:
                task = await self.trigger_slot(today_str, slot, db=db)
                if task:
                    triggered.append(f"{today_str}:{slot}")

        return triggered

    async def tick(self, db: aiosqlite.Connection | None = None) -> list[str]:
        """Perform a single scheduler check tick."""
        now_dt = self.get_current_time()
        today_str = now_dt.strftime("%Y-%m-%d")
        now_time = now_dt.time()

        triggered: list[str] = []
        for slot, slot_time in SLOT_TIMES:
            # Check if within current minute of slot_time
            if now_time.hour == slot_time.hour and now_time.minute == slot_time.minute:
                task = await self.trigger_slot(today_str, slot, db=db)
                if task:
                    triggered.append(f"{today_str}:{slot}")

        return triggered

    async def start(self) -> None:
        """Start the background briefing scheduler loop and run boot catchup."""
        if self._loop_task and not self._loop_task.done():
            return
        self._running = True

        # 1. Run catchup on boot
        try:
            async with get_db() as db:
                caught_up = await self.catchup_on_boot(db)
                if caught_up:
                    log.info("BriefingScheduler boot catchup triggered: %s", caught_up)
        except Exception as exc:
            log.warning("BriefingScheduler boot catchup error: %s", exc)

        # 2. Start loop
        self._loop_task = asyncio.create_task(self._scheduler_loop(), name="nox_briefing_scheduler")
        log.info("BriefingScheduler started (04:00 & 18:00 Asia/Kolkata).")

    async def stop(self) -> None:
        self._running = False
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        log.info("BriefingScheduler stopped.")

    async def _scheduler_loop(self) -> None:
        while self._running:
            try:
                async with get_db() as db:
                    await self.tick(db)
                await self.sleep_fn(30.0)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("BriefingScheduler loop tick error: %s", exc)
                await self.sleep_fn(10.0)
