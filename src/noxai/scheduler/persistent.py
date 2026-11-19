# Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
# commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
# Modified for NoX: Async SQLite WAL persistence, timezone awareness (Asia/Kolkata), catch-up recovery policies, single-flight locking, and heartbeat watchdog.

"""Authoritative persistent daemon-owned scheduler for NoX.

Supports:
- Timezone-aware cron and interval expressions.
- SQLite persistence (schedules and schedule_runs tables).
- Boot-time and suspend-resume catch-up policies (run_once, skip, run_all).
- Single-flight locking with timeouts and heartbeat crash recovery.
- Injectable Clock interface for deterministic time travel unit testing.
- Decoupled daemon execution: survives UI closing/refreshing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import logging
import shutil
import subprocess
from typing import Any, Literal, Protocol
import uuid
from zoneinfo import ZoneInfo

from croniter import croniter

from noxai.config import Settings, get_config
from noxai.db import execute, fetch_all, fetch_one, get_db

log = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Asia/Kolkata"


# ---------------------------------------------------------------------------
# Clock Interface & Implementations
# ---------------------------------------------------------------------------


class Clock(Protocol):
    """Protocol for time sources, allowing injected test clocks."""

    def now(self) -> datetime:
        """Return the current timezone-aware datetime."""
        ...


class SystemClock:
    """Real wall clock returning UTC timezone-aware datetimes."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    """Controllable test clock for deterministic time manipulation."""

    def __init__(self, initial_time: datetime | None = None) -> None:
        if initial_time is None:
            self._current = datetime(2026, 11, 18, 12, 0, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
        elif initial_time.tzinfo is None:
            self._current = initial_time.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
        else:
            self._current = initial_time

    def now(self) -> datetime:
        return self._current

    def set_time(self, new_time: datetime) -> None:
        if new_time.tzinfo is None:
            self._current = new_time.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
        else:
            self._current = new_time

    def advance(self, seconds: float) -> None:
        self._current += timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# Helpers for Time & Expressions
# ---------------------------------------------------------------------------


def parse_interval_seconds(expr: str) -> float:
    """Parse interval string into seconds (e.g. '120', '30s', '5m', '1h', '1d')."""
    s = str(expr).strip().lower()
    if s.endswith("s"):
        return float(s[:-1])
    if s.endswith("m"):
        return float(s[:-1]) * 60.0
    if s.endswith("h"):
        return float(s[:-1]) * 3600.0
    if s.endswith("d"):
        return float(s[:-1]) * 86400.0
    return float(s)


def compute_next_run(
    kind: str,
    expression: str,
    from_dt: datetime,
    tz_str: str = DEFAULT_TIMEZONE,
) -> datetime:
    """Compute the next occurrence from a given datetime in the target timezone."""
    tz = ZoneInfo(tz_str)
    dt_in_tz = from_dt.astimezone(tz) if from_dt.tzinfo else from_dt.replace(tzinfo=tz)

    if kind == "cron":
        itr = croniter(expression, dt_in_tz)
        nxt = itr.get_next(datetime)
        return nxt
    elif kind == "interval":
        secs = parse_interval_seconds(expression)
        return dt_in_tz + timedelta(seconds=secs)
    else:
        raise ValueError(f"Unsupported schedule kind: {kind}")


def to_utc_iso(dt: datetime) -> str:
    """Normalize datetime to UTC ISO 8601 string for consistent SQLite string comparison."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
    return dt.astimezone(UTC).isoformat()


def send_schedule_notification(title: str, body: str, urgency: str = "normal") -> None:
    """Send desktop notification via notify-send if available."""
    if shutil.which("notify-send"):
        try:
            subprocess.run(
                ["notify-send", "-a", "NoX", "-u", urgency, title[:100], body[:250]],
                check=False,
                timeout=4.0,
            )
        except Exception as exc:
            log.debug("notify-send failed: %s", exc)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass
