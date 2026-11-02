"""Unit and integration tests for briefing state machine, gates, and resume (Session K, Phase 1)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import noxai.db as db_mod
from noxai.briefing.engine import BriefingPipeline
from noxai.briefing.scheduler import BriefingScheduler
from noxai.config import get_config
from noxai.db import execute, fetch_all, fetch_one, get_db, run_migrations


@pytest.fixture
async def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    db_file = tmp_path / "nox_test.db"
    monkeypatch.setattr(db_mod, "DB_PATH", db_file)
    monkeypatch.setenv("NOX_DATABASE_PATH", str(db_file))
    await run_migrations()
    return db_file


@pytest.mark.asyncio
async def test_state_machine_zero_items_fails(temp_db: Path) -> None:
    """When pipeline produces 0 items, state MUST be 'failed', never 'succeeded'."""
    cfg = get_config()
    cfg.briefing.min_items_total = 15
    pipeline = BriefingPipeline(config=cfg)

    # Mock analyst to return 0 items
    async def mock_analyst(run_id: str, slot: str, today_str: str) -> dict:
        return {
            "items": [],
            "top_3": [],
            "tldr": "Empty",
            "sources_count": 0,
            "total_items": 0,
        }

    with patch.object(pipeline, "_run_analyst", side_effect=mock_analyst):
        with patch.object(pipeline, "_run_composer", return_value=temp_db.parent / "test.md"):
            with patch.object(pipeline, "_send_notification", new_callable=AsyncMock):
                run_id = await pipeline.execute_run(slot="morning", date_str="2026-11-02")

    async with get_db() as db:
        row = await fetch_one(db, "SELECT state, failure_reason FROM briefing_runs WHERE id = ?", (run_id,))
        assert row is not None
        assert row["state"] == "failed"
        assert "empty_run: pipeline produced 0 items (min required: 15)" in row["failure_reason"]


@pytest.mark.asyncio
async def test_state_machine_partial_items_gate(temp_db: Path) -> None:
    """When pipeline produces < min_items_total (e.g. 5 items), state MUST be 'partial'."""
    cfg = get_config()
    cfg.briefing.min_items_total = 15
    pipeline = BriefingPipeline(config=cfg)

    mock_items = [
        {"title": f"News {i}", "url": f"https://example.com/n{i}", "category": "news"}
        for i in range(4)
    ] + [
        {"title": "Internship 1", "url": "https://example.com/i1", "category": "internship", "score": 80}
    ]

    async def mock_analyst(run_id: str, slot: str, today_str: str) -> dict:
        return {
            "items": mock_items,
            "top_3": mock_items[:3],
            "tldr": "Partial briefing",
            "sources_count": 5,
            "total_items": 5,
        }

    with patch.object(pipeline, "_run_analyst", side_effect=mock_analyst):
        with patch.object(pipeline, "_run_composer", return_value=temp_db.parent / "test.md"):
            with patch.object(pipeline, "_send_notification", new_callable=AsyncMock):
                run_id = await pipeline.execute_run(slot="morning", date_str="2026-11-02")

    async with get_db() as db:
        row = await fetch_one(db, "SELECT state, failure_reason FROM briefing_runs WHERE id = ?", (run_id,))
        assert row is not None
        assert row["state"] == "partial"
        assert "empty_run: pipeline produced 5 items (min required: 15)" in row["failure_reason"]


@pytest.mark.asyncio
async def test_state_machine_missing_opportunity_marks_partial(temp_db: Path) -> None:
    """When pipeline produces >= 15 items but 0 opportunity items, state MUST be 'partial'."""
    cfg = get_config()
    cfg.briefing.min_items_total = 15
    pipeline = BriefingPipeline(config=cfg)

    # 16 news items, 0 opportunities
    mock_items = [
        {"title": f"News {i}", "url": f"https://example.com/n{i}", "category": "news"}
        for i in range(16)
    ]

    async def mock_analyst(run_id: str, slot: str, today_str: str) -> dict:
        return {
            "items": mock_items,
            "top_3": mock_items[:3],
            "tldr": "No opps",
            "sources_count": 16,
            "total_items": 16,
        }

    with patch.object(pipeline, "_run_analyst", side_effect=mock_analyst):
        with patch.object(pipeline, "_run_composer", return_value=temp_db.parent / "test.md"):
            with patch.object(pipeline, "_send_notification", new_callable=AsyncMock):
                run_id = await pipeline.execute_run(slot="morning", date_str="2026-11-02")

    async with get_db() as db:
        row = await fetch_one(db, "SELECT state, failure_reason FROM briefing_runs WHERE id = ?", (run_id,))
        assert row is not None
        assert row["state"] == "partial"
        assert "insufficient_opportunities" in row["failure_reason"]


@pytest.mark.asyncio
async def test_state_machine_meeting_gates_succeeds(temp_db: Path) -> None:
    """When pipeline produces >= 15 items with at least 1 news and 1 opp, state MUST be 'succeeded'."""
    cfg = get_config()
    cfg.briefing.min_items_total = 15
    pipeline = BriefingPipeline(config=cfg)

    mock_items = [
        {"title": f"News {i}", "url": f"https://example.com/n{i}", "category": "news"}
        for i in range(12)
    ] + [
        {"title": f"Internship {i}", "url": f"https://example.com/i{i}", "category": "internship", "score": 75}
        for i in range(3)
    ]

    async def mock_analyst(run_id: str, slot: str, today_str: str) -> dict:
        return {
            "items": mock_items,
            "top_3": mock_items[:3],
            "tldr": "Complete and verified briefing",
            "sources_count": 15,
            "total_items": 15,
        }

    with patch.object(pipeline, "_run_analyst", side_effect=mock_analyst):
        with patch.object(pipeline, "_run_composer", return_value=temp_db.parent / "test.md"):
            with patch.object(pipeline, "_send_notification", new_callable=AsyncMock):
                run_id = await pipeline.execute_run(slot="morning", date_str="2026-11-02")

    async with get_db() as db:
        row = await fetch_one(db, "SELECT state, failure_reason FROM briefing_runs WHERE id = ?", (run_id,))
        assert row is not None
        assert row["state"] == "succeeded"
        assert row["failure_reason"] is None


@pytest.mark.asyncio
async def test_resume_preserves_run_id_and_skips_done_topics(temp_db: Path) -> None:
    """Interrupted runs resume under SAME run_id and skip already completed topics."""
    run_id = "brun_test_resume_1"
    async with get_db() as db:
        await execute(
            db,
            """
            INSERT INTO briefing_runs (id, date, slot, state, trigger, rev, title)
            VALUES (?, '2026-11-02', 'morning', 'interrupted', 'scheduled', 1, 'Test Resume')
            """,
            (run_id,),
        )
        # Mark 2 topics as already 'done'
        await execute(
            db,
            """
            INSERT INTO briefing_topic_runs (run_id, topic_id, state, items_staged)
            VALUES (?, 'topic_ai', 'done', 5), (?, 'topic_systems', 'done', 4)
            """,
            (run_id, run_id),
        )
        await db.commit()

    cfg = get_config()
    sched = BriefingScheduler(config=cfg)

    # Execute repair_runs with requeue=True
    repaired = await sched.repair_runs(requeue=True)
    assert len(repaired) == 1
    assert repaired[0]["run_id"] == run_id
    assert repaired[0]["requeued"] is True

    # Check briefing_runs state was updated to queued under SAME run_id
    async with get_db() as db:
        row = await fetch_one(db, "SELECT state, task_id FROM briefing_runs WHERE id = ?", (run_id,))
        assert row is not None
        assert row["state"] == "queued"
        assert row["task_id"] is not None

        # Verify topic runs remain preserved
        t_rows = await fetch_all(db, "SELECT topic_id, state FROM briefing_topic_runs WHERE run_id = ?", (run_id,))
        done_map = {r["topic_id"]: r["state"] for r in t_rows}
        assert done_map["topic_ai"] == "done"
        assert done_map["topic_systems"] == "done"
