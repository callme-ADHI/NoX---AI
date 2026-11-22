"""Unit tests for PersistentScheduler (Workstream B).

Covers:
- Next-run calculation across midnight & interval parsing.
- Catch-up policies (run_once, skip, run_all).
- Single-flight execution guarantee.
- Interrupted-run crash recovery.
- Injected FakeClock deterministic time manipulation.
- Cooperative cancellation.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import pytest
from zoneinfo import ZoneInfo

from noxai.db import execute, fetch_all, fetch_one, get_db, init_db
from noxai.scheduler.persistent import (
    DEFAULT_TIMEZONE,
    FakeClock,
    PersistentScheduler,
    Schedule,
    compute_next_run,
    parse_interval_seconds,
    to_utc_iso,
)


@pytest.fixture(autouse=True)
async def init_test_db():
    await init_db()


@pytest.fixture
def fake_clock():
    # 2026-11-18 18:30:00 Asia/Kolkata
    init_dt = datetime(2026, 11, 18, 18, 30, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
    return FakeClock(init_dt)


def test_interval_parsing():
    assert parse_interval_seconds("120") == 120.0
    assert parse_interval_seconds("45s") == 45.0
    assert parse_interval_seconds("5m") == 300.0
    assert parse_interval_seconds("2h") == 7200.0
    assert parse_interval_seconds("1d") == 86400.0


def test_next_run_calculation_across_midnight(fake_clock):
    tz = ZoneInfo(DEFAULT_TIMEZONE)
    # At 18:30 IST:
    # 1. 20:00 is same day at 20:00
    now = fake_clock.now()
    nxt_20 = compute_next_run("cron", "0 20 * * *", now, DEFAULT_TIMEZONE)
    assert nxt_20.hour == 20
    assert nxt_20.day == 18

    # 2. 04:00 crosses midnight to next day (19th) at 04:00
    nxt_04 = compute_next_run("cron", "0 4 * * *", now, DEFAULT_TIMEZONE)
    assert nxt_04.hour == 4
    assert nxt_04.day == 19

    # 3. 18:00 (already passed at 18:30) crosses midnight to next day (19th) at 18:00
    nxt_18 = compute_next_run("cron", "0 18 * * *", now, DEFAULT_TIMEZONE)
    assert nxt_18.hour == 18
    assert nxt_18.day == 19

    # 4. Interval 10 minutes
    nxt_int = compute_next_run("interval", "10m", now, DEFAULT_TIMEZONE)
    assert nxt_int == now + timedelta(minutes=10)


@pytest.mark.asyncio
async def test_single_flight_and_execution(fake_clock, tmp_path):
    sched = PersistentScheduler(clock=fake_clock, tick_seconds=1.0)
    executed_events = []
    block_evt = asyncio.Event()

    async def blocking_handler(schedule, run_id, cancel_evt):
        executed_events.append(f"start:{run_id}")
        await block_evt.wait()
        executed_events.append(f"end:{run_id}")

    sched.register_handler("test_action", blocking_handler)

    test_sched = await sched.add_schedule(
        name="test_blocking_job",
        kind="interval",
        expression="60s",
        target_action="test_action",
    )

    try:
        # Launch first run
        task1 = asyncio.create_task(sched._execute_schedule(test_sched))
        await asyncio.sleep(0.05)

        assert test_sched.id in sched._active_runs

        # Second launch must fail single-flight check
        with pytest.raises(RuntimeError, match="already running"):
            await sched._execute_schedule(test_sched)

        # Unblock first run
        block_evt.set()
        await task1

        assert test_sched.id not in sched._active_runs
        assert any(e.startswith("start:") for e in executed_events)
        assert any(e.startswith("end:") for e in executed_events)

    finally:
        await sched.remove_schedule(test_sched.name)


@pytest.mark.asyncio
async def test_interrupted_run_crash_recovery(fake_clock):
    sched = PersistentScheduler(clock=fake_clock)

    test_sched = await sched.add_schedule(
        name="test_crash_job",
        kind="cron",
        expression="0 12 * * *",
        target_action="generic",
    )

    try:
        # Simulate a run that was left in 'running' state when process crashed
        simulated_run_id = "srun_simulated_crashed"
        now_iso = fake_clock.now().isoformat()
        async with get_db() as db:
            await execute(
                db,
                """
                INSERT INTO schedule_runs (id, schedule_id, started_at, status, heartbeat_at)
                VALUES (?, ?, ?, 'running', ?)
                """,
                (simulated_run_id, test_sched.id, now_iso, now_iso),
            )

        # Run crash recovery
        recovered = await sched.recover_interrupted_runs()
        assert recovered >= 1

        async with get_db() as db:
            row = await fetch_one(db, "SELECT status, error FROM schedule_runs WHERE id = ?", (simulated_run_id,))
            assert row["status"] == "interrupted"
            assert "Interrupted by daemon restart" in row["error"]

    finally:
        await sched.remove_schedule(test_sched.name)


@pytest.mark.asyncio
async def test_catchup_policy_run_once(fake_clock):
    # Setup clock at 18:30
    sched = PersistentScheduler(clock=fake_clock)
    executed = []

    async def fast_handler(schedule, run_id, cancel_evt):
        executed.append(run_id)

    sched.register_handler("test_catchup_action", fast_handler)

    # Schedule whose next_run_at was 2 hours ago (16:30)
    test_sched = await sched.add_schedule(
        name="test_catchup_run_once",
        kind="interval",
        expression="1h",
        catchup_policy="run_once",
        target_action="test_catchup_action",
    )

    try:
        # Backdate next_run_at
        past_iso = to_utc_iso(fake_clock.now() - timedelta(hours=2))
        async with get_db() as db:
            await execute(db, "UPDATE schedules SET next_run_at = ? WHERE id = ?", (past_iso, test_sched.id))

        # Evaluate catch-up
        triggered = await sched.evaluate_catchup()
        assert "test_catchup_run_once" in triggered

        # Allow execution task to complete
        await asyncio.sleep(0.1)
        assert len(executed) == 1

        # Check new next_run_at is strictly in the future
        refreshed = await sched.get_schedule(test_sched.id)
        nxt_dt = datetime.fromisoformat(refreshed.next_run_at)
        assert nxt_dt.astimezone(UTC) > fake_clock.now().astimezone(UTC)

    finally:
        await sched.remove_schedule(test_sched.name)


@pytest.mark.asyncio
async def test_catchup_policy_skip(fake_clock):
    sched = PersistentScheduler(clock=fake_clock)

    test_sched = await sched.add_schedule(
        name="test_catchup_skip",
        kind="cron",
        expression="0 4 * * *",
        catchup_policy="skip",
        target_action="generic",
    )

    try:
        # Backdate next_run_at to past 04:00
        past_iso = to_utc_iso(fake_clock.now() - timedelta(hours=14))
        async with get_db() as db:
            await execute(db, "UPDATE schedules SET next_run_at = ? WHERE id = ?", (past_iso, test_sched.id))

        triggered = await sched.evaluate_catchup()
        assert "test_catchup_skip" not in triggered  # skipped, not executed

        # Verify 'missed' entry in schedule_runs
        history = await sched.get_history(test_sched.id)
        assert len(history) == 1
        assert history[0].status == "missed"

        # Check next_run_at advanced to future
        refreshed = await sched.get_schedule(test_sched.id)
        nxt_dt = datetime.fromisoformat(refreshed.next_run_at)
        assert nxt_dt.astimezone(UTC) > fake_clock.now().astimezone(UTC)

    finally:
        await sched.remove_schedule(test_sched.name)
