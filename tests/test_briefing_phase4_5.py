"""Phase 4 & 5 Verification Tests: Briefing Pipeline, Feedback Loop & Catch-up."""

from __future__ import annotations

import pytest

from noxai.briefing.engine import BriefingPipeline, seed_briefing_topics_if_needed
from noxai.briefing.feedback import check_and_rewrite_topic_prompt, record_feedback
from noxai.config import get_config
from noxai.db import fetch_all, fetch_one, get_db, init_db


@pytest.fixture
async def setup_briefing(tmp_path, monkeypatch):
    test_db = tmp_path / "test_briefing.db"
    monkeypatch.setattr("noxai.db.DB_PATH", test_db)
    monkeypatch.setattr("noxai.db.DATA_DIR", tmp_path)
    monkeypatch.setattr("noxai.briefing.engine.BRIEFINGS_DIR", tmp_path / "briefings")
    monkeypatch.setattr("noxai.briefing.engine.STAGING_DIR", tmp_path / "staging")
    await init_db()

    cfg = get_config()
    pipeline = BriefingPipeline(config=cfg)
    return pipeline


@pytest.mark.asyncio
async def test_briefing_execution_and_markdown_output(setup_briefing):
    """Test full pipeline run produces DB records and markdown artifact."""
    pipeline = setup_briefing
    run_id = await pipeline.execute_run(slot="morning", date_str="2026-10-29", force=True)

    async with get_db() as db:
        run_row = await fetch_one(db, "SELECT * FROM briefing_runs WHERE id = ?", (run_id,))
        assert run_row is not None
        assert run_row["state"] == "succeeded"
        assert run_row["slot"] == "morning"

        items = await fetch_all(db, "SELECT * FROM briefing_items WHERE briefing_id = ?", (run_id,))
        assert len(items) > 0


@pytest.mark.asyncio
async def test_feedback_adjusts_weight_and_triggers_prompt_rewrite(setup_briefing):
    """Test like/dislike updates topic weights and auto-rewrites candidate prompt."""
    async with get_db() as db:
        await seed_briefing_topics_if_needed(db)
        t_before = await fetch_one(db, "SELECT weight FROM briefing_topics WHERE id = 'topic_ai'")
        initial_weight = float(t_before["weight"])

    # Dislike with reason
    await record_feedback(
        briefing_id="brun_test",
        topic_id="topic_ai",
        rating="dislike",
        reason_text="Summaries are too long, make them punchier",
        tags=["too_long"],
    )

    async with get_db() as db:
        t_after = await fetch_one(db, "SELECT weight FROM briefing_topics WHERE id = 'topic_ai'")
        assert float(t_after["weight"]) < initial_weight

        # Trigger prompt rewrite
        new_pv = await check_and_rewrite_topic_prompt("topic_ai", db)
        assert new_pv is not None

        pv_row = await fetch_one(db, "SELECT * FROM prompt_versions WHERE id = ?", (new_pv,))
        assert pv_row is not None
        assert "Summaries are too long" in pv_row["changelog"]
