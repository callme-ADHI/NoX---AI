"""Phase 5 Tests: Funded Internships, Hackathons, Evidence Verification & Opportunity Registry."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from noxai.briefing.engine import (
    BriefingPipeline,
    CandidateItem,
    InternshipBenefits,
    InternshipEligibility,
    InternshipItem,
    compute_hackathon_score,
    compute_internship_score,
    load_watchlist_programs,
    match_eligibility,
    validate_internship_evidence,
    verify_evidence_snippet,
)
from noxai.config import BriefingEligibilityProfile, BriefingValueWeights, get_config
from noxai.db import execute, fetch_one, get_db, init_db
from noxai.notes.vault import NotesVault


def test_evidence_verification_and_rejection():
    """Verify verbatim snippet validation and rejection of fabricated claims."""
    page_text = (
        "CERN Summer Student Programme offers accepted students a monthly subsistence "
        "allowance to cover accommodation and living expenses in the Geneva area. "
        "Travel expenses are also reimbursed on a lump sum basis. Eligible applicants must "
        "be enrolled in a bachelor or master student degree in computer science or physics."
    )

    # 1. Exact snippet present
    snippet_stipend = "monthly subsistence allowance to cover accommodation"
    assert verify_evidence_snippet(snippet_stipend, page_text) is True

    # 2. Case-insensitive and whitespace normalized
    snippet_norm = "MONTHLY   subsistence ALLOWANCE  to cover accommodation"
    assert verify_evidence_snippet(snippet_norm, page_text) is True

    # 3. Fabricated claim NOT in text
    snippet_fake = "free luxury penthouse suite in downtown Geneva with private chauffeur"
    assert verify_evidence_snippet(snippet_fake, page_text) is False

    # 4. validate_internship_evidence forces unverified benefits to 'unknown'
    claimed_benefits = InternshipBenefits(
        accommodation="provided",
        stipend="yes",
        travel="covered",
        visa_support="yes",
    )
    evidence = {
        "accommodation": "allowance to cover accommodation and living expenses",
        "stipend": "monthly subsistence allowance",
        "travel": "Travel expenses are also reimbursed",
        "visa_support": "full diplomatic visa issued immediately",  # Fabricated!
    }

    valid_b, valid_ev = validate_internship_evidence(claimed_benefits, evidence, page_text)
    assert valid_b.accommodation == "provided"
    assert valid_b.stipend == "yes"
    assert valid_b.travel == "covered"
    # Fabricated visa support MUST be forced to unknown!
    assert valid_b.visa_support == "unknown"
    assert "visa_support" not in valid_ev


def test_local_eligibility_matching():
    """Verify strict local matching against eligibility profile."""
    profile = BriefingEligibilityProfile(
        nationality="India",
        level="undergraduate",
        field="computer science",
    )

    # Match: open nationalities, undergrad, CS
    el_open = InternshipEligibility(
        education_level="undergraduate",
        fields=["computer science", "engineering"],
        nationalities=["open"],
    )
    assert match_eligibility(el_open, profile) == "yes"

    # Match: all/any level
    el_any = InternshipEligibility(
        education_level="any",
        fields=["all"],
        nationalities=["open"],
    )
    assert match_eligibility(el_any, profile) == "yes"

    # Exclusion: PhD only
    el_phd = InternshipEligibility(
        education_level="phd",
        fields=["computer science"],
        nationalities=["open"],
    )
    assert match_eligibility(el_phd, profile) == "no"

    # Exclusion: restricted nationality excluding India
    el_eu = InternshipEligibility(
        education_level="undergraduate",
        fields=["computer science"],
        nationalities=["Germany", "France", "Switzerland"],
    )
    assert match_eligibility(el_eu, profile) == "no"


def test_internship_and_hackathon_value_scoring():
    """Verify value scoring algorithm and why_ranked breakdown."""
    profile = BriefingEligibilityProfile(
        nationality="India",
        level="undergraduate",
        field="computer science",
    )
    weights = BriefingValueWeights()

    # CERN-like high value internship in Switzerland closing in 5 days
    five_days_away = (datetime.now(UTC) + timedelta(days=5)).strftime("%Y-%m-%d")
    cern_benefits = InternshipBenefits(
        accommodation="provided",
        stipend="yes",
        travel="covered",
        visa_support="yes",
    )
    item_data = {
        "benefits": cern_benefits,
        "country": "Switzerland",
        "eligibility_match": "yes",
        "deadline": five_days_away,
    }

    score, why = compute_internship_score(item_data, profile, weights, is_watchlist=True)
    assert score >= 100
    assert any("+30 housing provided" in r for r in why)
    assert any("+25 stipend included" in r for r in why)
    assert any("+15 abroad in Switzerland" in r for r in why)
    assert any("[urgent]" in r for r in why)

    # Hackathon scoring
    hack_data = {
        "prize_text": "$50,000 cash prizes for winning teams",
        "travel_or_accommodation_covered": "yes",
        "themes": ["AI systems", "cybersecurity"],
        "title": "Global AI & Systems Hackathon",
        "fee": "free",
        "deadline": five_days_away,
    }
    h_score, h_why = compute_hackathon_score(hack_data, is_watchlist=True)
    assert h_score >= 80
    assert any("+20 cash prize pool" in r for r in h_why)
    assert any("+25 travel & stay covered" in r for r in h_why)


def test_watchlist_loading_from_example():
    """Verify programs.example.toml loads correctly with verified programs."""
    programs = load_watchlist_programs()
    assert len(programs) >= 10
    names = [p["name"] for p in programs]
    assert "CERN Summer Student Programme" in names
    assert "Mitacs Globalink Research Internship" in names
    assert "Outreachy Internship" in names
    assert "Devpost Global Hackathons" in names


@pytest.fixture
async def setup_pipeline(tmp_path, monkeypatch):
    test_db = tmp_path / "test_opportunities.db"
    vault_dir = tmp_path / "test_vault"
    monkeypatch.setattr("noxai.db.DB_PATH", test_db)
    monkeypatch.setattr("noxai.db.DATA_DIR", tmp_path)
    monkeypatch.setattr("noxai.briefing.engine.BRIEFINGS_DIR", tmp_path / "briefings")
    monkeypatch.setattr("noxai.briefing.engine.STAGING_DIR", tmp_path / "staging")
    await init_db()

    cfg = get_config()
    cfg.notes.vault_path = str(vault_dir)
    pipeline = BriefingPipeline(config=cfg)
    return pipeline, vault_dir


@pytest.mark.asyncio
async def test_ctf_cap_and_opportunity_quotas(setup_pipeline):
    """Verify that CTFs are hard-capped at ctf_max (<=3) in the briefing analyst stage."""
    pipeline, _ = setup_pipeline
    run_id = "brun_test_quotas"
    today = "2026-11-02"

    async with get_db() as db:
        await execute(
            db,
            """
            INSERT INTO briefing_runs (id, date, slot, state, trigger, rev, title)
            VALUES (?, ?, 'morning', 'running', 'manual', 1, 'Test Run')
            """,
            (run_id, today),
        )

        # Stage 6 CTFs
        for i in range(6):
            ctf_item = CandidateItem(
                item_id=f"ctf_{i}",
                title=f"Security CTF Challenge #{i}",
                url=f"https://ctf.example.org/{i}",
                canonical_url=f"https://ctf.example.org/{i}",
                source_name="CTFtime",
                category="competitions",
                topic_id="topic_ctfs",
                topic_name="CTFs & Security Challenges",
                summary="Exploitation and reverse engineering challenges.",
                relevance_score=1.5,
            )
            await execute(
                db,
                """
                INSERT INTO briefing_staging (run_id, topic_id, item_id, payload_json, status)
                VALUES (?, ?, ?, ?, 'ready')
                """,
                (run_id, "topic_ctfs", f"ctf_{i}", json.dumps(ctf_item.model_dump())),
            )

        # Stage 12 Internships with distinct program names
        names = [
            "CERN Summer", "Mitacs Globalink", "ETH Fellowship", "GSoC Mentorship",
            "Outreachy Program", "LFX Mentorship", "Sakura Science", "IST Austria",
            "RIPS UCLA", "SURF Caltech", "Max Planck Internship", "Weizmann Summer",
        ]
        for i, name in enumerate(names):
            int_item = InternshipItem(
                item_id=f"int_{i}",
                title=f"{name} Engineering Track",
                url=f"https://internships.example.org/{i}",
                canonical_url=f"https://internships.example.org/{i}",
                source_name="Watchlist",
                category="internship",
                topic_id="topic_internships",
                topic_name="Funded Internships",
                summary="Research fellowship with stipend.",
                score=50 + i,
                relevance_score=5.0 + i,
            )
            await execute(
                db,
                """
                INSERT INTO briefing_staging (run_id, topic_id, item_id, payload_json, status)
                VALUES (?, ?, ?, ?, 'ready')
                """,
                (run_id, "topic_internships", f"int_{i}", json.dumps(int_item.model_dump())),
            )
        await db.commit()

    analyst_data = await pipeline._run_analyst(run_id, "morning", today)
    items = analyst_data["items"]

    # 1. CTF cap strictly enforced <= 3
    ctfs_in_briefing = [
        it
        for it in items
        if "ctf" in it.get("topic_id", "").lower() or "ctf" in it.get("title", "").lower()
    ]
    assert len(ctfs_in_briefing) <= 3

    # 2. Internships capped at quota (10)
    internships_in_briefing = [
        it for it in items if (it.get("category") or "").lower() == "internship"
    ]
    assert len(internships_in_briefing) == 10

    # 3. Top Picks: best internship is chosen first
    top_3 = analyst_data["top_3"]
    assert len(top_3) > 0
    assert (top_3[0].get("category") or "").lower() == "internship"
    assert top_3[0]["title"] == "Weizmann Summer Engineering Track"


@pytest.mark.asyncio
async def test_opportunity_registry_status_preservation_and_vault_sync(setup_pipeline):
    """Verify opportunity_registry preserves user status across runs and syncs from vault."""
    _pipeline, vault_dir = setup_pipeline
    test_url = "https://careers.cern/programmes/summer-studentship/"

    async with get_db() as db:
        # User manually applied to CERN
        await execute(
            db,
            """
            INSERT INTO opportunity_registry (
                canonical_url, type, title, org,
                first_seen, last_seen, deadline, status, score, raw_json
            ) VALUES (?, 'internship', 'CERN Summer Student Programme', 'CERN',
                      '2026-11-01', '2026-11-01', '2026-12-01', 'applied', 100, '{}')
            """,
            (test_url,),
        )
        await db.commit()

    # Now pipeline processes a topic worker that re-encounters CERN
    async with get_db() as db:
        # Staging and upsert into registry
        now_iso = datetime.now(UTC).isoformat()
        await execute(
            db,
            """
            INSERT INTO opportunity_registry (
                canonical_url, type, title, org,
                first_seen, last_seen, deadline, status, score, raw_json
            ) VALUES (?, 'internship', 'CERN Summer Student Programme', 'CERN',
                      ?, ?, '2026-12-01', 'new', 105, '{"title": "CERN"}')
            ON CONFLICT(canonical_url) DO UPDATE SET
                last_seen = excluded.last_seen,
                score = excluded.score
            """,
            (test_url, now_iso, now_iso),
        )
        await db.commit()

        # Check status: MUST still be 'applied', not reset to 'new'!
        row = await fetch_one(
            db,
            "SELECT status, score FROM opportunity_registry WHERE canonical_url = ?",
            (test_url,),
        )
        assert row is not None
        assert row["status"] == "applied"
        assert row["score"] == 105

    # Test vault note sync back to registry
    vault = NotesVault(vault_path=vault_dir)
    vault.export_item_note(
        {
            "item_id": "cern_01",
            "title": "CERN Summer Student Programme",
            "category": "internship",
            "url": test_url,
            "apply_url": test_url,
            "status": "interview",  # User updated status in note frontmatter
            "benefits": {"accommodation": "provided", "stipend": "yes"},
        },
        "2026-11-02",
        "morning",
    )

    # Sync vault frontmatter to DB
    async with get_db() as db:
        await vault.sync_vault_to_db(db)
        row_after = await fetch_one(
            db,
            "SELECT status FROM opportunity_registry WHERE canonical_url = ?",
            (test_url,),
        )
        assert row_after is not None
        assert row_after["status"] == "interview"

    # Regenerate dashboards and check files exist
    vault.regenerate_dashboards()
    assert (vault_dir / "Dashboards/Internships Tracker.md").exists()
    assert (vault_dir / "Dashboards/Hackathons.md").exists()
    assert (vault_dir / "Dashboards/Opportunities Board.md").exists()
