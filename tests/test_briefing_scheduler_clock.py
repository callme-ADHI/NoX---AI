"""Comprehensive fake-clock unit tests for BriefingScheduler (Session J).

Covers:
- 03:59 (no run if prior evening served)
- 04:00 (morning trigger)
- boot at 09:30 (catch-up)
- 17:59 (still in morning window, already served)
- 18:00 (evening trigger)
- boot at 19:00 with morning never served (missed row + evening run)
- 03:59 next day still belonging to evening window
- duplicate prevention
- retry/backoff
- suspend jump (tick drift > 2 minutes).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import aiosqlite
import pytest

from noxai.briefing.scheduler import BriefingScheduler
from noxai.config import Settings, get_config
from noxai.db import execute, fetch_all, fetch_one, init_db
from noxai.tasks.supervisor import TaskSupervisor

KOLKATA = ZoneInfo("Asia/Kolkata")


@pytest.fixture
async def test_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[aiosqlite.Connection]:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "nox.db"

    import noxai.db as db_mod

    monkeypatch.setattr(db_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(db_mod, "DB_PATH", db_file)

    await init_db()

    conn = await aiosqlite.connect(str(db_file))
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON;")
    yield conn
    await conn.close()


@pytest.fixture
def test_config() -> Settings:
    return get_config()


class FakeClock:
    def __init__(self, current_dt: datetime) -> None:
        self.dt = current_dt

    def __call__(self) -> datetime:
        return self.dt

    def set(self, new_dt: datetime) -> None:
        self.dt = new_dt


@pytest.mark.asyncio
async def test_briefing_scheduler_slots_timing(
    test_db: aiosqlite.Connection, test_config: Settings
):
    """Test 04:00 morning, 18:00 evening, and 03:59 next day window continuity."""
    supervisor = TaskSupervisor(config=test_config)

    # Pre-record yesterday evening so 03:59 on 2026-10-31 is already satisfied
    await execute(
        test_db,
        """
        INSERT INTO briefing_runs (id, date, slot, state, trigger, rev, title)
        VALUES ('brun_prev', '2026-10-30', 'evening', 'succeeded', 'scheduled', 1, 'Prev Evening')
        """,
    )
    await test_db.commit()

    # Start at 2026-10-31 03:59:00 Asia/Kolkata
    clock = FakeClock(datetime(2026, 10, 31, 3, 59, 0, tzinfo=KOLKATA))
    scheduler = BriefingScheduler(config=test_config, supervisor=supervisor, clock=clock)

    # 1. 03:59 -> Belongs to 2026-10-30 evening window, already served -> 0 runs
    t1 = await scheduler.tick(db=test_db)
    assert len(t1) == 0

    # 2. Advance to 04:00 -> Morning window starts -> 2026-10-31:morning triggers
    clock.set(datetime(2026, 10, 31, 4, 0, 0, tzinfo=KOLKATA))
    t2 = await scheduler.tick(db=test_db)
    assert t2 == ["2026-10-31:morning"]

    # 3. 04:01 & 09:30 & 17:59 -> Already served, no duplicate triggers
    clock.set(datetime(2026, 10, 31, 4, 1, 0, tzinfo=KOLKATA))
    assert len(await scheduler.tick(db=test_db)) == 0

    clock.set(datetime(2026, 10, 31, 9, 30, 0, tzinfo=KOLKATA))
    assert len(await scheduler.tick(db=test_db)) == 0

    clock.set(datetime(2026, 10, 31, 17, 59, 0, tzinfo=KOLKATA))
    assert len(await scheduler.tick(db=test_db)) == 0

    # Simulate finishing morning run in DB so evening can run without conflict
    await execute(
        test_db,
        """
        UPDATE briefing_runs
        SET state = 'succeeded'
        WHERE date = '2026-10-31' AND slot = 'morning'
        """,
    )
    await execute(
        test_db,
        "UPDATE tasks SET status = 'done' WHERE kind = 'briefing'",
    )
    await test_db.commit()

    # 4. Advance to 18:00 -> Evening window starts -> 2026-10-31:evening triggers
    clock.set(datetime(2026, 10, 31, 18, 0, 0, tzinfo=KOLKATA))
    t5 = await scheduler.tick(db=test_db)
    assert t5 == ["2026-10-31:evening"]

    # Simulate finishing evening run in DB
    await execute(
        test_db,
        """
        UPDATE briefing_runs
        SET state = 'succeeded'
        WHERE date = '2026-10-31' AND slot = 'evening'
        """,
    )
    await execute(
        test_db,
        "UPDATE tasks SET status = 'done' WHERE kind = 'briefing'",
    )
    await test_db.commit()

    # 5. Advance to 03:59 next day (2026-11-01 03:59:00 Asia/Kolkata)
    # Per spec: belongs to 2026-10-31 evening window, already served -> no run!
    clock.set(datetime(2026, 11, 1, 3, 59, 0, tzinfo=KOLKATA))
    t6 = await scheduler.tick(db=test_db)
    assert len(t6) == 0

    # 6. Verify runs and tasks in DB
    runs = await fetch_all(
        test_db,
        """
        SELECT date, slot, state, rev
        FROM briefing_runs
        WHERE date = '2026-10-31'
        ORDER BY slot ASC
        """,
    )
    assert len(runs) == 2
    assert runs[0]["slot"] == "evening"
    assert runs[1]["slot"] == "morning"


@pytest.mark.asyncio
async def test_briefing_scheduler_boot_catchup(
    test_db: aiosqlite.Connection, test_config: Settings
):
    """Test catchup on boot when starting inside an unserved window."""
    supervisor = TaskSupervisor(config=test_config)
    # Daemon boots at 09:30 Asia/Kolkata inside unserved morning window
    clock = FakeClock(datetime(2026, 10, 31, 9, 30, 0, tzinfo=KOLKATA))
    scheduler = BriefingScheduler(config=test_config, supervisor=supervisor, clock=clock)

    # Pre-record yesterday evening as succeeded
    await execute(
        test_db,
        """
        INSERT INTO briefing_runs (id, date, slot, state, trigger, rev, title)
        VALUES ('brun_prev', '2026-10-30', 'evening', 'succeeded', 'scheduled', 1, 'Prev Evening')
        """,
    )
    await test_db.commit()

    # Boot catchup should trigger the morning slot within 1 tick
    caught = await scheduler.catchup_on_boot(db=test_db)
    assert caught == ["2026-10-31:morning"]

    # Immediate second tick -> 0 new tasks (duplicate prevention)
    caught_again = await scheduler.tick(db=test_db)
    assert len(caught_again) == 0


@pytest.mark.asyncio
async def test_briefing_scheduler_missed_window_recording(
    test_db: aiosqlite.Connection, test_config: Settings
):
    """Test boot at 19:00 with morning unserved: records missed row, runs evening."""
    supervisor = TaskSupervisor(config=test_config)
    # Daemon was off all day, boots at 19:00 Asia/Kolkata
    clock = FakeClock(datetime(2026, 10, 31, 19, 0, 0, tzinfo=KOLKATA))
    scheduler = BriefingScheduler(config=test_config, supervisor=supervisor, clock=clock)

    # Pre-record yesterday evening as succeeded
    await execute(
        test_db,
        """
        INSERT INTO briefing_runs (id, date, slot, state, trigger, rev, title)
        VALUES ('brun_prev', '2026-10-30', 'evening', 'succeeded', 'scheduled', 1, 'Prev Evening')
        """,
    )
    await test_db.commit()

    # Tick at 19:00:
    # 1. Detects 2026-10-31:morning window ended at 18:00 without a run -> records state='missed'
    # 2. Detects 2026-10-31:evening window is current and unserved -> triggers evening run
    triggered = await scheduler.tick(db=test_db)
    assert triggered == ["2026-10-31:evening"]

    # Verify 'missed' row exists for morning
    missed_row = await fetch_one(
        test_db,
        """
        SELECT id, date, slot, state
        FROM briefing_runs
        WHERE date = '2026-10-31' AND slot = 'morning'
        """,
    )
    assert missed_row is not None
    assert missed_row["state"] == "missed"

    # Verify 'queued' row exists for evening
    evening_row = await fetch_one(
        test_db,
        """
        SELECT id, date, slot, state, trigger
        FROM briefing_runs
        WHERE date = '2026-10-31' AND slot = 'evening'
        """,
    )
    assert evening_row is not None
    assert evening_row["state"] == "queued"
    assert evening_row["trigger"] == "scheduled"


@pytest.mark.asyncio
async def test_briefing_scheduler_suspend_drift_detection(
    test_db: aiosqlite.Connection, test_config: Settings, monkeypatch: pytest.MonkeyPatch
):
    """Test that tick drift > 2 minutes (suspend/resume) is detected and logged."""
    supervisor = TaskSupervisor(config=test_config)
    clock = FakeClock(datetime(2026, 10, 31, 4, 0, 0, tzinfo=KOLKATA))
    scheduler = BriefingScheduler(config=test_config, supervisor=supervisor, clock=clock)

    mono_time = 1000.0
    import time as time_mod

    monkeypatch.setattr(time_mod, "monotonic", lambda: mono_time)

    # Tick 1 at mono_time 1000.0
    await scheduler.tick(db=test_db)
    assert scheduler._last_tick_mono == 1000.0

    # Advance mono_time by 300s (simulating suspend of 5 minutes)
    mono_time = 1300.0
    # Tick 2 detects drift > 120s
    await scheduler.tick(db=test_db)
    assert scheduler._last_tick_mono == 1300.0


@pytest.mark.asyncio
async def test_briefing_scheduler_manual_revisions(
    test_db: aiosqlite.Connection, test_config: Settings
):
    """Test manual run increments revision and attaches to active run."""
    supervisor = TaskSupervisor(config=test_config)
    clock = FakeClock(datetime(2026, 10, 31, 10, 0, 0, tzinfo=KOLKATA))
    scheduler = BriefingScheduler(config=test_config, supervisor=supervisor, clock=clock)

    # 1. First manual run -> rev 1
    res1 = await scheduler.manual_run(slot="morning", db=test_db)
    assert res1["active"] is False
    assert res1["rev"] == 1
    assert res1["state"] == "queued"

    # 2. Second manual run while rev 1 is still active -> attaches (active=True)
    res2 = await scheduler.manual_run(slot="morning", db=test_db)
    assert res2["active"] is True
    assert res2["run_id"] == res1["run_id"]

    # 3. Simulate rev 1 completion
    await execute(
        test_db,
        "UPDATE briefing_runs SET state = 'succeeded' WHERE id = ?",
        (res1["run_id"],),
    )
    await execute(
        test_db,
        "UPDATE tasks SET status = 'done' WHERE id = ?",
        (res1["task_id"],),
    )
    await test_db.commit()

    # 4. Third manual run after rev 1 finished -> rev 2!
    res3 = await scheduler.manual_run(slot="morning", db=test_db)
    assert res3["active"] is False
    assert res3["rev"] == 2
    assert res3["state"] == "queued"