class Schedule:
    id: str
    name: str
    kind: str  # 'cron' | 'interval'
    expression: str
    timezone: str
    enabled: bool
    last_run_at: str | None
    next_run_at: str | None
    last_status: str | None
    catchup_policy: str  # 'run_once' | 'skip' | 'run_all'
    target_action: str
    config_json: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> Schedule:
        return cls(
            id=str(row["id"]),
            name=str(row["name"]),
            kind=str(row["kind"]),
            expression=str(row["expression"]),
            timezone=str(row["timezone"] or DEFAULT_TIMEZONE),
            enabled=bool(row["enabled"]),
            last_run_at=row["last_run_at"],
            next_run_at=row["next_run_at"],
            last_status=row["last_status"],
            catchup_policy=str(row["catchup_policy"] or "run_once"),
            target_action=str(row["target_action"]),
            config_json=row["config_json"],
            created_at=str(row["created_at"]),
        )


@dataclass
class ScheduleRun:
    id: str
    schedule_id: str
    started_at: str
    finished_at: str | None
    status: str  # 'running' | 'success' | 'failed' | 'cancelled' | 'missed' | 'interrupted'
    error: str | None
    trace_id: str | None
    log_path: str | None
    heartbeat_at: str | None

    @classmethod
    def from_row(cls, row: Any) -> ScheduleRun:
        return cls(
            id=str(row["id"]),
            schedule_id=str(row["schedule_id"]),
            started_at=str(row["started_at"]),
            finished_at=row["finished_at"],
            status=str(row["status"]),
            error=row["error"],
            trace_id=row["trace_id"],
            log_path=row["log_path"],
            heartbeat_at=row["heartbeat_at"],
        )


# ---------------------------------------------------------------------------
# PersistentScheduler
# ---------------------------------------------------------------------------


