"""Automated proof for Gate A: One authoritative scheduler per job type (Session N Gate A).

Verifies that when both BriefingScheduler and PersistentScheduler are running or ticking,
morning slot (04:00) and evening slot (18:00) can never fire twice,
resulting in exactly ONE morning row and ONE evening row in briefing_runs.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from noxai.briefing.scheduler import BriefingScheduler
from noxai.config import get_config
from noxai.db import execute, fetch_all, get_db, init_db
from noxai.scheduler.persistent import FakeClock, PersistentScheduler, Schedule
from noxai.tasks.supervisor import TaskSupervisor


@pytest.mark.asyncio
async def test_gate_a_one_scheduler_authority_briefings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Prove that morning and evening briefing slots fire exactly once when both schedulers run."""
    monkeypatch.setenv("NOX_DATABASE_PATH", str(tmp_path / "test_gate_a.db"))
    monkeypatch.setenv("NOX_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("noxai.db.DATA_DIR", tmp_path)
    await init_db()

    cfg = get_config()
    tz = ZoneInfo("Asia/Kolkata")
    supervisor = TaskSupervisor(config=cfg)

    # 1. Setup clocks and schedulers
    clock_dt = datetime(2026, 11, 21, 4, 0, 0, tzinfo=tz)
    fake_clock = FakeClock(clock_dt)

    briefing_sched = BriefingScheduler(
        config=cfg, supervisor=supervisor, clock=lambda: fake_clock.now()
    )
    persistent_sched = PersistentScheduler(config=cfg, clock=fake_clock)

    async with get_db() as db:
        # Pre-satisfy yesterday evening so window check is clean
        await execute(
            db,
            """
            INSERT INTO briefing_runs (id, date, slot, state, trigger, rev, title)
            VALUES (
                'brun_prev', '2026-11-20', 'evening', 'succeeded', 'scheduled', 1, 'Prev Evening'
            )
            """,
        )

        # A. Morning slot at 04:00:00 IST
        # 1. BriefingScheduler tick
        b_morning_triggers = await briefing_sched.tick(db=db, trigger="scheduled")
        assert b_morning_triggers == ["2026-11-21:morning"]

        # Mark run and task as succeeded
        await execute(
            db,
            "UPDATE briefing_runs SET state = 'succeeded' "
            "WHERE date = '2026-11-21' AND slot = 'morning'",
        )
        await execute(db, "UPDATE tasks SET status = 'done' WHERE kind = 'briefing'")

    # 2. PersistentScheduler executes its morning schedule (yields to BriefingScheduler)
    sched_morning = Schedule(
        id="sched_briefing_morning",
        name="briefing_morning",
        kind="cron",
        expression="0 4 * * *",
        timezone="Asia/Kolkata",
        enabled=True,
        last_run_at=None,
        next_run_at=None,
        last_status=None,
        catchup_policy="run_once",
        target_action="briefing_morning",
        config_json=None,
        created_at="2026-11-21T00:00:00Z",
    )
    srun_morning = await persistent_sched._execute_schedule(sched_morning)
    assert srun_morning.status == "success"

    async with get_db() as db:
        # 3. Check briefing_runs table after morning slot: exactly 1 run from BriefingScheduler
        morning_rows = await fetch_all(
            db,
            "SELECT id, date, slot, state FROM briefing_runs "
            "WHERE date = '2026-11-21' AND slot = 'morning'",
        )
        assert len(morning_rows) == 1

        # B. Evening slot at 18:00:00 IST
        clock_dt_evening = datetime(2026, 11, 21, 18, 0, 0, tzinfo=tz)
        fake_clock.set_time(clock_dt_evening)

        # 1. BriefingScheduler tick
        b_evening_triggers = await briefing_sched.tick(db=db, trigger="scheduled")
        assert b_evening_triggers == ["2026-11-21:evening"]

        # Mark evening run as succeeded
        await execute(
            db,
            "UPDATE briefing_runs SET state = 'succeeded' "
            "WHERE date = '2026-11-21' AND slot = 'evening'",
        )

    # 2. PersistentScheduler executes its evening schedule (yields to BriefingScheduler)
    sched_evening = Schedule(
        id="sched_briefing_evening",
        name="briefing_evening",
        kind="cron",
        expression="0 18 * * *",
        timezone="Asia/Kolkata",
        enabled=True,
        last_run_at=None,
        next_run_at=None,
        last_status=None,
        catchup_policy="run_once",
        target_action="briefing_evening",
        config_json=None,
        created_at="2026-11-21T00:00:00Z",
    )
    srun_evening = await persistent_sched._execute_schedule(sched_evening)
    assert srun_evening.status == "success"

    # Final assertion: exactly ONE morning row and exactly ONE evening row for 2026-11-21
    async with get_db() as db:
        runs_today = await fetch_all(
            db,
            "SELECT id, date, slot, state, trigger, title FROM briefing_runs "
            "WHERE date = '2026-11-21' ORDER BY slot ASC",
        )
        assert len(runs_today) == 2
        slots = [r["slot"] for r in runs_today]
        assert sorted(slots) == ["evening", "morning"]
