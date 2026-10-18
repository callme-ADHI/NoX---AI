"""Tests for Meta-tools: jobs, reminders, sources, preferences, undo (Section 6.7)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from noxai.config import ModelRoleConfig, ModelsConfig, Settings
from noxai.db import fetch_one
from noxai.models.fake import FakeProvider
from noxai.models.router import ModelRouter
from noxai.scheduler.seeds import seed_database_if_needed
from noxai.tools.builtin.scheduler_tools import (
    ChangeHistoryParams,
    JobCreateParams,
    JobGetParams,
    JobListParams,
    JobToggleParams,
    JobUpdateParams,
    PreferenceGetParams,
    PreferenceListParams,
    PreferenceSetParams,
    ReminderCancelParams,
    ReminderCreateParams,
    ReminderSnoozeParams,
    SourceAddParams,
    SourceListParams,
    SourceRemoveParams,
    UndoLastChangeParams,
    change_history,
    job_create,
    job_delete,
    job_disable,
    job_enable,
    job_get,
    job_list,
    job_update,
    preference_get,
    preference_list,
    preference_set,
    reminder_cancel,
    reminder_create,
    reminder_snooze,
    source_add,
    source_list,
    source_remove,
    undo_last_change,
)
from noxai.tools.registry import ToolContext


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
async def seeded_db(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    db_file = tmp_path / "nox_meta_test.db"
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
    await seed_database_if_needed(async_conn)
    yield async_conn
    await async_conn.close()


def _make_context(db: aiosqlite.Connection, router: ModelRouter, config: Settings) -> ToolContext:
    return ToolContext(
        run_id="test_meta_run",
        config=config,
        db=db,
        router=router,
    )


@pytest.mark.asyncio
async def test_job_update_and_undo_flow(
    seeded_db: aiosqlite.Connection,
    fake_router: tuple[ModelRouter, FakeProvider],
    test_config: Settings,
) -> None:
    """'move my daily news to 6 pm' updates job schedule, and 'undo that' restores it."""
    router, _ = fake_router
    ctx = _make_context(seeded_db, router, test_config)

    # 1. Verify initial schedule: 7:30 AM (30 7 * * *)
    init_res = await job_get(JobGetParams(name="daily-briefing"), ctx)
    assert init_res.ok
    assert "30 7 * * *" in init_res.output

    # 2. 'move my daily news to 6 pm'
    upd_res = await job_update(JobUpdateParams(name="daily-briefing", schedule="6 pm"), ctx)
    assert upd_res.ok
    assert "schedule changed" in upd_res.output

    # Check updated row in DB
    row_6pm = await fetch_one(
        seeded_db, "SELECT schedule_expr, version FROM jobs WHERE name = 'daily-briefing'"
    )
    assert row_6pm is not None
    assert row_6pm["schedule_expr"] == "0 18 * * *"
    assert row_6pm["version"] == 2

    # 3. Check history
    hist_res = await change_history(ChangeHistoryParams(entity="job", name="daily-briefing"), ctx)
    assert hist_res.ok
    assert "schedule changed" in hist_res.output

    # 4. 'undo that'
    undo_res = await undo_last_change(
        UndoLastChangeParams(entity="job", name="daily-briefing"), ctx
    )
    assert undo_res.ok
    assert "restored job 'daily-briefing' to version 1" in undo_res.output

    # Check restored row in DB
    restored_row = await fetch_one(
        seeded_db, "SELECT schedule_expr, version FROM jobs WHERE name = 'daily-briefing'"
    )
    assert restored_row is not None
    assert restored_row["schedule_expr"] == "30 7 * * *"
    assert restored_row["version"] == 1


@pytest.mark.asyncio
async def test_job_toggle_and_crud(
    seeded_db: aiosqlite.Connection,
    fake_router: tuple[ModelRouter, FakeProvider],
    test_config: Settings,
) -> None:
    """Test job_create, job_disable ('turn off opportunities digest'), job_enable, job_delete."""
    router, _ = fake_router
    ctx = _make_context(seeded_db, router, test_config)

    # Disable opportunities-digest
    dis_res = await job_disable(JobToggleParams(name="opportunities-digest"), ctx)
    assert dis_res.ok
    row_dis = await fetch_one(
        seeded_db, "SELECT enabled FROM jobs WHERE name = 'opportunities-digest'"
    )
    assert row_dis is not None
    assert row_dis["enabled"] == 0

    # Enable it again
    en_res = await job_enable(JobToggleParams(name="opportunities-digest"), ctx)
    assert en_res.ok
    row_en = await fetch_one(
        seeded_db, "SELECT enabled FROM jobs WHERE name = 'opportunities-digest'"
    )
    assert row_en is not None
    assert row_en["enabled"] == 1

    # Create a custom job
    create_res = await job_create(
        JobCreateParams(
            name="kerala-tech-scan",
            description="Scan Kerala startup news",
            schedule="0 10 * * 1",
            instruction="Find Kerala startup highlights",
        ),
        ctx,
    )
    assert create_res.ok

    # List jobs
    list_res = await job_list(JobListParams(enabled_only=True), ctx)
    assert list_res.ok
    assert "kerala-tech-scan" in list_res.output

    # Delete custom job
    del_res = await job_delete(JobToggleParams(name="kerala-tech-scan"), ctx)
    assert del_res.ok
    row_del = await fetch_one(seeded_db, "SELECT * FROM jobs WHERE name = 'kerala-tech-scan'")
    assert row_del is None


@pytest.mark.asyncio
async def test_reminder_tools(
    seeded_db: aiosqlite.Connection,
    fake_router: tuple[ModelRouter, FakeProvider],
    test_config: Settings,
) -> None:
    """reminder_create sets natural time reminder, reminder_snooze adjusts time,
    and reminder_cancel disables.
    """
    router, _ = fake_router
    ctx = _make_context(seeded_db, router, test_config)

    # 1. Create reminder: "remind me in 15 minutes to take a break"
    create_res = await reminder_create(
        ReminderCreateParams(message="Take a break", when_text="in 15 minutes"),
        ctx,
    )
    assert create_res.ok
    assert "Reminder set for" in create_res.output
    assert "Take a break" in create_res.output

    # Find created reminder in DB
    rem_row = await fetch_one(
        seeded_db, "SELECT id, name, next_run_at FROM jobs WHERE instruction = 'Take a break'"
    )
    assert rem_row is not None
    rem_id = rem_row["id"]
    initial_next = rem_row["next_run_at"]

    # 2. Snooze reminder (+20 minutes)
    snooze_res = await reminder_snooze(ReminderSnoozeParams(reminder_id=rem_id, minutes=20), ctx)
    assert snooze_res.ok
    assert "Snoozed reminder" in snooze_res.output

    snoozed_row = await fetch_one(seeded_db, "SELECT next_run_at FROM jobs WHERE id = ?", (rem_id,))
    assert snoozed_row is not None
    assert snoozed_row["next_run_at"] > initial_next

    # 3. Cancel reminder
    cancel_res = await reminder_cancel(ReminderCancelParams(reminder_id=rem_id), ctx)
    assert cancel_res.ok
    cancelled_row = await fetch_one(seeded_db, "SELECT enabled FROM jobs WHERE id = ?", (rem_id,))
    assert cancelled_row is not None
    assert cancelled_row["enabled"] == 0


@pytest.mark.asyncio
async def test_source_management_tools(
    seeded_db: aiosqlite.Connection,
    fake_router: tuple[ModelRouter, FakeProvider],
    test_config: Settings,
) -> None:
    """source_add, source_list, source_remove manage feed sources."""
    router, _ = fake_router
    ctx = _make_context(seeded_db, router, test_config)

    # Add new source
    add_res = await source_add(
        SourceAddParams(
            name="Semiconductor Industry Association",
            url="https://www.semiconductors.org/feed/",
            kind="rss",
            tags=["news", "chips", "tech"],
        ),
        ctx,
    )
    assert add_res.ok

    # List by tag
    chip_sources = await source_list(SourceListParams(tag="chips"), ctx)
    assert chip_sources.ok
    assert "Semiconductor Industry Association" in chip_sources.output

    # Remove source
    rem_res = await source_remove(
        SourceRemoveParams(name_or_url="Semiconductor Industry Association"), ctx
    )
    assert rem_res.ok

    # Verify removed
    chip_sources_after = await source_list(SourceListParams(tag="chips"), ctx)
    assert "Semiconductor Industry Association" not in chip_sources_after.output


@pytest.mark.asyncio
async def test_preference_tools(
    seeded_db: aiosqlite.Connection,
    fake_router: tuple[ModelRouter, FakeProvider],
    test_config: Settings,
) -> None:
    """preference_set, preference_get, preference_list manage owner preferences."""
    router, _ = fake_router
    ctx = _make_context(seeded_db, router, test_config)

    # Set preference
    set_res = await preference_set(
        PreferenceSetParams(key="briefing.focus", value="appsec and cloud"), ctx
    )
    assert set_res.ok

    # Get preference
    get_res = await preference_get(PreferenceGetParams(key="briefing.focus"), ctx)
    assert get_res.ok
    assert get_res.output == "appsec and cloud"

    # List preferences
    list_res = await preference_list(PreferenceListParams(), ctx)
    assert list_res.ok
    data = json.loads(list_res.output)
    assert data["briefing.focus"] == "appsec and cloud"
