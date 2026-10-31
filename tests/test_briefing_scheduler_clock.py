"""Tests for BriefingScheduler: 04:00/18:00 Asia/Kolkata slots, uniqueness, and boot catchup."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import aiosqlite
import pytest

from noxai.briefing.scheduler import BriefingScheduler
from noxai.config import Settings, get_config
from noxai.db import fetch_all, init_db
from noxai.tasks.supervisor import TaskSupervisor

KOLKATA = ZoneInfo("Asia/Kolkata")


@pytest.fixture
async def test_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[aiosqlite.Connection]:
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
    """Test 04:00 (morning) and 18:00 (evening) triggers via fake clock."""
    supervisor = TaskSupervisor(config=test_config)
    # Start at 2026-10-31 03:59:00 Asia/Kolkata
    clock = FakeClock(datetime(2026, 10, 31, 3, 59, 0, tzinfo=KOLKATA))
    scheduler = BriefingScheduler(config=test_config, supervisor=supervisor, clock=clock)

    # 1. 03:59 -> No slots due
    t1 = await scheduler.tick(db=test_db)
    assert len(t1) == 0

    # 2. Advance to 04:00 -> Morning slot triggers
    clock.set(datetime(2026, 10, 31, 4, 0, 0, tzinfo=KOLKATA))
    t2 = await scheduler.tick(db=test_db)
    assert t2 == ["2026-10-31:morning"]

    # 3. 04:01 -> Does not re-trigger
    clock.set(datetime(2026, 10, 31, 4, 1, 0, tzinfo=KOLKATA))
    t3 = await scheduler.tick(db=test_db)
    assert len(t3) == 0

    # 4. Advance to 17:59 -> No slots due
    clock.set(datetime(2026, 10, 31, 17, 59, 0, tzinfo=KOLKATA))
    t4 = await scheduler.tick(db=test_db)
    assert len(t4) == 0

    # 5. Advance to 18:00 -> Evening slot triggers
    clock.set(datetime(2026, 10, 31, 18, 0, 0, tzinfo=KOLKATA))
    t5 = await scheduler.tick(db=test_db)
    assert t5 == ["2026-10-31:evening"]

    # 6. Verify tasks were submitted to DB
    tasks = await fetch_all(test_db, "SELECT title, kind, status FROM tasks WHERE kind = 'briefing' ORDER BY created_at ASC")
    assert len(tasks) == 2
    assert "Morning" in tasks[0]["title"]
    assert "Evening" in tasks[1]["title"]


@pytest.mark.asyncio
async def test_briefing_scheduler_boot_catchup(
    test_db: aiosqlite.Connection, test_config: Settings
):
    """Test catchup on boot if daemon starts after scheduled time."""
    supervisor = TaskSupervisor(config=test_config)
    # Daemon boots at 10:30 Asia/Kolkata (missed 04:00 slot)
    clock = FakeClock(datetime(2026, 10, 31, 10, 30, 0, tzinfo=KOLKATA))
    scheduler = BriefingScheduler(config=test_config, supervisor=supervisor, clock=clock)

    # Boot catchup should trigger the missed morning slot
    caught = await scheduler.catchup_on_boot(db=test_db)
    assert caught == ["2026-10-31:morning"]

    # Second boot catchup immediately after -> 0 new tasks (uniqueness check)
    caught_again = await scheduler.catchup_on_boot(db=test_db)
    assert len(caught_again) == 0

    # Verify task in DB
    tasks = await fetch_all(test_db, "SELECT title, kind FROM tasks WHERE kind = 'briefing'")
    assert len(tasks) == 1
    assert "Morning" in tasks[0]["title"]
