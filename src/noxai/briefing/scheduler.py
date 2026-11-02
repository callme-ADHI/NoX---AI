"""Authoritative Briefing Scheduler for NoX.

Implements the single authoritative briefing scheduling engine inside the daemon lifespan.
Manages morning and evening windows (Asia/Kolkata timezone):
- Morning window: [morning_start, evening_start) of date D (default 04:00 - 18:00)
- Evening window: [evening_start of D, morning_start of D+1) (default 18:00 - 04:00 next day)

Rules:
1. Wall-clock ticks every 30 seconds.
2. Slot is DUE if:
   - now is inside the slot's window
   - AND no run for (date, slot) with state in ('running', 'queued', 'succeeded', 'partial') exists
   - AND no other briefing run is currently active.
3. Due slot is submitted to TaskSupervisor (never inside scheduler loop, never tied to request).
4. Catch-up on boot and after suspend: whenever daemon wakes/starts inside an unserved window,
   run starts within one tick.
5. If a whole window was missed (e.g. PC off from 04:00 to 18:00 and booted at 19:00),
   record state='missed' for the missed slot so history is honest, and run the evening one.
6. Failed runs retry with backoff up to 3 times, then state='failed' plus notification.
7. Tick drift > 2 minutes (system suspend) logs warning and re-evaluates immediately.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta
from datetime import time as dt_time
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


def parse_time_str(val: str, default: dt_time) -> dt_time:
    """Parse 'HH:MM' string to dt_time object."""
    try:
        parts = val.strip().split(":")
        return dt_time(int(parts[0]), int(parts[1]))
    except Exception:
        return default


async def default_briefing_handler(task: Task, cancel_evt: asyncio.Event) -> dict[str, Any]:
    """Default handler executing BriefingPipeline for kind='briefing' tasks."""
    from noxai.briefing.engine import BriefingPipeline

    cfg = get_config()
    payload = json.loads(task.payload_json) if task.payload_json else {}
    slot = payload.get("slot", "morning")
    date_str = payload.get("date")
    run_id = payload.get("run_id")
    rev = payload.get("rev", 1)
    trigger = payload.get("trigger", "scheduled")

    pipeline = BriefingPipeline(config=cfg)
    actual_run_id = await pipeline.execute_run(
        slot=slot,
        date_str=date_str,
        run_id=run_id,
        rev=rev,
        trigger=trigger,
        cancel_event=cancel_evt,
    )
    return {"run_id": actual_run_id, "slot": slot, "date": date_str, "rev": rev}


class BriefingScheduler:
    """Authoritative briefing scheduler for morning and evening slot windows."""

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
        self._tz = ZoneInfo(getattr(self.config.briefing, "timezone", "Asia/Kolkata"))
        self.clock = clock or (lambda: datetime.now(self._tz))
        self.sleep_fn = sleep_fn or asyncio.sleep
        self._registry = task_registry or get_task_registry()
        self._running = False
        self._loop_task: asyncio.Task[None] | None = None
        self._last_tick_mono: float | None = None

        # Register briefing task handler
        if not self._registry.has("briefing"):
            self._registry.register("briefing", default_briefing_handler)

    @property
    def timezone(self) -> ZoneInfo:
        return self._tz

    def get_slot_times(self) -> tuple[dt_time, dt_time]:
        """Return (morning_start_time, evening_start_time) honoring env overrides."""
        m_str = os.environ.get("NOX_BRIEFING_MORNING_START") or getattr(
            self.config.briefing, "morning_start", "04:00"
        )
        e_str = os.environ.get("NOX_BRIEFING_EVENING_START") or getattr(
            self.config.briefing, "evening_start", "18:00"
        )
        m_time = parse_time_str(m_str, dt_time(4, 0))
        e_time = parse_time_str(e_str, dt_time(18, 0))
        return m_time, e_time

    def get_current_time(self) -> datetime:
        dt = self.clock()
        if dt.tzinfo is None:
            return dt.replace(tzinfo=self._tz)
        return dt.astimezone(self._tz)

    def get_window_for_time(
        self, dt: datetime | None = None
    ) -> tuple[str, str, datetime, datetime]:
        """Determine date, slot, and [window_start, window_end) for a given datetime."""
        cur = dt or self.get_current_time()
        m_time, e_time = self.get_slot_times()
        t = cur.time()
        tz = cur.tzinfo

        if m_time <= t < e_time:
            # Morning window: [04:00, 18:00) of date D
            date_str = cur.strftime("%Y-%m-%d")
            slot = "morning"
            win_start = datetime.combine(cur.date(), m_time, tzinfo=tz)
            win_end = datetime.combine(cur.date(), e_time, tzinfo=tz)
        elif t >= e_time:
            # Evening window: [18:00 of D, 04:00 of D+1)
            date_str = cur.strftime("%Y-%m-%d")
            slot = "evening"
            win_start = datetime.combine(cur.date(), e_time, tzinfo=tz)
            win_end = datetime.combine(cur.date() + timedelta(days=1), m_time, tzinfo=tz)
        else:
            # Before morning_start (e.g. 03:59) -> belongs to evening window of previous day
            prev_d = cur.date() - timedelta(days=1)
            date_str = prev_d.strftime("%Y-%m-%d")
            slot = "evening"
            win_start = datetime.combine(prev_d, e_time, tzinfo=tz)
            win_end = datetime.combine(cur.date(), m_time, tzinfo=tz)

        return date_str, slot, win_start, win_end

    def get_next_runs(self, dt: datetime | None = None) -> list[dict[str, Any]]:
        """Calculate next morning and evening scheduled execution datetimes."""
        cur = dt or self.get_current_time()
        m_time, e_time = self.get_slot_times()
        tz = cur.tzinfo
        t = cur.time()

        if t < m_time:
            next_m = datetime.combine(cur.date(), m_time, tzinfo=tz)
            next_e = datetime.combine(cur.date(), e_time, tzinfo=tz)
        elif t < e_time:
            next_m = datetime.combine(cur.date() + timedelta(days=1), m_time, tzinfo=tz)
            next_e = datetime.combine(cur.date(), e_time, tzinfo=tz)
        else:
            next_m = datetime.combine(cur.date() + timedelta(days=1), m_time, tzinfo=tz)
            next_e = datetime.combine(cur.date() + timedelta(days=1), e_time, tzinfo=tz)

        return [
            {"slot": "morning", "at": next_m.isoformat()},
            {"slot": "evening", "at": next_e.isoformat()},
        ]

    async def check_and_record_missed_windows(
        self, now_dt: datetime, db: aiosqlite.Connection
    ) -> list[str]:
        """Record state='missed' for completely elapsed past windows that were never served."""
        m_time, e_time = self.get_slot_times()
        date_str, current_slot, _, _ = self.get_window_for_time(now_dt)
        today = now_dt.date()
        yesterday = today - timedelta(days=1)
        missed_recorded: list[str] = []

        # Windows to verify for complete expiration:
        windows_to_check: list[tuple[str, str, datetime]] = []
        # If currently in evening window of date D:
        # morning window of date D has elapsed at evening_start
        if current_slot == "evening":
            m_win_end = datetime.combine(today, e_time, tzinfo=self._tz)
            windows_to_check.append((date_str, "morning", m_win_end))
            # Also check yesterday evening window
            y_e_win_end = datetime.combine(today, m_time, tzinfo=self._tz)
            windows_to_check.append((yesterday.strftime("%Y-%m-%d"), "evening", y_e_win_end))
        else:
            # Currently in morning window of date D:
            # evening window of yesterday elapsed at morning_start
            e_win_end = datetime.combine(today, m_time, tzinfo=self._tz)
            windows_to_check.append((yesterday.strftime("%Y-%m-%d"), "evening", e_win_end))

        for d_check, s_check, win_end in windows_to_check:
            if now_dt >= win_end:
                # Check if any run exists for (d_check, s_check)
                row = await fetch_one(
                    db,
                    "SELECT id FROM briefing_runs WHERE date = ? AND slot = ?",
                    (d_check, s_check),
                )
                if not row:
                    missed_id = f"brun_missed_{d_check}_{s_check}"
                    title = f"NoX {s_check.capitalize()} Intelligence Briefing — {d_check} (Missed)"
                    now_iso = now_dt.isoformat()
                    await execute(
                        db,
                        """
                        INSERT INTO briefing_runs (
                            id, date, slot, state, trigger, rev, title, summary, created_at
                        )
                        VALUES (?, ?, ?, 'missed', 'scheduled', 1, ?, ?, ?)
                        ON CONFLICT(date, slot, rev) DO NOTHING
                        """,
                        (
                            missed_id,
                            d_check,
                            s_check,
                            title,
                            "Window elapsed while daemon was offline or unserved.",
                            now_iso,
                        ),
                    )
                    missed_recorded.append(f"{d_check}:{s_check}")
                    log.info("Recorded missed briefing window: %s (%s)", d_check, s_check)

        return missed_recorded

    async def is_due(
        self, now_dt: datetime, db: aiosqlite.Connection
    ) -> tuple[bool, str, str, str]:
        """Check if a briefing slot is due for execution.

        Returns (is_due, date_str, slot, reason).
        """
        date_str, slot, _, _ = self.get_window_for_time(now_dt)

        # 1. Check if any briefing run is currently active
        active_run = await fetch_one(
            db,
            "SELECT id FROM briefing_runs WHERE state IN ('running', 'queued') LIMIT 1",
        )
        if active_run:
            return False, date_str, slot, f"Briefing run {active_run['id']} is currently active"

        active_task = await fetch_one(
            db,
            """
            SELECT id FROM tasks
            WHERE kind = 'briefing' AND status IN ('queued', 'running')
            LIMIT 1
            """,
        )
        if active_task:
            return False, date_str, slot, f"Briefing task {active_task['id']} is currently active"

        # 2. Check if (date, slot) is already served (state in running, queued, succeeded, partial)
        existing = await fetch_one(
            db,
            """
            SELECT id, state FROM briefing_runs
            WHERE date = ? AND slot = ? AND state IN ('running', 'queued', 'succeeded', 'partial')
            LIMIT 1
            """,
            (date_str, slot),
        )
        if existing:
            return (
                False,
                date_str,
                slot,
                f"Slot {date_str} {slot} already served ({existing['state']})",
            )

        return True, date_str, slot, "due"

    async def trigger_slot(
        self,
        date_str: str,
        slot: str,
        trigger: str = "scheduled",
        rev: int = 1,
        db: aiosqlite.Connection | None = None,
    ) -> Task | None:
        """Submit a briefing run to TaskSupervisor and record queued state in DB."""
        slot_title = slot.capitalize()
        title = f"NoX {slot_title} Intelligence Briefing — {date_str}"
        if rev > 1:
            title += f" (rev {rev})"
        prompt = f"Generate {slot} intelligence briefing for {date_str}"
        run_id = f"brun_{uuid.uuid4().hex[:12]}"
        now_iso = self.get_current_time().isoformat()
        payload = json.dumps(
            {
                "run_id": run_id,
                "slot": slot,
                "date": date_str,
                "trigger": trigger,
                "rev": rev,
            }
        )

        sup = self.supervisor or get_task_supervisor()
        if not sup:
            log.error("TaskSupervisor unavailable; cannot trigger briefing.")
            return None

        # Determine lane: use cloud if enabled, else local
        cloud_active = getattr(self.config.privacy, "chat_to_cloud", True)
        lane = TaskLane.CLOUD if cloud_active else TaskLane.LOCAL

        async def _submit(conn: aiosqlite.Connection) -> Task:
            # 1. Insert briefing_runs row with state='queued'
            await execute(
                conn,
                """
                INSERT INTO briefing_runs (id, date, slot, state, trigger, rev, title, created_at)
                VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)
                ON CONFLICT(date, slot, rev) DO UPDATE SET
                    state = 'queued',
                    error = NULL
                """,
                (run_id, date_str, slot, trigger, rev, title, now_iso),
            )
            # 2. Submit task
            task = await sup.submit_task(
                title=title,
                prompt=prompt,
                lane=lane,
                priority=TaskPriority.HIGH,
                kind="briefing",
                payload_json=payload,
                owner="scheduler" if trigger == "scheduled" else "user",
                db=conn,
            )
            # 3. Associate task_id
            await execute(
                conn,
                "UPDATE briefing_runs SET task_id = ? WHERE id = ?",
                (task.id, run_id),
            )
            return task

        if db is not None:
            task = await _submit(db)
        else:
            async with get_db() as conn:
                task = await _submit(conn)

        log.info("Submitted %s briefing run %s (task=%s, rev=%d)", trigger, run_id, task.id, rev)
        return task

    async def manual_run(
        self,
        slot: str | None = None,
        date_str: str | None = None,
        db: aiosqlite.Connection | None = None,
    ) -> dict[str, Any]:
        """Trigger an on-demand briefing revision or attach to existing active run.

        If a run is active -> returns 200 {active: true, run_id: ..., ...}
        If no run is active -> queues new revision and returns 202 {run_id: ..., rev: ...}
        """

        async def _run(conn: aiosqlite.Connection) -> dict[str, Any]:
            # 1. Check if a run is already active
            active = await fetch_one(
                conn,
                """
                SELECT br.id, br.slot, br.rev, br.trigger, br.state, t.status AS task_status
                FROM briefing_runs br
                LEFT JOIN tasks t ON t.id = br.task_id
                WHERE br.state IN ('running', 'queued')
                ORDER BY br.created_at DESC LIMIT 1
                """,
            )
            if active:
                if active["task_status"] in ("done", "failed", "cancelled", "interrupted"):
                    new_state = "succeeded" if active["task_status"] == "done" else "failed"
                    await execute(
                        conn,
                        "UPDATE briefing_runs SET state = ? WHERE id = ?",
                        (new_state, active["id"]),
                    )
                    await conn.commit()
                    active = None
                else:
                    return {
                        "active": True,
                        "run_id": active["id"],
                        "slot": active["slot"],
                        "rev": active["rev"],
                        "state": active["state"],
                    }

            # 2. Determine slot and date
            now_dt = self.get_current_time()
            cur_date, cur_slot, _, _ = self.get_window_for_time(now_dt)
            target_slot = slot if slot in ("morning", "evening") else cur_slot
            target_date = date_str or cur_date

            rev_row = await fetch_one(
                conn,
                """
                SELECT COALESCE(MAX(rev), 0) AS max_rev
                FROM briefing_runs
                WHERE date = ? AND slot = ?
                """,
                (target_date, target_slot),
            )
            next_rev = (rev_row["max_rev"] if rev_row else 0) + 1

            # 4. Trigger new revision
            task = await self.trigger_slot(
                date_str=target_date,
                slot=target_slot,
                trigger="manual",
                rev=next_rev,
                db=conn,
            )
            if not task:
                raise RuntimeError("Failed to submit manual briefing task to supervisor.")

            payload = json.loads(task.payload_json or "{}")
            return {
                "active": False,
                "run_id": payload.get("run_id"),
                "task_id": task.id,
                "slot": target_slot,
                "date": target_date,
                "rev": next_rev,
                "state": "queued",
            }

        if db is not None:
            return await _run(db)
        async with get_db() as conn:
            return await _run(conn)

    async def get_status(self, db: aiosqlite.Connection | None = None) -> dict[str, Any]:
        """Return comprehensive scheduler status for UI and CLI inspection."""
        now_dt = self.get_current_time()
        now_iso = now_dt.isoformat()
        next_runs = self.get_next_runs(now_dt)

        async def _query(conn: aiosqlite.Connection) -> dict[str, Any]:
            # Active run
            active_row = await fetch_one(
                conn,
                """
                SELECT br.id, br.slot, br.rev, br.trigger, br.state, br.generation_seconds,
                       t.status AS task_status
                FROM briefing_runs br
                LEFT JOIN tasks t ON t.id = br.task_id
                WHERE br.state IN ('running', 'queued')
                ORDER BY br.created_at DESC LIMIT 1
                """,
            )
            if active_row and active_row["task_status"] in (
                "done",
                "failed",
                "cancelled",
                "interrupted",
            ):
                new_state = "succeeded" if active_row["task_status"] == "done" else "failed"
                await execute(
                    conn,
                    "UPDATE briefing_runs SET state = ? WHERE id = ?",
                    (new_state, active_row["id"]),
                )
                await conn.commit()
                active_row = None
            active_info: dict[str, Any] | None = None
            if active_row:
                run_id = active_row["id"]
                topic_rows = await fetch_all(
                    conn,
                    """
                    SELECT btr.topic_id, bt.name, btr.state, btr.items_staged
                    FROM briefing_topic_runs btr
                    LEFT JOIN briefing_topics bt ON btr.topic_id = bt.id
                    WHERE btr.run_id = ?
                    ORDER BY bt.weight DESC
                    """,
                    (run_id,),
                )
                done_count = sum(1 for r in topic_rows if r["state"] == "done")
                total_topics = len(topic_rows)
                progress = round(done_count / total_topics, 2) if total_topics > 0 else 0.0

                active_info = {
                    "run_id": run_id,
                    "slot": active_row["slot"],
                    "rev": active_row["rev"],
                    "trigger": active_row["trigger"],
                    "state": active_row["state"],
                    "progress": progress,
                    "topics_completed": done_count,
                    "topics_total": total_topics,
                    "topics": [
                        {
                            "id": r["topic_id"],
                            "name": r["name"] or r["topic_id"],
                            "state": r["state"],
                            "items": r["items_staged"],
                        }
                        for r in topic_rows
                    ],
                }

            # Last runs for morning and evening
            last_runs: dict[str, Any] = {"morning": None, "evening": None}
            for s in ("morning", "evening"):
                r = await fetch_one(
                    conn,
                    """
                    SELECT id, date, slot, state, rev, trigger, sources_count,
                           generation_seconds, created_at, finished_at
                    FROM briefing_runs
                    WHERE slot = ? AND state != 'missed'
                    ORDER BY date DESC, rev DESC LIMIT 1
                    """,
                    (s,),
                )
                if r:
                    last_runs[s] = dict(r)

            # Recent missed runs
            missed_rows = await fetch_all(
                conn,
                """
                SELECT id, date, slot, created_at
                FROM briefing_runs
                WHERE state = 'missed'
                ORDER BY created_at DESC LIMIT 5
                """,
            )

            return {
                "now": now_iso,
                "timezone": str(self._tz),
                "next_runs": next_runs,
                "active": active_info,
                "last": last_runs,
                "missed": [dict(m) for m in missed_rows],
            }

        if db is not None:
            return await _query(db)
        async with get_db() as conn:
            return await _query(conn)

    async def cancel_run(self, run_id: str, db: aiosqlite.Connection | None = None) -> bool:
        """Explicitly cancel an active briefing run."""

        async def _cancel(conn: aiosqlite.Connection) -> bool:
            row = await fetch_one(
                conn,
                "SELECT task_id, state FROM briefing_runs WHERE id = ?",
                (run_id,),
            )
            if not row:
                return False

            task_id = row["task_id"]
            sup = self.supervisor or get_task_supervisor()
            if sup and task_id:
                await sup.cancel_task(task_id, db=conn)

            await execute(
                conn,
                "UPDATE briefing_runs SET state = 'cancelled', finished_at = ? WHERE id = ?",
                (self.get_current_time().isoformat(), run_id),
            )
            log.info("Cancelled briefing run %s", run_id)
            return True

        if db is not None:
            return await _cancel(db)
        async with get_db() as conn:
            return await _cancel(conn)

    async def tick(
        self,
        db: aiosqlite.Connection | None = None,
        trigger: str = "scheduled",
    ) -> list[str]:
        """Single 30-second scheduler evaluation tick."""
        now_dt = self.get_current_time()
        now_mono = time.monotonic()

        # Suspend drift detection: gap > 2 minutes
        if self._last_tick_mono is not None:
            gap = now_mono - self._last_tick_mono
            if gap > 120.0:
                log.warning(
                    "Tick drift of %.1fs detected (system suspend/wake). "
                    "Re-evaluating immediately.",
                    gap,
                )
        self._last_tick_mono = now_mono

        async def _tick_internal(conn: aiosqlite.Connection) -> list[str]:
            # 1. Record any missed past windows
            await self.check_and_record_missed_windows(now_dt, conn)

            # 2. Check if current window slot is due
            due, date_str, slot, reason = await self.is_due(now_dt, conn)
            if not due:
                log.debug("Briefing tick (%s %s): not due (%s)", date_str, slot, reason)
                return []

            # 3. Trigger due slot
            task = await self.trigger_slot(date_str, slot, trigger=trigger, rev=1, db=conn)
            if task:
                return [f"{date_str}:{slot}"]
            return []

        if db is not None:
            return await _tick_internal(db)
        async with get_db() as conn:
            return await _tick_internal(conn)

    async def catchup_on_boot(self, db: aiosqlite.Connection | None = None) -> list[str]:
        """Boot catchup logic: evaluates missed past windows and executes current window."""
        return await self.tick(db=db, trigger="scheduled")

    async def start(self) -> None:
        """Start background scheduler loop."""
        if self._loop_task and not self._loop_task.done():
            return
        self._running = True

        # Catchup on startup
        try:
            async with get_db() as db:
                caught = await self.catchup_on_boot(db)
                if caught:
                    log.info("BriefingScheduler startup triggered slot(s): %s", caught)
        except Exception as exc:
            log.warning("BriefingScheduler boot catchup error: %s", exc)

        self._loop_task = asyncio.create_task(self._scheduler_loop(), name="nox_briefing_scheduler")
        log.info("BriefingScheduler started (timezone=%s)", self._tz)

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
                log.error("BriefingScheduler tick loop error: %s", exc)
                await self.sleep_fn(10.0)
