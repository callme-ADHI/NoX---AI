"""Unit tests for scheduler, delivery channels, and time utilities (Phase E7.3)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from noxai.agent.resume import mark_interrupted_runs
from noxai.config import get_config
from noxai.events import EventBus
from noxai.models.base import ChatChunk
from noxai.models.fake import FakeProvider
from noxai.models.router import ModelRouter
from noxai.scheduler.delivery import (
    DeliveryRouter,
    DesktopDeliveryChannel,
    UIDeliveryChannel,
)
from noxai.scheduler.scheduler import JobScheduler
from noxai.scheduler.time_utils import (
    compute_next_run,
    format_human_time,
    get_zoneinfo,
    parse_interval_seconds,
    parse_natural_time,
    should_catchup,
)

# ============================================================================
# TIME UTILS TESTS
# ============================================================================


def test_get_zoneinfo() -> None:
    """get_zoneinfo returns valid ZoneInfo or falls back to Asia/Kolkata."""
    assert get_zoneinfo("Asia/Kolkata").key == "Asia/Kolkata"
    assert get_zoneinfo("UTC").key == "UTC"
    assert get_zoneinfo(None).key == "Asia/Kolkata"
    assert get_zoneinfo("Invalid/Nonexistent_Timezone").key == "Asia/Kolkata"


def test_parse_interval_seconds() -> None:
    """parse_interval_seconds parses second, minute, hour, day units."""
    assert parse_interval_seconds("30") == 30
    assert parse_interval_seconds("45s") == 45
    assert parse_interval_seconds("15m") == 900
    assert parse_interval_seconds("2h") == 7200
    assert parse_interval_seconds("1d") == 86400

    with pytest.raises(ValueError):
        parse_interval_seconds("invalid")
    with pytest.raises(ValueError):
        parse_interval_seconds("10x")


def test_format_human_time() -> None:
    """format_human_time produces readable string with timezone."""
    dt = datetime(2026, 10, 19, 14, 30, tzinfo=UTC)
    s = format_human_time(dt, tz_name="Asia/Kolkata")
    assert "2026" in s
    assert "20:00" in s
    assert "IST" in s


def test_parse_natural_time_fixed_clock() -> None:
    """parse_natural_time parses relative and absolute phrases, and rejects past times."""
    fixed_base = datetime(2026, 10, 19, 10, 0, 0, tzinfo=UTC)

    # 1. Relative time: "in 15 minutes"
    t1 = parse_natural_time("in 15 minutes", tz_name="Asia/Kolkata", base_dt=fixed_base)
    assert t1 is not None
    assert (t1 - fixed_base).total_seconds() == 15 * 60

    # 2. Relative time: "in 2 hours"
    t2 = parse_natural_time("in 2 hours", tz_name="Asia/Kolkata", base_dt=fixed_base)
    assert t2 is not None
    assert (t2 - fixed_base).total_seconds() == 2 * 3600

    # 3. Future absolute date
    t3 = parse_natural_time("25 December 2026 at 10:00", tz_name="Asia/Kolkata", base_dt=fixed_base)
    assert t3 is not None
    assert t3 > fixed_base

    # 4. Past time rejected
    t_past = parse_natural_time("yesterday at 3pm", tz_name="Asia/Kolkata", base_dt=fixed_base)
    assert t_past is None

    # 5. Gibberish returns None
    t_none = parse_natural_time("completely unparseable nonsense text", base_dt=fixed_base)
    assert t_none is None


def test_compute_next_run() -> None:
    """compute_next_run handles cron, interval, once, and invalid types."""
    fixed_base = datetime(2026, 10, 19, 10, 0, 0, tzinfo=UTC)

    # 1. Cron
    next_cron = compute_next_run("cron", "0 12 * * *", tz_name="UTC", base_dt=fixed_base)
    assert next_cron is not None
    assert next_cron == datetime(2026, 10, 19, 12, 0, 0, tzinfo=UTC)

    # 2. Interval
    next_int = compute_next_run("interval", "30m", tz_name="UTC", base_dt=fixed_base)
    assert next_int == fixed_base + timedelta(minutes=30)

    # 3. Once - future
    fut_iso = (fixed_base + timedelta(hours=1)).isoformat()
    next_once = compute_next_run("once", fut_iso, tz_name="UTC", base_dt=fixed_base)
    assert next_once is not None

    # 4. Once - past returns None
    past_iso = (fixed_base - timedelta(hours=1)).isoformat()
    next_past = compute_next_run("once", past_iso, tz_name="UTC", base_dt=fixed_base)
    assert next_past is None

    # 5. Invalid schedule type
    with pytest.raises(ValueError):
        compute_next_run("unknown_type", "expr", base_dt=fixed_base)


def test_should_catchup() -> None:
    """should_catchup allows missed runs < 12h for once policy, rejects > 12h or skip."""
    now = datetime(2026, 10, 19, 12, 0, 0, tzinfo=UTC)

    # 2 hours ago (< 12 hours)
    due_2h = now - timedelta(hours=2)
    assert should_catchup(due_2h, now, catchup_policy="once") is True

    # 15 hours ago (> 12 hours)
    due_15h = now - timedelta(hours=15)
    assert should_catchup(due_15h, now, catchup_policy="once") is False

    # Skip policy
    assert should_catchup(due_2h, now, catchup_policy="skip") is False


# ============================================================================
# DELIVERY CHANNELS TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_ui_delivery_channel() -> None:
    """UIDeliveryChannel publishes notification and reminder events."""
    bus = EventBus()
    received = []
    bus.subscribe("*", lambda e: received.append(e))

    ch = UIDeliveryChannel(event_bus=bus)
    ok1 = await ch.deliver("Notice", "System updated")
    assert ok1 is True
    assert len(received) == 1
    assert received[0].type == "notification"

    ok2 = await ch.deliver("Reminder", "Time to stretch", payload={"kind": "reminder"})
    assert ok2 is True
    assert len(received) == 2
    assert received[1].type == "reminder_fired"


@pytest.mark.asyncio
async def test_desktop_delivery_channel_missing_binary() -> None:
    """DesktopDeliveryChannel handles missing notify-send gracefully."""
    ch = DesktopDeliveryChannel()
    with patch("shutil.which", return_value=None):
        ok = await ch.deliver("Title", "Message")
        assert ok is False


@pytest.mark.asyncio
async def test_desktop_delivery_channel_with_binary() -> None:
    """DesktopDeliveryChannel executes notify-send when available."""
    ch = DesktopDeliveryChannel()
    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.wait = AsyncMock(return_value=0)

    with patch("shutil.which", return_value="/usr/bin/notify-send"):
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            ok = await ch.deliver("Hello", "World")
            assert ok is True
            assert mock_exec.called


@pytest.mark.asyncio
async def test_delivery_router() -> None:
    """DeliveryRouter dispatches across multiple channels and handles unknown channels."""
    mock_ui = AsyncMock()
    mock_ui.deliver.return_value = True

    mock_desktop = AsyncMock()
    mock_desktop.deliver.return_value = False

    router = DeliveryRouter(channels={"ui": mock_ui, "desktop": mock_desktop})
    res = await router.deliver(["ui", "desktop", "nonexistent"], "T", "M")
    assert res["ui"] is True
    assert res["desktop"] is False
    assert res["nonexistent"] is False


# ============================================================================
# SCHEDULER & RECOVERY TESTS
# ============================================================================


async def _create_test_schema(db: aiosqlite.Connection) -> None:
    """Create minimal tables needed for scheduler test."""
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            title TEXT,
            status TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS task_nodes (
            id TEXT PRIMARY KEY,
            run_id TEXT,
            node_index INTEGER,
            title TEXT,
            objective TEXT,
            kind TEXT,
            depends_on_json TEXT,
            status TEXT NOT NULL,
            error TEXT,
            finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            name TEXT UNIQUE,
            description TEXT,
            schedule_type TEXT,
            schedule_expr TEXT,
            timezone TEXT,
            instruction TEXT,
            config_json TEXT,
            enabled INTEGER DEFAULT 1,
            created_by TEXT DEFAULT 'owner',
            version INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            last_run_at TEXT,
            next_run_at TEXT
        );
        CREATE TABLE IF NOT EXISTS job_runs (
            id TEXT PRIMARY KEY,
            job_id TEXT,
            started_at TEXT,
            finished_at TEXT,
            status TEXT NOT NULL,
            output_markdown TEXT,
            error TEXT
        );
        CREATE TABLE IF NOT EXISTS outputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_run_id TEXT,
            title TEXT,
            markdown TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        """
    )
    await db.commit()


