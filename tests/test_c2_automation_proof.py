"""Automated proof for C-2: Unattended briefing automation proof (04:00 & 18:00 with catch-up).

Requirements verified:
1. Morning 04:00 slot triggers with trigger='scheduled'.
2. Catch-up inside unserved morning window (e.g. boot at 09:30).
3. Evening 18:00 slot triggers with trigger='scheduled'.
4. Boot past window (e.g. 19:00 with morning never served) records state='missed' for morning and queues evening.
5. A run producing 0 items must be marked state='failed'.
6. No reconciler marks runs 'succeeded' when task is gone; orphaned runs are interrupted and resumed under the same run_id.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import aiosqlite
import pytest

from noxai.briefing.engine import BriefingPipeline
from noxai.briefing.scheduler import BriefingScheduler
from noxai.config import get_config
from noxai.db import _configure_async_connection, execute, fetch_all, fetch_one, get_db, run_migrations
from noxai.tasks.supervisor import TaskSupervisor

KOLKATA = ZoneInfo("Asia/Kolkata")


class FakeClock:
    def __init__(self, dt: datetime) -> None:
        self.dt = dt

    def __call__(self) -> datetime:
        return self.dt

    def set(self, dt: datetime) -> None:
        self.dt = dt


@pytest.fixture
async def c2_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_file = tmp_path / "nox_c2.db"
    monkeypatch.setattr("noxai.db.DB_PATH", db_file)
    monkeypatch.setattr("noxai.db.DATA_DIR", tmp_path)
    monkeypatch.setenv("NOX_DATABASE_PATH", str(db_file))
    await run_migrations()
    return db_file


@pytest.mark.asyncio
async def test_c2_unattended_automation_and_state_proof(c2_db: Path):
    """Complete C-2 proof: 04:00, 18:00, catchup, missed, 0-items failure, and orphaned resume."""
    cfg = get_config()
    sup = TaskSupervisor(config=cfg)

    # Pre-record prior day evening so 03:59 has no trigger
    async with get_db() as db:
        await execute(
            db,
            """
            INSERT INTO briefing_runs (id, date, slot, state, trigger, rev, title)
            VALUES ('brun_prev', '2026-11-19', 'evening', 'succeeded', 'scheduled', 1, 'Prev')
            """,
        )
        await db.commit()

    # 1. 04:00 Morning slot trigger
    clock = FakeClock(datetime(2026, 11, 20, 4, 0, 0, tzinfo=KOLKATA))
    sched = BriefingScheduler(config=cfg, supervisor=sup, clock=clock)

    async with get_db() as db:
        triggered_morning = await sched.tick(db=db)
    assert triggered_morning == ["2026-11-20:morning"]

    async with get_db() as db:
        row = await fetch_one(db, "SELECT state, trigger FROM briefing_runs WHERE date='2026-11-20' AND slot='morning'")
        assert row is not None
        assert row["state"] == "queued"
        assert row["trigger"] == "scheduled"
        # Simulate completion
        await execute(db, "UPDATE briefing_runs SET state='succeeded' WHERE date='2026-11-20' AND slot='morning'")
        await execute(db, "UPDATE tasks SET status='done' WHERE kind='briefing'")
        await db.commit()

    # 2. 18:00 Evening slot trigger
    clock.set(datetime(2026, 11, 20, 18, 0, 0, tzinfo=KOLKATA))
    async with get_db() as db:
        triggered_evening = await sched.tick(db=db)
    assert triggered_evening == ["2026-11-20:evening"]

    async with get_db() as db:
        row = await fetch_one(db, "SELECT state, trigger FROM briefing_runs WHERE date='2026-11-20' AND slot='evening'")
        assert row is not None
        assert row["state"] == "queued"
        assert row["trigger"] == "scheduled"
        await execute(db, "UPDATE briefing_runs SET state='succeeded' WHERE date='2026-11-20' AND slot='evening'")
        await execute(db, "UPDATE tasks SET status='done' WHERE kind='briefing'")
        await db.commit()

    # 3. Catch-up inside unserved morning window (boot at 09:30 on 2026-11-21)
    clock.set(datetime(2026, 11, 21, 9, 30, 0, tzinfo=KOLKATA))
    async with get_db() as db:
        caught = await sched.catchup_on_boot(db=db)
    assert caught == ["2026-11-21:morning"]

    async with get_db() as db:
        row = await fetch_one(db, "SELECT state, trigger FROM briefing_runs WHERE date='2026-11-21' AND slot='morning'")
        assert row is not None
        assert row["state"] == "queued"
        assert row["trigger"] == "scheduled"
        await execute(db, "UPDATE briefing_runs SET state='succeeded' WHERE date='2026-11-21' AND slot='morning'")
        await execute(db, "UPDATE tasks SET status='done' WHERE kind='briefing'")
        await db.commit()

    # 4. Missed window: boot at 19:00 on 2026-11-22 when morning was never served
    clock.set(datetime(2026, 11, 22, 19, 0, 0, tzinfo=KOLKATA))
    async with get_db() as db:
        triggered_1900 = await sched.tick(db=db)
    assert triggered_1900 == ["2026-11-22:evening"]

    async with get_db() as db:
        missed = await fetch_one(db, "SELECT state FROM briefing_runs WHERE date='2026-11-22' AND slot='morning'")
        assert missed is not None
        assert missed["state"] == "missed"

    # 5. 0 items gate: pipeline producing 0 items must mark state='failed'
    pipeline = BriefingPipeline(config=cfg)
    async def mock_zero_analyst(run_id: str, slot: str, today_str: str) -> dict:
        return {"items": [], "top_3": [], "tldr": "Empty", "sources_count": 0, "total_items": 0}

    with patch.object(pipeline, "_run_analyst", side_effect=mock_zero_analyst):
        with patch.object(pipeline, "_run_composer", return_value=c2_db.parent / "test.md"):
            with patch.object(pipeline, "_send_notification", new_callable=AsyncMock):
                zero_run_id = await pipeline.execute_run(slot="morning", date_str="2026-11-23")

    async with get_db() as db:
        zero_row = await fetch_one(db, "SELECT state, failure_reason FROM briefing_runs WHERE id = ?", (zero_run_id,))
        assert zero_row is not None
        assert zero_row["state"] == "failed"
        assert "empty_run: pipeline produced 0 items" in zero_row["failure_reason"]

    # 6. Orphaned run interrupted and resumed under SAME run_id
    orphan_run_id = "brun_c2_orphan_test"
    async with get_db() as db:
        await execute(
            db,
            """
            INSERT INTO briefing_runs (id, date, slot, state, trigger, rev, title)
            VALUES (?, '2026-11-24', 'morning', 'running', 'scheduled', 1, 'Orphaned Run')
            """,
            (orphan_run_id,),
        )
        await execute(
            db,
            """
            INSERT INTO briefing_topic_runs (run_id, topic_id, state, items_staged)
            VALUES (?, 'topic_ai', 'done', 6), (?, 'topic_systems', 'queued', 0)
            """,
            (orphan_run_id, orphan_run_id),
        )
        await db.commit()

        # Execute repair_runs: MUST NOT mark succeeded; marks interrupted and requeues under same run_id
        repaired = await sched.repair_runs(db=db, requeue=True)
        assert len(repaired) == 1
        assert repaired[0]["run_id"] == orphan_run_id

        # Verify state in DB is 'queued' under same run_id
        rep_row = await fetch_one(db, "SELECT state FROM briefing_runs WHERE id = ?", (orphan_run_id,))
        assert rep_row is not None
        assert rep_row["state"] == "queued"

        # Verify completed topic run remains 'done'
        topic_ai = await fetch_one(db, "SELECT state FROM briefing_topic_runs WHERE run_id = ? AND topic_id = 'topic_ai'", (orphan_run_id,))
        assert topic_ai is not None
        assert topic_ai["state"] == "done"
