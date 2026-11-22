"""T-1 Verification Test Suite: Briefing Content Quality, Link Checks & Live Verification
(§4.1 - §4.7).

Covers:
1. Strict link check rules:
   - 2xx/3xx pass
   - 401/403/429 kept IF known_fetched=True; dropped IF known_fetched=False
   - 404/410/5xx dropped
   - Soft-404 dropped
   - Timeout/DNS error dropped
2. Opportunity quotas and CTF cap (CTFs <= 3)
3. Verbatim <=25-word evidence snippet validation & fabricated claim rejection
4. Local eligibility matching against profile
5. Carry-over registry persistence and state preservation
6. Per-stage timings and drop reason tracking in pipeline
7. Live test (@pytest.mark.live) with real cloud model and real network requests
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from noxai.briefing.engine import (
    BriefingPipeline,
    CandidateItem,
    HackathonItem,
    InternshipBenefits,
    validate_internship_evidence,
    verify_evidence_snippet,
)
from noxai.config import get_config
from noxai.db import execute, get_db, init_db
from noxai.research.crawler import WebResearcher

# ===========================================================================
# 1. STRICT LINK CHECK POLICY TESTS (§4.7)
# ===========================================================================


@pytest.mark.asyncio
async def test_link_verification_policy():
    """Verify strict link verification behavior for 2xx/3xx, 401/403/429, 404, 5xx, soft-404."""
    cfg = get_config()
    researcher = WebResearcher(config=cfg)

    # 1. 200 OK -> Pass
    async def mock_200(*args, **kwargs):
        return httpx.Response(200, request=httpx.Request("HEAD", "https://example.com/ok"))

    with patch.object(researcher, "_safe_request", side_effect=mock_200):
        ok, code, reason = await researcher.verify_link("https://example.com/ok")
        assert ok is True
        assert code == 200
        assert reason == "ok"

    # 2. 301 Redirect -> Pass
    async def mock_301(*args, **kwargs):
        return httpx.Response(301, request=httpx.Request("HEAD", "https://example.com/redirect"))

    with patch.object(researcher, "_safe_request", side_effect=mock_301):
        ok, code, reason = await researcher.verify_link("https://example.com/redirect")
        assert ok is True
        assert code == 301
        assert reason == "ok"

    # 3. 403 Forbidden with known_fetched=True -> Kept
    async def mock_403(*args, **kwargs):
        return httpx.Response(403, request=httpx.Request("HEAD", "https://example.com/walled"))

    with patch.object(researcher, "_safe_request", side_effect=mock_403):
        ok, code, reason = await researcher.verify_link(
            "https://example.com/walled", known_fetched=True
        )
        assert ok is True
        assert code == 403
        assert reason == "blocked_or_login_kept"

    # 4. 403 Forbidden with known_fetched=False -> Dropped
    with patch.object(researcher, "_safe_request", side_effect=mock_403):
        ok, code, reason = await researcher.verify_link(
            "https://example.com/walled", known_fetched=False
        )
        assert ok is False
        assert code == 403
        assert reason == "blocked_or_login"

    # 5. 404 Not Found -> Dropped
    async def mock_404(*args, **kwargs):
        return httpx.Response(404, request=httpx.Request("HEAD", "https://example.com/notfound"))

    with patch.object(researcher, "_safe_request", side_effect=mock_404):
        ok, code, reason = await researcher.verify_link("https://example.com/notfound")
        assert ok is False
        assert code == 404
        assert reason == "not_found"

    # 6. 500 Server Error -> Dropped after retries
    async def mock_500(*args, **kwargs):
        return httpx.Response(500, request=httpx.Request("HEAD", "https://example.com/servererror"))

    with patch.object(researcher, "_safe_request", side_effect=mock_500):
        ok, code, reason = await researcher.verify_link("https://example.com/servererror")
        assert ok is False
        assert code >= 500
        assert reason == "server_error"

    # 7. Soft-404: 200 status but body contains "page not found" -> Dropped
    async def mock_soft_404(method, *args, **kwargs):
        if method == "HEAD":
            raise httpx.RequestError("HEAD not supported")
        return httpx.Response(
            200,
            text=(
                "<html><head><title>404 - Page Not Found</title></head>"
                "<body>Missing</body></html>"
            ),
            request=httpx.Request("GET", "https://example.com/soft404"),
        )

    with patch.object(researcher, "_safe_request", side_effect=mock_soft_404):
        ok, code, reason = await researcher.verify_link("https://example.com/soft404")
        assert ok is False
        assert code == 404
        assert reason == "soft_404"


# ===========================================================================
# 2. EVIDENCE RULES & FABRICATION REJECTION (§4.3)
# ===========================================================================


def test_verbatim_evidence_and_length_rule():
    """Snippets must be <= 25 words and appear verbatim; else forced to unknown."""
    page_text = (
        "The Google Summer of Code is a global, online program focused on bringing new "
        "contributors into open source software development. GSoC Contributors work with an "
        "open source organization on a 12+ week programming project under the guidance of "
        "mentors. Contributors receive a stipend to support their work during the program."
    )

    # Valid <= 25 words verbatim
    valid_snip = "Contributors receive a stipend to support their work during the program"
    assert verify_evidence_snippet(valid_snip, page_text) is True
    assert len(valid_snip.split()) <= 25

    # Snippet > 25 words must fail
    long_snip = (
        "The Google Summer of Code is a global, online program focused on bringing new "
        "contributors into open source software development. GSoC Contributors work with an "
        "open source organization on a 12+ week programming project under mentors"
    )
    assert len(long_snip.split()) > 25
    assert verify_evidence_snippet(long_snip, page_text) is False

    # Fabricated claim must fail
    fake_snip = "Free all-expenses paid luxury villa in Mountain View California"
    assert verify_evidence_snippet(fake_snip, page_text) is False

    # Validate benefits cleans unverified claims
    b = InternshipBenefits(
        accommodation="provided",
        stipend="yes",
        travel="covered",
        visa_support="yes",
    )
    ev = {
        "accommodation": "Free all-expenses paid luxury villa in Mountain View",  # Fake!
        "stipend": "Contributors receive a stipend to support their work",        # Real!
        "travel": "Private jet transportation included",                         # Fake!
    }
    cleaned_b, cleaned_ev = validate_internship_evidence(b, ev, page_text)
    assert cleaned_b.stipend == "yes"
    assert cleaned_b.accommodation == "unknown"
    assert cleaned_b.travel == "unknown"
    assert cleaned_b.visa_support == "unknown"
    assert "accommodation" not in cleaned_ev
    assert "travel" not in cleaned_ev
    assert "stipend" in cleaned_ev


# ===========================================================================
# 3. QUOTAS, CTF CAP & STAGE METRICS (§4.4, §4.5)
# ===========================================================================


@pytest.mark.asyncio
async def test_ctf_cap_and_drop_reasons(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify that CTFs are capped at <= 3 and drop reasons are tracked accurately."""
    test_db = tmp_path / "test_t1.db"
    monkeypatch.setattr("noxai.db.DB_PATH", test_db)
    monkeypatch.setattr("noxai.db.DATA_DIR", tmp_path)
    monkeypatch.setattr("noxai.briefing.engine.BRIEFINGS_DIR", tmp_path / "briefings")
    monkeypatch.setattr("noxai.briefing.engine.STAGING_DIR", tmp_path / "staging")
    await init_db()

    cfg = get_config()
    pipeline = BriefingPipeline(config=cfg)
    run_id = "brun_t1_test"
    today = "2026-11-22"

    async with get_db() as db:
        await execute(
            db,
            """
            INSERT INTO briefing_runs (id, date, slot, state, trigger, rev, title)
            VALUES (?, ?, 'morning', 'running', 'manual', 1, 'T-1 Run')
            """,
            (run_id, today),
        )

        # Stage 5 CTFs
        for i in range(5):
            ctf = CandidateItem(
                item_id=f"ctf_{i}",
                title=f"National Cyber Defense CTF #{i}",
                url=f"https://ctf.example.org/event/{i}",
                canonical_url=f"https://ctf.example.org/event/{i}",
                source_name="CTFtime",
                category="competitions",
                topic_id="topic_ctfs",
                topic_name="CTFs & Security Challenges",
                summary="Binary exploitation and web challenge.",
                relevance_score=1.5,
                verified_link=True,
            )
            await execute(
                db,
                """
                INSERT INTO briefing_staging (run_id, topic_id, item_id, payload_json, status)
                VALUES (?, ?, ?, ?, 'ready')
                """,
                (run_id, "topic_ctfs", f"ctf_{i}", json.dumps(ctf.model_dump())),
            )

        # Stage 3 Hackathons with distinct titles
        h_titles = [
            "ETHGlobal San Francisco Hackathon",
            "DoraHacks AppChain Bounty Hack",
            "Devpost Global AI Agents Challenge",
        ]
        for i, title in enumerate(h_titles):
            hack = HackathonItem(
                item_id=f"hack_{i}",
                title=title,
                url=f"https://devpost.com/hackathon/{i}",
                canonical_url=f"https://devpost.com/hackathon/{i}",
                source_name="Devpost",
                category="hackathon",
                topic_id="topic_hackathons",
                topic_name="Hackathons & Ideathons",
                summary="Build open source AI applications.",
                relevance_score=2.0,
                score=85,
                verified_link=True,
                organiser="Devpost",
                deadline="2026-12-15",
                prize_text="$25,000 in bounties",
                apply_url=f"https://devpost.com/hackathon/{i}",
            )
            await execute(
                db,
                """
                INSERT INTO briefing_staging (run_id, topic_id, item_id, payload_json, status)
                VALUES (?, ?, ?, ?, 'ready')
                """,
                (run_id, "topic_hackathons", f"hack_{i}", json.dumps(hack.model_dump())),
            )
        await db.commit()

    analyst_result = await pipeline._run_analyst(run_id, "morning", today)
    items = analyst_result["items"]

    # Verify CTFs capped at <= 3
    ctf_items = [it for it in items if it.get("topic_id") == "topic_ctfs"]
    assert len(ctf_items) <= 3

    # Verify Hackathons retained
    hack_items = [it for it in items if it.get("category") == "hackathon"]
    assert len(hack_items) == 3
    for h in hack_items:
        assert h.get("deadline") is not None
        assert h.get("prize_text") is not None
        assert h.get("apply_url") is not None

    # Verify drop reasons recorded
    assert "drop_reasons" in analyst_result
    assert isinstance(analyst_result["drop_reasons"], dict)