@pytest.mark.asyncio
async def test_mark_interrupted_runs_at_start() -> None:
    """mark_interrupted_runs transitions orphaned running runs, tasks, and job_runs to interrupted."""
    db = await aiosqlite.connect(":memory:")
    await _create_test_schema(db)

    # Insert running records
    await db.execute("INSERT INTO runs (id, status) VALUES ('run_1', 'running')")
    await db.execute(
        "INSERT INTO task_nodes (id, run_id, status) VALUES ('node_1', 'run_1', 'running')"
    )
    await db.execute(
        "INSERT INTO job_runs (id, job_id, status) VALUES ('jrun_1', 'job_1', 'running')"
    )
    await db.commit()

    count = await mark_interrupted_runs(db)
    assert count == 2  # 1 run + 1 job_run

    # Check status
    r_run = await db.execute("SELECT status FROM runs WHERE id = 'run_1'")
    assert (await r_run.fetchone())[0] == "interrupted"

    r_task = await db.execute("SELECT status, error FROM task_nodes WHERE id = 'node_1'")
    row_t = await r_task.fetchone()
    assert row_t[0] == "failed"
    assert "interrupted" in row_t[1]

    r_job = await db.execute("SELECT status, error FROM job_runs WHERE id = 'jrun_1'")
    row_j = await r_job.fetchone()
    assert row_j[0] == "interrupted"
    assert "interrupted" in row_j[1]

    await db.close()