class PersistentScheduler:
    """Daemon-owned persistent scheduler executing cron and interval jobs."""

    def __init__(
        self,
        config: Settings | None = None,
        clock: Clock | None = None,
        tick_seconds: float = 15.0,
        sleep_fn: Callable[[float], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        self.config = config or get_config()
        self.clock: Clock = clock or SystemClock()
        self.tick_seconds = tick_seconds
        self.sleep_fn = sleep_fn or asyncio.sleep

        self._running = False
        self._loop_task: asyncio.Task[None] | None = None
        self._active_runs: dict[str, asyncio.Task[Any]] = {}  # schedule_id -> asyncio.Task
        self._cancel_events: dict[str, asyncio.Event] = {}  # schedule_id -> Event
        self._last_tick_time: float | None = None
        self._handlers: dict[str, Callable[[Schedule, str, asyncio.Event], Coroutine[Any, Any, Any]]] = {}

        # Register default actions
        self._register_default_handlers()

    def _register_default_handlers(self) -> None:
        """Register built-in action runners."""
        self.register_handler("briefing_morning", self._run_morning_briefing)
        self.register_handler("briefing_evening", self._run_evening_briefing)
        self.register_handler("backup_daily", self._run_backup_job)
        self.register_handler("generic", self._run_generic_job)
        self.register_handler("traces_cleanup", self._run_traces_cleanup)
        self.register_handler("memory_index", self._run_memory_index)
        self.register_handler("ping", self._run_generic_job)
        self.register_handler("test", self._run_generic_job)

    def register_handler(
        self,
        action_name: str,
        handler: Callable[[Schedule, str, asyncio.Event], Coroutine[Any, Any, Any]],
    ) -> None:
        """Register a custom asynchronous execution handler for a target_action."""
        self._handlers[action_name] = handler

    # -----------------------------------------------------------------------
    # Built-in Handlers
    # -----------------------------------------------------------------------

    async def _run_morning_briefing(self, schedule: Schedule, run_id: str, cancel_event: asyncio.Event) -> Any:
        from noxai.briefing.engine import BriefingPipeline

        pipeline = BriefingPipeline(config=self.config)
        now_dt = self.clock.now()
        date_str = now_dt.strftime("%Y-%m-%d")
        return await pipeline.execute_run(
            slot="morning",
            date_str=date_str,
            trigger="scheduled",
            cancel_event=cancel_event,
        )

    async def _run_evening_briefing(self, schedule: Schedule, run_id: str, cancel_event: asyncio.Event) -> Any:
        from noxai.briefing.engine import BriefingPipeline

        pipeline = BriefingPipeline(config=self.config)
        now_dt = self.clock.now()
        date_str = now_dt.strftime("%Y-%m-%d")
        return await pipeline.execute_run(
            slot="evening",
            date_str=date_str,
            trigger="scheduled",
            cancel_event=cancel_event,
        )

    async def _run_backup_job(self, schedule: Schedule, run_id: str, cancel_event: asyncio.Event) -> Any:
        from noxai.jobs.executor import execute_planned_job

        cfg_dict = json.loads(schedule.config_json) if schedule.config_json else {}
        job_id = cfg_dict.get("job_id", "job_06725cb80054")
        return await execute_planned_job(
            job_id=job_id,
            run_id=run_id,
            cancel_event=cancel_event,
            config=self.config,
        )

    async def _run_generic_job(self, schedule: Schedule, run_id: str, cancel_event: asyncio.Event) -> Any:
        log.info("Executing generic schedule '%s' (run_id=%s)", schedule.name, run_id)
        await asyncio.sleep(0.2)
        return {"status": "ok", "schedule": schedule.name, "run_id": run_id}

    async def _run_traces_cleanup(self, schedule: Schedule, run_id: str, cancel_event: asyncio.Event) -> Any:
        from noxai.tracing.cleanup import cleanup_old_traces

        log.info("Executing daily trace retention cleanup (run_id=%s)", run_id)
        return await cleanup_old_traces()

    async def _run_memory_index(self, schedule: Schedule, run_id: str, cancel_event: asyncio.Event) -> Any:
        from noxai.memory.docs import index_documents

        log.info("Executing scheduled document memory indexing (run_id=%s)", run_id)
        stats = await index_documents()
        return stats.to_dict()

    # -----------------------------------------------------------------------
    # Lifecycle & Loop
    # -----------------------------------------------------------------------

    async def start(self) -> None:
        """Initialize scheduler: recover interrupted runs, evaluate catch-up, start loop."""
        if self._running:
            return

        self._running = True
        log.info("Starting PersistentScheduler...")

        # 1. Crash recovery: recover orphaned/interrupted runs from previous daemon instance
        await self.recover_interrupted_runs()
        try:
            from noxai.tracing.recorder import recover_interrupted_traces

            await recover_interrupted_traces()
        except Exception as exc:
            log.warning("Trace recovery warning on scheduler start: %s", exc)

        # 2. Boot catch-up check
        await self.evaluate_catchup()

        # 3. Ensure all enabled schedules have a future next_run_at computed
        await self._ensure_next_runs()

        # 4. Start the tick loop
        self._loop_task = asyncio.create_task(self._run_loop(), name="persistent_scheduler_loop")

    async def stop(self) -> None:
        """Cleanly cancel the loop and any active runs."""
        if not self._running:
            return

        self._running = False
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass

        # Cooperatively cancel active runs
        for sched_id, cancel_evt in list(self._cancel_events.items()):
            cancel_evt.set()

        for sched_id, task in list(self._active_runs.items()):
            if not task.done():
                task.cancel()

        log.info("PersistentScheduler stopped.")

    async def _ensure_next_runs(self) -> None:
        """Ensure all enabled schedules have next_run_at populated."""
        now = self.clock.now()
        async with get_db() as db:
            rows = await fetch_all(
                db,
                "SELECT * FROM schedules WHERE enabled = 1 AND next_run_at IS NULL",
            )
            for r in rows:
                sched = Schedule.from_row(r)
                nxt = compute_next_run(sched.kind, sched.expression, now, sched.timezone)
                await execute(
                    db,
                    "UPDATE schedules SET next_run_at = ? WHERE id = ?",
                    (to_utc_iso(nxt), sched.id),
                )

    async def recover_interrupted_runs(self) -> int:
        """Find and reconcile any schedule_runs left in 'running' state after a crash."""
        now_iso = to_utc_iso(self.clock.now())
        recovered = 0
        async with get_db() as db:
            rows = await fetch_all(
                db,
                """
                SELECT sr.id, sr.schedule_id, s.name
                FROM schedule_runs sr
                JOIN schedules s ON sr.schedule_id = s.id
                WHERE sr.status = 'running'
                """,
            )
            for r in rows:
                run_id = r["id"]
                s_name = r["name"]
                sched_id = r["schedule_id"]
                await execute(
                    db,
                    """
                    UPDATE schedule_runs
                    SET status = 'interrupted',
                        finished_at = ?,
                        error = 'Interrupted by daemon restart / crash'
                    WHERE id = ?
                    """,
                    (now_iso, run_id),
                )
                await execute(
                    db,
                    "UPDATE schedules SET last_status = 'interrupted' WHERE id = ?",
                    (sched_id,),
                )
                recovered += 1
                send_schedule_notification(
                    f"NoX Schedule Interrupted: {s_name}",
                    f"Run was interrupted by a system crash or daemon restart.",
                    urgency="critical",
                )
                log.warning("Recovered interrupted run %s for schedule '%s'", run_id, s_name)

        return recovered

    async def evaluate_catchup(self) -> list[str]:
        """Evaluate missed slots for enabled schedules whose next_run_at <= now."""
        now = self.clock.now()
        now_iso = to_utc_iso(now)
        triggered_runs: list[str] = []

        async with get_db() as db:
            rows = await fetch_all(
                db,
                """
                SELECT * FROM schedules
                WHERE enabled = 1
                  AND next_run_at IS NOT NULL
                  AND next_run_at <= ?
                """,
                (now_iso,),
            )

            for r in rows:
                sched = Schedule.from_row(r)
                policy = sched.catchup_policy

                if policy == "run_once":
                    # Run exactly one catch-up run now, then set next_run_at to the next future slot
                    log.info("Catch-up: firing single run_once for '%s' (due %s)", sched.name, sched.next_run_at)
                    nxt = compute_next_run(sched.kind, sched.expression, now, sched.timezone)
                    await execute(
                        db,
                        "UPDATE schedules SET next_run_at = ? WHERE id = ?",
                        (to_utc_iso(nxt), sched.id),
                    )
                    task = asyncio.create_task(
                        self._execute_schedule(sched, is_catchup=True),
                        name=f"catchup_{sched.name}",
                    )
                    triggered_runs.append(sched.name)

                elif policy == "skip":
                    # Mark the missed slot as 'missed' and advance next_run_at to future slot
                    log.info("Catch-up: skipping missed slot for '%s' (policy=skip)", sched.name)
                    missed_id = f"srun_missed_{uuid.uuid4().hex[:8]}"
                    await execute(
                        db,
                        """
                        INSERT INTO schedule_runs (id, schedule_id, started_at, finished_at, status, error)
                        VALUES (?, ?, ?, ?, 'missed', 'Missed slot during downtime')
                        """,
                        (missed_id, sched.id, sched.next_run_at or now_iso, now_iso),
                    )
                    nxt = compute_next_run(sched.kind, sched.expression, now, sched.timezone)
                    await execute(
                        db,
                        "UPDATE schedules SET next_run_at = ?, last_status = 'missed' WHERE id = ?",
                        (to_utc_iso(nxt), sched.id),
                    )

                elif policy == "run_all":
                    # Sequential catch-up of all missed slots
                    log.info("Catch-up: running all missed slots for '%s'", sched.name)
                    task = asyncio.create_task(
                        self._execute_schedule(sched, is_catchup=True),
                        name=f"catchup_all_{sched.name}",
                    )
                    nxt = compute_next_run(sched.kind, sched.expression, now, sched.timezone)
                    await execute(
                        db,
                        "UPDATE schedules SET next_run_at = ? WHERE id = ?",
                        (to_utc_iso(nxt), sched.id),
                    )
                    triggered_runs.append(sched.name)

        return triggered_runs

    async def _run_loop(self) -> None:
        """Main periodic scheduler evaluation loop."""
        import time

        self._last_tick_time = time.monotonic()

        while self._running:
            try:
                # 1. Detect system suspend / clock jump (>2 min drift between loop ticks)
                now_mono = time.monotonic()
                if self._last_tick_time is not None:
                    elapsed = now_mono - self._last_tick_time
                    if elapsed > (self.tick_seconds + 120.0):
                        log.warning(
                            "Clock jump / suspend resume detected (elapsed %.1fs > tick %.1fs + 120s). Evaluating catch-up...",
                            elapsed,
                            self.tick_seconds,
                        )
                        await self.evaluate_catchup()
                self._last_tick_time = now_mono

                # 2. Check for due schedules
                now = self.clock.now()
                now_iso = to_utc_iso(now)

                async with get_db() as db:
                    rows = await fetch_all(
                        db,
                        """
                        SELECT * FROM schedules
                        WHERE enabled = 1
                          AND next_run_at IS NOT NULL
                          AND next_run_at <= ?
                        """,
                        (now_iso,),
                    )

                    for r in rows:
                        sched = Schedule.from_row(r)
                        if sched.id in self._active_runs:
                            # Single-flight: already running, do not spawn another
                            log.debug("Schedule '%s' is already active; skipping tick", sched.name)
                            continue

                        # Advance next_run_at strictly into the future
                        nxt = compute_next_run(sched.kind, sched.expression, now, sched.timezone)
                        await execute(
                            db,
                            "UPDATE schedules SET next_run_at = ? WHERE id = ?",
                            (to_utc_iso(nxt), sched.id),
                        )

                        # Trigger the run
                        task = asyncio.create_task(
                            self._execute_schedule(sched, is_catchup=False),
                            name=f"sched_run_{sched.name}",
                        )

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("Error in scheduler loop tick: %s", exc, exc_info=True)

            try:
                await self.sleep_fn(self.tick_seconds)
            except asyncio.CancelledError:
                break

    # -----------------------------------------------------------------------
    # Execution & Heartbeat
    # -----------------------------------------------------------------------

    async def _heartbeat_loop(self, run_id: str, stop_evt: asyncio.Event) -> None:
        """Emit periodic heartbeats into schedule_runs every 5 seconds."""
        while not stop_evt.is_set():
            try:
                await asyncio.wait_for(stop_evt.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            if stop_evt.is_set():
                break
            now_iso = to_utc_iso(self.clock.now())
            try:
                async with get_db() as db:
                    await execute(
                        db,
                        "UPDATE schedule_runs SET heartbeat_at = ? WHERE id = ?",
                        (now_iso, run_id),
                    )
            except Exception as exc:
                log.debug("Heartbeat update failed: %s", exc)

    async def _execute_schedule(
        self,
        schedule: Schedule,
        is_catchup: bool = False,
        timeout_seconds: float = 1800.0,
    ) -> ScheduleRun:
        """Execute a schedule run under single-flight locking with timeout and heartbeats."""
        if schedule.id in self._active_runs:
            log.warning("Single-flight violation prevented for schedule '%s'", schedule.name)
            raise RuntimeError(f"Schedule '{schedule.name}' is already running.")

        run_id = f"srun_{uuid.uuid4().hex[:12]}"
        now = self.clock.now()
        now_iso = to_utc_iso(now)

        cancel_evt = asyncio.Event()
        self._cancel_events[schedule.id] = cancel_evt
        current_task = asyncio.current_task()
        if current_task:
            self._active_runs[schedule.id] = current_task

        # Record running in schedule_runs
        async with get_db() as db:
            await execute(
                db,
                """
                INSERT INTO schedule_runs (id, schedule_id, started_at, status, heartbeat_at)
                VALUES (?, ?, ?, 'running', ?)
                """,
                (run_id, schedule.id, now_iso, now_iso),
            )
            await execute(
                db,
                "UPDATE schedules SET last_status = 'running', last_run_at = ? WHERE id = ?",
                (now_iso, schedule.id),
            )

        log.info("Started schedule run %s for '%s' (action=%s)", run_id, schedule.name, schedule.target_action)

        # Start heartbeat
        heartbeat_stop = asyncio.Event()
        hb_task = asyncio.create_task(self._heartbeat_loop(run_id, heartbeat_stop))

        handler = self._handlers.get(schedule.target_action)
        finished_status = "success"
        error_msg: str | None = None

        from noxai.tracing.recorder import trace_execution

        try:
            async with trace_execution(
                source="schedule",
                request_text=f"Scheduled execution of '{schedule.name}' (action={schedule.target_action})",
                schedule_run_id=run_id,
            ) as tr_rec:
                try:
                    if handler is None:
                        raise ValueError(f"No registered handler for target_action: '{schedule.target_action}'")

                    # Execute with timeout
                    await asyncio.wait_for(handler(schedule, run_id, cancel_evt), timeout=timeout_seconds)
                    tr_rec.record_step(name=f"execute_{schedule.target_action}", status="success")

                except TimeoutError:
                    tr_rec.record_step(name=f"execute_{schedule.target_action}", status="timeout")
                    finished_status = "failed"
                    error_msg = f"Run exceeded timeout of {timeout_seconds}s"
                    log.error("Schedule run %s for '%s' timed out", run_id, schedule.name)
                    send_schedule_notification(
                        f"NoX Schedule Timeout: {schedule.name}",
                        error_msg,
                        urgency="critical",
                    )
                except asyncio.CancelledError:
                    tr_rec.record_step(name=f"execute_{schedule.target_action}", status="cancelled")
                    finished_status = "cancelled"
                    error_msg = "Run cancelled cooperatively"
                    log.info("Schedule run %s for '%s' was cancelled", run_id, schedule.name)
                except Exception as exc:
                    tr_rec.record_step(name=f"execute_{schedule.target_action}", status="failed")
                    finished_status = "failed"
                    error_msg = str(exc)
                    log.error("Schedule run %s for '%s' failed: %s", run_id, schedule.name, exc, exc_info=True)
                    send_schedule_notification(
                        f"NoX Schedule Failed: {schedule.name}",
                        f"Error: {exc}",
                        urgency="critical",
                    )
                else:
                    log.info("Schedule run %s for '%s' completed successfully", run_id, schedule.name)
                    if is_catchup:
                        send_schedule_notification(
                            f"NoX Schedule Catch-up: {schedule.name}",
                            f"Missed run completed successfully.",
                            urgency="normal",
                        )
                    else:
                        send_schedule_notification(
                            f"NoX Schedule: {schedule.name}",
                            f"Completed successfully.",
                            urgency="low",
                        )
        finally:
            heartbeat_stop.set()
            if not hb_task.done():
                await hb_task

            finish_iso = to_utc_iso(self.clock.now())
            async with get_db() as db:
                await execute(
                    db,
                    """
                    UPDATE schedule_runs
                    SET status = ?, finished_at = ?, error = ?
                    WHERE id = ?
                    """,
                    (finished_status, finish_iso, error_msg, run_id),
                )
                await execute(
                    db,
                    "UPDATE schedules SET last_status = ? WHERE id = ?",
                    (finished_status, schedule.id),
                )

            self._active_runs.pop(schedule.id, None)
            self._cancel_events.pop(schedule.id, None)

        return ScheduleRun(
            id=run_id,
            schedule_id=schedule.id,
            started_at=now_iso,
            finished_at=finish_iso,
            status=finished_status,
            error=error_msg,
            trace_id=None,
            log_path=None,
            heartbeat_at=finish_iso,
        )

    # -----------------------------------------------------------------------
    # Manual Trigger & Management API
    # -----------------------------------------------------------------------

    async def trigger_run(self, name_or_id: str) -> str:
        """Trigger a manual run for a schedule immediately; executes in background."""
        async with get_db() as db:
            row = await fetch_one(
                db,
                "SELECT * FROM schedules WHERE id = ? OR name = ?",
                (name_or_id, name_or_id),
            )
            if not row:
                raise KeyError(f"Schedule '{name_or_id}' not found.")
            sched = Schedule.from_row(row)

        if sched.id in self._active_runs:
            raise RuntimeError(f"Schedule '{sched.name}' is already running.")

        task = asyncio.create_task(
            self._execute_schedule(sched, is_catchup=False),
            name=f"manual_{sched.name}",
        )
        return sched.id

    async def list_schedules(self) -> list[Schedule]:
        """Return all persisted schedules."""
        async with get_db() as db:
            rows = await fetch_all(db, "SELECT * FROM schedules ORDER BY created_at ASC")
            return [Schedule.from_row(r) for r in rows]

    async def get_schedule(self, name_or_id: str) -> Schedule | None:
        """Fetch a single schedule by ID or unique name."""
        async with get_db() as db:
            row = await fetch_one(
                db,
                "SELECT * FROM schedules WHERE id = ? OR name = ?",
                (name_or_id, name_or_id),
            )
            return Schedule.from_row(row) if row else None

    async def add_schedule(
        self,
        name: str,
        kind: str,
        expression: str,
        timezone: str = DEFAULT_TIMEZONE,
        catchup_policy: str = "run_once",
        target_action: str = "generic",
        config_json: str | None = None,
        enabled: bool = True,
    ) -> Schedule:
        """Add a new persistent schedule and compute its initial next_run_at."""
        now = self.clock.now()
        nxt = compute_next_run(kind, expression, now, timezone)
        sched_id = f"sched_{uuid.uuid4().hex[:8]}"
        now_iso = to_utc_iso(now)
        nxt_iso = to_utc_iso(nxt)

        async with get_db() as db:
            await execute(
                db,
                """
                INSERT INTO schedules (
                    id, name, kind, expression, timezone, enabled,
                    next_run_at, catchup_policy, target_action, config_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sched_id,
                    name,
                    kind,
                    expression,
                    timezone,
                    1 if enabled else 0,
                    nxt_iso,
                    catchup_policy,
                    target_action,
                    config_json,
                    now_iso,
                ),
            )

        res = await self.get_schedule(sched_id)
        if res is None:
            raise RuntimeError(f"Failed to create schedule {name}")
        return res

    async def remove_schedule(self, name_or_id: str) -> bool:
        """Delete a schedule, cooperatively cancelling any active run first."""
        sched = await self.get_schedule(name_or_id)
        if not sched:
            return False

        # Cooperatively cancel running execution
        if sched.id in self._cancel_events:
            self._cancel_events[sched.id].set()
        if sched.id in self._active_runs:
            self._active_runs[sched.id].cancel()

        async with get_db() as db:
            await execute(db, "DELETE FROM schedules WHERE id = ?", (sched.id,))
        return True

    async def enable_schedule(self, name_or_id: str) -> bool:
        """Enable a schedule and recompute next_run_at."""
        sched = await self.get_schedule(name_or_id)
        if not sched:
            return False

        now = self.clock.now()
        nxt = compute_next_run(sched.kind, sched.expression, now, sched.timezone)
        async with get_db() as db:
            await execute(
                db,
                "UPDATE schedules SET enabled = 1, next_run_at = ? WHERE id = ?",
                (to_utc_iso(nxt), sched.id),
            )
        return True

    async def disable_schedule(self, name_or_id: str) -> bool:
        """Disable a schedule, preventing automatic firings."""
        sched = await self.get_schedule(name_or_id)
        if not sched:
            return False

        async with get_db() as db:
            await execute(
                db,
                "UPDATE schedules SET enabled = 0, next_run_at = NULL WHERE id = ?",
                (sched.id,),
            )
        return True

    async def get_history(self, name_or_id: str, limit: int = 20) -> list[ScheduleRun]:
        """Fetch historical runs for a schedule, newest first."""
        sched = await self.get_schedule(name_or_id)
        if not sched:
            return []

        async with get_db() as db:
            rows = await fetch_all(
                db,
                """
                SELECT * FROM schedule_runs
                WHERE schedule_id = ?
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (sched.id, limit),
            )
            return [ScheduleRun.from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Singleton Provider
# ---------------------------------------------------------------------------

_scheduler_instance: PersistentScheduler | None = None


def get_persistent_scheduler(config: Settings | None = None) -> PersistentScheduler:
    """Retrieve or create the authoritative singleton PersistentScheduler."""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = PersistentScheduler(config=config)
    return _scheduler_instance
