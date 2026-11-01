"""Phase 4 & 5 Verification Tests: Briefing Pipeline, Feedback Loop & Catch-up."""

from __future__ import annotations

import json

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


def test_candidate_item_schema_and_url_normalization():
    """Verify CandidateItem Pydantic schema validation and URL normalization."""
    from noxai.briefing.engine import CandidateItem, normalize_url

    raw_url = "https://example.org/article?utm_source=twitter&utm_medium=cpc&id=123#header"
    clean = normalize_url(raw_url)
    assert "utm_source" not in clean
    assert "utm_medium" not in clean
    assert clean == "https://example.org/article?id=123"

    item = CandidateItem(
        item_id="item_01",
        title="Linux Kernel Exploit Disclosed",
        url=raw_url,
        canonical_url=clean,
        source_name="The Hacker News",
        topic_id="topic_cybersecurity",
        topic_name="Cybersecurity & Exploits",
        summary="A critical local privilege escalation flaw was fixed in Linux kernel.",
        relevance_score=1.5,
    )
    assert item.item_id == "item_01"
    assert item.relevance_score == 1.5
    assert item.canonical_url == clean


@pytest.mark.asyncio
async def test_analyst_cross_topic_dedupe_and_ranking(setup_briefing):
    """Verify Analyst removes duplicate URLs and similar titles, and ranks by relevance."""
    pipeline = setup_briefing
    run_id = "brun_test_dedupe"

    async with get_db() as db:
        from noxai.db import execute
        # Insert 3 staged items: 2 duplicates (by URL tracking variation and title), 1 unique
        p1 = {
            "item_id": "it1",
            "title": "Quantum Computing Breakthrough at MIT",
            "url": "https://news.mit.edu/quantum?utm_campaign=feed",
            "canonical_url": "https://news.mit.edu/quantum",
            "summary": "MIT researchers achieved 1000 qubit coherence.",
            "source_name": "MIT News",
            "topic_id": "topic_ai",
            "topic_name": "AI & Frontier Systems",
            "relevance_score": 1.2,
        }
        p2 = {
            "item_id": "it2",
            "title": "Quantum Computing Breakthrough at MIT Lab",  # Similar title
            "url": "https://news.mit.edu/quantum",
            "canonical_url": "https://news.mit.edu/quantum",
            "summary": "MIT achieved qubit coherence.",
            "source_name": "Hacker News",
            "topic_id": "topic_cs",
            "topic_name": "Computer Science & Engineering",
            "relevance_score": 1.0,
        }
        p3 = {
            "item_id": "it3",
            "title": "CISA Releases Critical Advisory for ICS Routers",
            "url": "https://cisa.gov/advisory-ics",
            "canonical_url": "https://cisa.gov/advisory-ics",
            "summary": "Immediate patching required for critical ICS infrastructure.",
            "source_name": "CISA Alerts",
            "topic_id": "topic_cybersecurity",
            "topic_name": "Cybersecurity & Exploits",
            "relevance_score": 1.8,
        }
        for it in (p1, p2, p3):
            await execute(
                db,
                "INSERT INTO briefing_staging (run_id, topic_id, item_id, payload_json, status) VALUES (?, ?, ?, ?, 'ready')",
                (run_id, it["topic_id"], it["item_id"], json.dumps(it)),
            )
        await db.commit()

    analyst_res = await pipeline._run_analyst(run_id, "morning", "2026-10-31")
    items = analyst_res["items"]

    # Must deduplicate p1 and p2 -> only 2 unique items remain
    assert len(items) == 2

    # Highest relevance score (p3 with 1.8) must be first
    assert items[0]["item_id"] == "it3"
    assert items[1]["item_id"] == "it1"


@pytest.mark.asyncio
async def test_feedback_auto_revert_safety_net(setup_briefing):
    """Verify check_and_auto_revert rolls back rewritten prompt if negative feedback persists."""
    from noxai.briefing.feedback import check_and_auto_revert

    async with get_db() as db:
        await seed_briefing_topics_if_needed(db)

    # 1. First trigger a rewrite
    await record_feedback(briefing_id="b1", topic_id="topic_cs", rating="dislike", reason_text="Need more Linux kernels")
    async with get_db() as db:
        new_pv = await check_and_rewrite_topic_prompt("topic_cs", db)
        assert new_pv is not None

        # Verify new active version is 2
        active_row = await fetch_one(db, "SELECT version FROM prompt_versions WHERE scope = 'topic_topic_cs' AND status = 'active'")
        assert active_row["version"] == 2

    # 2. Simulate 2 subsequent dislikes under version 2
    await record_feedback(briefing_id="b2", topic_id="topic_cs", rating="dislike", reason_text="Too repetitive now")
    await record_feedback(briefing_id="b3", topic_id="topic_cs", rating="dislike", reason_text="Quality got worse")

    async with get_db() as db:
        reverted_ver = await check_and_auto_revert("topic_cs", db)
        assert reverted_ver == "1"

        # Verify active version is now restored to version 1
        active_restored = await fetch_one(db, "SELECT version FROM prompt_versions WHERE scope = 'topic_topic_cs' AND status = 'active'")
        assert active_restored["version"] == 1