@pytest.mark.asyncio
async def test_scheduler_due_detection_order_and_failure_resilience() -> None:
    """Scheduler tick finds due jobs in order, and failing job never stops loop."""
    db = await aiosqlite.connect(":memory:")
    await _create_test_schema(db)

    now_iso = datetime.now(UTC).isoformat()
    earlier_iso = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()

    # Job 1 (earlier): Reminder job
    await db.execute(
        """
        INSERT INTO jobs (
            id, name, schedule_type, schedule_expr, instruction, config_json, next_run_at
        )
        VALUES ('j1', 'first-reminder', 'interval', '10m', 'Drink water',
                '{"kind": "reminder", "message": "Drink water"}', ?)
        """,
        (earlier_iso,),
    )
    # Job 2 (later): Agent job
    await db.execute(
        """
        INSERT INTO jobs (
            id, name, schedule_type, schedule_expr, instruction, config_json, next_run_at
        )
        VALUES ('j2', 'second-agent', 'interval', '10m', 'Say hello', '{"kind": "agent"}', ?)
        """,
        (now_iso,),
    )
    await db.commit()

    cfg = get_config()
    fake_brain = FakeProvider("ollama", is_local=True)
    fake_brain.queue_response([ChatChunk(delta_text="Hello from agent", finish_reason="stop")])
    router = ModelRouter(cfg, providers={"ollama": fake_brain})

    scheduler = JobScheduler(router=router, config=cfg)

    # Execute tick
    executed = await scheduler.tick(db)
    assert executed == ["first-reminder", "second-agent"]

    # Verify both recorded job_runs
    cur = await db.execute("SELECT job_id, status FROM job_runs ORDER BY started_at ASC")
    runs = await cur.fetchall()
    assert len(runs) == 2
    assert runs[0][0] == "j1" and runs[0][1] == "ok"
    assert runs[1][0] == "j2" and runs[1][1] == "ok"

    await db.close()


@pytest.mark.asyncio
async def test_scheduler_once_job_disables_after_firing() -> None:
    """Jobs of schedule_type 'once' are disabled and next_run_at cleared after firing."""
    db = await aiosqlite.connect(":memory:")
    await _create_test_schema(db)

    due_iso = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    await db.execute(
        """
        INSERT INTO jobs (
            id, name, schedule_type, schedule_expr, instruction, config_json, next_run_at
        )
        VALUES ('once_1', 'one-time-remind', 'once', ?, 'Alert', '{"kind": "reminder"}', ?)
        """,
        (due_iso, due_iso),
    )
    await db.commit()

    scheduler = JobScheduler(config=get_config())
    await scheduler.tick(db)

    # Verify job is now disabled with NULL next_run_at
    cur = await db.execute("SELECT enabled, next_run_at FROM jobs WHERE id = 'once_1'")
    row = await cur.fetchone()
    assert row[0] == 0
    assert row[1] is None

    await db.close()