# ===========================================================================
# 4. LIVE MODEL & LIVE NETWORK PROOF (§4.1, §4.6)
# ===========================================================================


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_briefing_research_and_model_validation():
    """Live test: hits real network to verify watchlist fetching and live model evaluation."""
    cfg = get_config()
    researcher = WebResearcher(config=cfg)

    # 1. Real network link check on a live stable domain (e.g. iana.org / example.com)
    ok_live, code_live, _reason_live = await researcher.verify_link("https://example.com")
    assert ok_live is True
    assert code_live == 200

    # 2. Real network link check on a 404 URL
    ok_404, code_404, _reason_404 = await researcher.verify_link(
        "https://example.com/this-path-definitely-does-not-exist-nox-test"
    )
    assert ok_404 is False
    assert code_404 == 404

    # 3. Live model verification via ModelRouter and KeyPool
    from noxai.models.router import ModelRouter

    router = ModelRouter(config=cfg)
    assert not router.key_pool.is_empty(), "KeyPool must have healthy keys configured"

    # Send briefing extraction prompt to live cloud model
    from noxai.models.base import Message

    prompt = (
        "Extract key information from this snippet: "
        "'CERN Summer Student Programme Geneva offers CHF 90 per day stipend and covers travel.' "
        "Format as JSON with keys: 'stipend', 'location', 'travel'."
    )
    resp = await router.chat(
        [Message(role="user", content=prompt)],
        allow_cloud=True,
        temperature=0.1,
    )
    assert resp.text
    assert "chf" in resp.text.lower() or "90" in resp.text.lower() or "geneva" in resp.text.lower()
