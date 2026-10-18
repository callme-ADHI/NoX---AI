"""Tests for Scheduler, cron, catchup, reminders, and concurrency queue per Section 6.7."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from noxai.config import ModelRoleConfig, ModelsConfig, Settings
from noxai.db import fetch_all, fetch_one
from noxai.models.fake import FakeProvider
from noxai.models.router import ModelRouter
from noxai.scheduler.delivery import DeliveryChannel, DeliveryRouter
from noxai.scheduler.scheduler import JobScheduler
from noxai.scheduler.seeds import seed_database_if_needed
from noxai.scheduler.time_utils import (
    DEFAULT_TIMEZONE,
    compute_next_run,
    parse_interval_seconds,
    parse_natural_time,
    should_catchup,
)
from noxai.tools.registry import ToolRegistry


class RecordingDeliveryChannel(DeliveryChannel):
    """Test channel that records delivered messages."""

    def __init__(self) -> None:
        self.delivered: list[dict[str, Any]] = []

    async def deliver(
        self,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
        db: aiosqlite.Connection | None = None,
    ) -> bool:
        self.delivered.append(
            {
                "title": title,
                "message": message,
                "payload": payload or {},
            }
        )
        return True


@pytest.fixture
def test_config() -> Settings:
    return Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="fake", model="fake-brain"),
                "heavy": ModelRoleConfig(provider="fake", model="fake-heavy"),
                "embed": ModelRoleConfig(provider="fake", model="fake-embed"),
            }
        )
    )


@pytest.fixture
def fake_router(test_config: Settings) -> tuple[ModelRouter, FakeProvider]:
    fake = FakeProvider()
    router = ModelRouter(config=test_config, providers={"fake": fake})
    return router, fake


@pytest.fixture
async def migrated_db(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    db_file = tmp_path / "nox_sched_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA foreign_keys = ON;")
    with open("src/noxai/migrations/001_initial.sql") as f:
        conn.executescript(f.read())
    with open("src/noxai/migrations/002_spec_alignment.sql") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()

    async_conn = await aiosqlite.connect(str(db_file))
    async_conn.row_factory = aiosqlite.Row
    yield async_conn
    await async_conn.close()


def test_time_utils_cron_and_timezone() -> None:
    """Cron expressions are computed in the specified timezone and returned in UTC."""
    # 7:30 AM in Asia/Kolkata (+05:30) is 2:00 AM UTC
    base = datetime(2026, 10, 19, 1, 0, 0, tzinfo=UTC)
    nxt = compute_next_run("cron", "30 7 * * *", "Asia/Kolkata", base_dt=base)
    assert nxt is not None
    assert nxt.hour == 2
    assert nxt.minute == 0


def test_time_utils_interval_parsing() -> None:
    """Intervals like '15m', '2h', '1d', '30s' are parsed accurately."""
    assert parse_interval_seconds("15m") == 900
    assert parse_interval_seconds("2h") == 7200
    assert parse_interval_seconds("1d") == 86400
    assert parse_interval_seconds("45s") == 45
    assert parse_interval_seconds("60") == 60


def test_time_utils_natural_language_parsing() -> None:
    """Natural language expressions parse into future datetimes."""
    base = datetime(2026, 10, 19, 10, 0, 0, tzinfo=UTC)
    parsed = parse_natural_time("in 20 minutes", tz_name=DEFAULT_TIMEZONE, base_dt=base)
    assert parsed is not None
    assert parsed > base
    diff_secs = (parsed - base).total_seconds()
    assert 1190 <= diff_secs <= 1210


def test_time_utils_should_catchup() -> None:
    """Missed under 12 hours qualifies for catch-up; over 12 hours or skip does not."""
    now = datetime(2026, 10, 19, 12, 0, 0, tzinfo=UTC)

    # 2 hours ago: catchup
    missed_2h = now - timedelta(hours=2)
    assert should_catchup(missed_2h, now, catchup_policy="once") is True
    assert should_catchup(missed_2h, now, catchup_policy="skip") is False

    # 13 hours ago: miss exceeds 12h threshold
    missed_13h = now - timedelta(hours=13)
    assert should_catchup(missed_13h, now, catchup_policy="once") is False


@pytest.mark.asyncio
async def test_seed_database_initialization(migrated_db: aiosqlite.Connection) -> None:
    """seed_database_if_needed populates initial jobs and verified sources idempotently."""
    first = await seed_database_if_needed(migrated_db)
    assert first is True

    # Check jobs
    jobs = await fetch_all(migrated_db, "SELECT name, schedule_expr FROM jobs ORDER BY name")
    job_names = [j["name"] for j in jobs]
    assert "daily-briefing" in job_names
    assert "opportunities-digest" in job_names
    assert "memory-consolidation" in job_names

    # Check sources
    sources = await fetch_all(migrated_db, "SELECT name, url FROM sources")
    assert len(sources) >= 5

    # Second call should be a no-op
    second = await seed_database_if_needed(migrated_db)
    assert second is False


@pytest.mark.asyncio
async def test_reminder_fires_with_no_llm_call(
    fake_router: tuple[ModelRouter, FakeProvider],
    migrated_db: aiosqlite.Connection,
    test_config: Settings,
) -> None:
    """Reminders fire directly via delivery channels with zero LLM calls."""
    router, fake = fake_router
    delivery_chan = RecordingDeliveryChannel()
    delivery_router = DeliveryRouter({"ui": delivery_chan, "desktop": delivery_chan})

    scheduler = JobScheduler(
        router=router,
        registry=ToolRegistry(),
        config=test_config,
        delivery_router=delivery_router,
    )

    # Create a reminder job scheduled in the past
    past_iso = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    rem_id = "rem_test_1"
    await migrated_db.execute(
        """
        INSERT INTO jobs
        (id, name, schedule_type, schedule_expr, instruction, config_json, enabled, next_run_at)
        VALUES (?, 'stretch-break', 'once', ?, 'Time to stretch!', ?, 1, ?)
        """,
        (
            rem_id,
            past_iso,
            json.dumps(
                {"kind": "reminder", "message": "Time to stretch and hydrate!", "delivery": ["ui"]}
            ),
            past_iso,
        ),
    )
    await migrated_db.commit()

    # Run tick
    executed = await scheduler.tick(migrated_db)
    assert "stretch-break" in executed

    # 1. Delivery received message
    assert len(delivery_chan.delivered) == 1
    assert "Time to stretch and hydrate!" in delivery_chan.delivered[0]["message"]

    # 2. Crucial spec requirement: ZERO LLM calls made!
    assert len(fake.calls) == 0

    # 3. 'once' job disabled after firing
    row = await fetch_one(
        migrated_db, "SELECT enabled, next_run_at FROM jobs WHERE id = ?", (rem_id,)
    )
    assert row is not None
    assert row["enabled"] == 0
    assert row["next_run_at"] is None


@pytest.mark.asyncio
async def test_catch_up_missed_jobs(
    fake_router: tuple[ModelRouter, FakeProvider],
    migrated_db: aiosqlite.Connection,
    test_config: Settings,
) -> None:
    """Startup catch-up executes jobs missed under 12 hours and reschedules them."""
    router, _ = fake_router
    delivery_chan = RecordingDeliveryChannel()
    delivery_router = DeliveryRouter({"ui": delivery_chan})

    scheduler = JobScheduler(
        router=router,
        registry=ToolRegistry(),
        config=test_config,
        delivery_router=delivery_router,
    )

    # Seed one missed reminder (missed 1 hour ago)
    one_hour_ago = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    await migrated_db.execute(
        """
        INSERT INTO jobs
        (id, name, schedule_type, schedule_expr, instruction, config_json, enabled, next_run_at)
        VALUES ('j_missed', 'missed-reminder', 'once', ?, 'Drink water', ?, 1, ?)
        """,
        (
            one_hour_ago,
            json.dumps(
                {
                    "kind": "reminder",
                    "message": "Drink water",
                    "catchup": "once",
                    "delivery": ["ui"],
                }
            ),
            one_hour_ago,
        ),
    )
    await migrated_db.commit()

    caught_up = await scheduler.catch_up_missed_jobs(migrated_db)
    assert caught_up == 1
    assert len(delivery_chan.delivered) == 1
    assert "Drink water" in delivery_chan.delivered[0]["message"]