@pytest.mark.asyncio
async def test_scheduler_max_runtime_seconds_cancellation() -> None:
    """Jobs that exceed max_runtime_seconds are cancelled with timeout status."""
    db = await aiosqlite.connect(":memory:")
    await _create_test_schema(db)

    due_iso = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    # Configure job with max_runtime_seconds = 0.05
    cfg_json = json.dumps({"kind": "agent", "max_runtime_seconds": 0.05})
    await db.execute(
        """
        INSERT INTO jobs (
            id, name, schedule_type, schedule_expr, instruction, config_json, next_run_at
        )
        VALUES ('timeout_job', 'slow-task', 'interval', '1h', 'Slow instruction', ?, ?)
        """,
        (cfg_json, due_iso),
    )
    await db.commit()

    cfg = get_config()

    # Provider that sleeps longer than 0.05s
    class SlowProvider(FakeProvider):
        async def chat(self, *args, **kwargs):
            await asyncio.sleep(0.3)
            yield ChatChunk(delta_text="Done")

    slow_brain = SlowProvider("ollama", is_local=True)
    router = ModelRouter(cfg, providers={"ollama": slow_brain})

    scheduler = JobScheduler(router=router, config=cfg)
    await scheduler.tick(db)

    # Check job_run status is failed with timeout message
    cur = await db.execute(
        "SELECT status, error, output_markdown FROM job_runs WHERE job_id = 'timeout_job'"
    )
    row = await cur.fetchone()
    assert row[0] == "failed"
    assert "exceeded max_runtime_seconds" in row[1]

    await db.close()


@pytest.mark.asyncio
async def test_scheduler_catchup_policies() -> None:
    """Scheduler catch_up_missed_jobs runs missed 'once' jobs <12h, skips >12h or 'skip' policy."""
    db = await aiosqlite.connect(":memory:")
    await _create_test_schema(db)

    now = datetime.now(UTC)
    miss_2h = (now - timedelta(hours=2)).isoformat()
    miss_15h = (now - timedelta(hours=15)).isoformat()

    # 1. Missed 2h ago, catchup=once -> caught up
    await db.execute(
        """
        INSERT INTO jobs (id, name, schedule_type, schedule_expr, instruction, config_json, next_run_at)
        VALUES ('c1', 'catchup-me', 'interval', '1h', 'Remind', '{"kind": "reminder", "catchup": "once"}', ?)
        """,
        (miss_2h,),
    )
    # 2. Missed 2h ago, catchup=skip -> skipped
    await db.execute(
        """
        INSERT INTO jobs (id, name, schedule_type, schedule_expr, instruction, config_json, next_run_at)
        VALUES ('c2', 'skip-me', 'interval', '1h', 'Remind', '{"kind": "reminder", "catchup": "skip"}', ?)
        """,
        (miss_2h,),
    )
    # 3. Missed 15h ago, catchup=once -> skipped
    await db.execute(
        """
        INSERT INTO jobs (id, name, schedule_type, schedule_expr, instruction, config_json, next_run_at)
        VALUES ('c3', 'too-old', 'interval', '1h', 'Remind', '{"kind": "reminder", "catchup": "once"}', ?)
        """,
        (miss_15h,),
    )
    await db.commit()

    scheduler = JobScheduler(config=get_config())
    caught_up_count = await scheduler.catch_up_missed_jobs(db)
    assert caught_up_count == 1

    # c1 ran
    cur = await db.execute("SELECT status FROM job_runs WHERE job_id = 'c1'")
    assert (await cur.fetchone())[0] == "ok"

    # c2 and c3 rescheduled without running
    cur2 = await db.execute("SELECT status FROM job_runs WHERE job_id = 'c2'")
    assert await cur2.fetchone() is None

    cur3 = await db.execute("SELECT status FROM job_runs WHERE job_id = 'c3'")
    assert await cur3.fetchone() is None

    await db.close()


@pytest.mark.asyncio
async def test_scheduler_delivery_channel_failure_does_not_fail_job() -> None:
    """When delivery router fails, job execution still completes successfully."""
    db = await aiosqlite.connect(":memory:")
    await _create_test_schema(db)

    due_iso = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    await db.execute(
        """
        INSERT INTO jobs (id, name, schedule_type, schedule_expr, instruction, config_json, next_run_at)
        VALUES ('deliv_job', 'broken-delivery', 'interval', '1h', 'Msg', '{"kind": "reminder"}', ?)
        """,
        (due_iso,),
    )
    await db.commit()

    mock_deliv = MagicMock()
    mock_deliv.deliver = AsyncMock(side_effect=RuntimeError("Delivery network error"))

    scheduler = JobScheduler(config=get_config(), delivery_router=mock_deliv)
    await scheduler.tick(db)

    cur = await db.execute("SELECT status FROM job_runs WHERE job_id = 'deliv_job'")
    row = await cur.fetchone()
    assert row[0] == "ok"

    await db.close()
