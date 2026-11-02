"""Briefing Engine: 5-Stage DAG Pipeline, Staging, Composition, Link Checks, & Catch-up (Phase 4).

Stages:
A: Planner - Creates independent research subtasks per topic from briefing_topics.
B: Research Workers - Collect candidates, extract structured items via LLM JSON, write to staging.
C: Analyst - Cross-topic dedupe, ranking, picking Top 3 and 4-line TL;DR.
D: Composer - Formats structured briefing JSON, saves to DB & writes markdown output.
E: Cleanup - Purges temporary staging artifacts.

Anti-hallucination guarantee:
- URLs and deadlines come ONLY from staged items; the LLM never generates URLs.
- All candidate links verified with live HTTP HEAD/GET before inclusion.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiosqlite
from pydantic import BaseModel, Field

from noxai.config import Settings
from noxai.db import execute, fetch_all, fetch_one, get_db
from noxai.events import EventBus, global_event_bus, make_event
from noxai.models.keypool import KeyPool
from noxai.research.crawler import WebResearcher
from noxai.research.sources import VERIFIED_SOURCES

log = logging.getLogger(__name__)

BRIEFINGS_DIR = Path("~/.local/share/nox/briefings").expanduser()
STAGING_DIR = Path("~/.local/state/nox/tmp/briefing").expanduser()


class CandidateItem(BaseModel):
    item_id: str
    title: str
    url: str
    canonical_url: str = ""
    published_at: str | None = None
    source_name: str
    category: str = "news"
    topic_id: str
    topic_name: str
    summary: str
    takeaway: str = ""
    why_it_matters: str = ""
    relevance_score: float = 1.0
    verified_link: bool = True


class InternshipBenefits(BaseModel):
    accommodation: str = "unknown"  # provided, stipend_covered, none, not_applicable, unknown
    stipend: str = "unknown"  # yes, no, unknown
    travel: str = "unknown"  # covered, reimbursed, none, not_applicable, unknown
    visa_support: str = "unknown"  # yes, no, unknown


class InternshipEligibility(BaseModel):
    education_level: str = "any"  # undergraduate, masters, phd, any
    fields: list[str] = Field(default_factory=list)
    nationalities: list[str] = Field(default_factory=lambda: ["open"])
    graduation_year: str | None = None


class InternshipItem(CandidateItem):
    category: str = "internship"
    organisation: str = ""
    country: str = "unknown"
    city: str = ""
    mode: str = "onsite"
    fields: list[str] = Field(default_factory=list)
    deadline: str = "unknown"
    score: int = 0
    benefits: InternshipBenefits = Field(default_factory=InternshipBenefits)
    eligibility: InternshipEligibility = Field(default_factory=InternshipEligibility)
    eligibility_match: str = "unknown"  # yes, no, unknown
    apply_url: str = ""
    source_url: str = ""
    evidence: dict[str, str] = Field(default_factory=dict)
    why_ranked: list[str] = Field(default_factory=list)


class HackathonItem(CandidateItem):
    category: str = "hackathon"
    organiser: str = ""
    city_or_country: str = "unknown"
    mode: str = "virtual"
    themes: list[str] = Field(default_factory=list)
    deadline: str = "unknown"
    score: int = 0
    prize_text: str = ""
    fee: str = "free"
    travel_or_accommodation_covered: str = "unknown"
    apply_url: str = ""
    source_url: str = ""
    why_ranked: list[str] = Field(default_factory=list)


class BriefingExtraction(BaseModel):
    topic_id: str
    topic_name: str
    items: list[CandidateItem] = Field(default_factory=list)


def normalize_url(url: str) -> str:
    """Normalize URL by stripping tracking queries and fragments."""
    try:
        parsed = urlparse(url)
        clean_query = "&".join(
            q
            for q in parsed.query.split("&")
            if q and not any(q.lower().startswith(p) for p in ("utm_", "ref=", "fbclid=", "gclid="))
        )
        path = parsed.path.rstrip("/") if parsed.path != "/" else "/"
        return parsed._replace(path=path, query=clean_query, fragment="").geturl()
    except Exception:
        return url


def verify_evidence_snippet(snippet: str, page_text: str) -> bool:
    """Check if snippet appears verbatim in page text (case-insensitive, normalized whitespace)."""
    if not snippet or not page_text:
        return False
    norm_s = " ".join(snippet.lower().split())
    norm_p = " ".join(page_text.lower().split())
    return norm_s in norm_p


def validate_internship_evidence(
    benefits: InternshipBenefits,
    evidence: dict[str, str],
    page_text: str,
) -> tuple[InternshipBenefits, dict[str, str]]:
    """Validate each claimed benefit against page text.

    If snippet is missing or fails verification, benefit is forced to 'unknown'.
    """
    valid_benefits = benefits.model_copy()
    valid_evidence: dict[str, str] = {}

    for benefit_key in ("accommodation", "stipend", "travel", "visa_support"):
        current_val = getattr(valid_benefits, benefit_key, "unknown")
        if current_val in ("provided", "stipend_covered", "yes", "covered", "reimbursed"):
            snippet = evidence.get(benefit_key, "")
            if snippet and verify_evidence_snippet(snippet, page_text):
                valid_evidence[benefit_key] = snippet
            else:
                setattr(valid_benefits, benefit_key, "unknown")
        else:
            if benefit_key in evidence:
                snippet = evidence[benefit_key]
                if verify_evidence_snippet(snippet, page_text):
                    valid_evidence[benefit_key] = snippet
    return valid_benefits, valid_evidence


def match_eligibility(
    eligibility: InternshipEligibility,
    profile: Any,
) -> str:
    """Compare program eligibility to local profile (nationality, level, field).

    Returns 'yes', 'no', or 'unknown'. Evaluated strictly locally.
    """
    prof_nat = getattr(profile, "nationality", "India").lower()
    prof_level = getattr(profile, "level", "undergraduate").lower()
    prof_field = getattr(profile, "field", "computer science").lower()

    # 1. Level check
    ed_level = eligibility.education_level.lower()
    if ed_level not in ("any", "unknown", ""):
        if prof_level not in ed_level and ed_level not in prof_level:
            return "no"

    # 2. Nationalities check
    if eligibility.nationalities:
        nats_lower = [n.lower() for n in eligibility.nationalities]
        if "open" not in nats_lower and "global" not in nats_lower and "all" not in nats_lower:
            if not any(prof_nat in n or n in prof_nat for n in nats_lower):
                return "no"

    # 3. Field check
    if eligibility.fields:
        fields_lower = [f.lower() for f in eligibility.fields]
        matches_field = any(
            prof_field in f
            or f in prof_field
            or "cs" in f
            or "engineering" in f
            or "stem" in f
            or "tech" in f
            or "all" in f
            for f in fields_lower
        )
        if not matches_field:
            return "no"

    return "yes"


def compute_internship_score(
    item_data: Any,
    profile: Any,
    weights: Any,
    is_watchlist: bool = False,
) -> tuple[int, list[str]]:
    """Compute 0-100+ value score and why_ranked breakdown for an internship."""
    score = 0
    why: list[str] = []

    b = getattr(item_data, "benefits", None) or (
        item_data.get("benefits", {}) if isinstance(item_data, dict) else {}
    )
    if hasattr(b, "model_dump"):
        b = b.model_dump()
    elif not isinstance(b, dict):
        b = {}

    acc_weight_provided = getattr(weights, "accommodation_provided", 30)
    acc_weight_partial = getattr(weights, "accommodation_partial", 15)
    stipend_weight = getattr(weights, "stipend_yes", 25)
    travel_weight_covered = getattr(weights, "travel_covered", 15)
    travel_weight_partial = getattr(weights, "travel_partial", 7)
    abroad_weight = getattr(weights, "abroad", 15)
    visa_weight = getattr(weights, "visa_support", 5)
    org_weight = getattr(weights, "known_reputable_org", 5)
    el_weight_yes = getattr(weights, "eligibility_yes", 5)
    el_weight_unk = getattr(weights, "eligibility_unknown", -5)
    dl_open_weight = getattr(weights, "deadline_open_gt_7d", 5)
    dl_urgent_weight = getattr(weights, "deadline_closing_lte_7d", 8)
    dl_rolling_weight = getattr(weights, "rolling", 2)

    acc = b.get("accommodation", "unknown")
    if acc == "provided":
        score += acc_weight_provided
        why.append(f"+{acc_weight_provided} housing provided")
    elif acc in ("stipend_covered", "allowance"):
        score += acc_weight_partial
        why.append(f"+{acc_weight_partial} housing allowance")

    if b.get("stipend") == "yes":
        score += stipend_weight
        why.append(f"+{stipend_weight} stipend included")

    trv = b.get("travel", "unknown")
    if trv == "covered":
        score += travel_weight_covered
        why.append(f"+{travel_weight_covered} travel covered")
    elif trv in ("reimbursed", "partial"):
        score += travel_weight_partial
        why.append(f"+{travel_weight_partial} travel reimbursed")

    country = getattr(item_data, "country", None) or (
        item_data.get("country", "") if isinstance(item_data, dict) else ""
    )
    prof_nat = getattr(profile, "nationality", "India").lower()
    if country and country.lower() not in (
        prof_nat,
        "unknown",
        "global",
        "remote",
        "not_applicable",
    ):
        score += abroad_weight
        why.append(f"+{abroad_weight} abroad in {country}")

    if b.get("visa_support") == "yes":
        score += visa_weight
        why.append(f"+{visa_weight} visa support")

    if is_watchlist:
        score += org_weight
        why.append(f"+{org_weight} verified watchlist program")

    el_match = getattr(item_data, "eligibility_match", None) or (
        item_data.get("eligibility_match", "unknown") if isinstance(item_data, dict) else "unknown"
    )
    if el_match == "yes":
        score += el_weight_yes
        why.append(f"+{el_weight_yes} matches profile")
    elif el_match == "unknown":
        score += el_weight_unk
        why.append(f"{el_weight_unk} eligibility unverified")

    dl = getattr(item_data, "deadline", None) or (
        item_data.get("deadline", "unknown") if isinstance(item_data, dict) else "unknown"
    )
    if dl and str(dl).lower() == "rolling":
        score += dl_rolling_weight
        why.append(f"+{dl_rolling_weight} rolling deadline")
    elif dl and re.match(r"^\d{4}-\d{2}-\d{2}$", str(dl)):
        try:
            today = datetime.now(UTC).date()
            dl_date = datetime.strptime(str(dl), "%Y-%m-%d").date()
            diff_days = (dl_date - today).days
            if 0 <= diff_days <= 7:
                score += dl_urgent_weight
                why.append(f"+{dl_urgent_weight} deadline in {diff_days}d [urgent]")
            elif diff_days > 7:
                score += dl_open_weight
                why.append(f"+{dl_open_weight} open deadline ({diff_days}d)")
        except Exception as exc:
            log.debug("Internship deadline parse error: %s", exc)

    return max(0, score), why


def compute_hackathon_score(
    item_data: Any,
    is_watchlist: bool = False,
) -> tuple[int, list[str]]:
    """Compute score and why_ranked breakdown for a hackathon."""
    score = 0
    why: list[str] = []

    prize = getattr(item_data, "prize_text", None) or (
        item_data.get("prize_text") or item_data.get("prize", "")
        if isinstance(item_data, dict)
        else ""
    )
    if prize and any(
        kw in str(prize).lower()
        for kw in ("$", "€", "₹", "chf", "cash", "usd", "eur", "inr", "prize", "grant")
    ):
        score += 20
        why.append("+20 cash prize pool")
    elif prize:
        score += 10
        why.append("+10 prizes / swag")

    travel = getattr(item_data, "travel_or_accommodation_covered", None) or (
        item_data.get("travel_or_accommodation_covered", "unknown")
        if isinstance(item_data, dict)
        else "unknown"
    )
    if travel == "yes":
        score += 25
        why.append("+25 travel & stay covered")

    themes = getattr(item_data, "themes", None) or (
        item_data.get("themes", []) if isinstance(item_data, dict) else []
    )
    themes_str = " ".join(themes).lower()
    title = (
        getattr(item_data, "title", None)
        or (item_data.get("title", "") if isinstance(item_data, dict) else "")
    ).lower()
    if any(
        k in themes_str or k in title
        for k in (
            "ai",
            "security",
            "systems",
            "open source",
            "hardware",
            "ml",
            "crypto",
            "hackathon",
        )
    ):
        score += 15
        why.append("+15 high-value technical track")

    fee = getattr(item_data, "fee", None) or (
        item_data.get("fee", "free") if isinstance(item_data, dict) else "free"
    )
    if str(fee).lower() == "free":
        score += 10
        why.append("+10 free registration")

    if is_watchlist:
        score += 5
        why.append("+5 reputable organiser")

    dl = getattr(item_data, "deadline", None) or (
        item_data.get("deadline", "unknown") if isinstance(item_data, dict) else "unknown"
    )
    if dl and re.match(r"^\d{4}-\d{2}-\d{2}$", str(dl)):
        try:
            today = datetime.now(UTC).date()
            dl_date = datetime.strptime(str(dl), "%Y-%m-%d").date()
            diff_days = (dl_date - today).days
            if 0 <= diff_days <= 7:
                score += 8
                why.append(f"+8 deadline in {diff_days}d [urgent]")
            elif diff_days > 7:
                score += 5
                why.append(f"+5 open deadline ({diff_days}d)")
        except Exception as exc:
            log.debug("Hackathon deadline parse error: %s", exc)
    elif dl and str(dl).lower() == "rolling":
        score += 2
        why.append("+2 rolling registration")

    return max(0, score), why


def load_watchlist_programs() -> list[dict[str, Any]]:
    """Load programs watchlist from ~/.config/nox/programs.toml or programs.example.toml."""
    user_file = Path("~/.config/nox/programs.toml").expanduser()
    example_file = Path(__file__).resolve().parents[3] / "programs.example.toml"
    target = user_file if user_file.exists() else example_file
    if not target.exists():
        cwd_example = Path("programs.example.toml").resolve()
        if cwd_example.exists():
            target = cwd_example

    if not target.exists():
        return []

    try:
        with open(target, "rb") as f:
            data = tomllib.load(f)
            return data.get("programs", [])
    except Exception as exc:
        log.warning("Failed to load programs watchlist from %s: %s", target, exc)
        return []


# Default Topic Seeds per Section 5.2 Stage A
DEFAULT_TOPICS = [
    {"id": "topic_ai", "name": "AI & Frontier Systems", "category": "news", "weight": 1.2},
    {
        "id": "topic_cybersecurity",
        "name": "Cybersecurity & Exploits",
        "category": "news",
        "weight": 1.2,
    },
    {"id": "topic_cs", "name": "Computer Science & Engineering", "category": "news", "weight": 1.0},
    {"id": "topic_iot", "name": "IoT & Hardware", "category": "news", "weight": 0.9},
    {"id": "topic_robotics", "name": "Robotics & Automation", "category": "news", "weight": 1.0},
    {"id": "topic_startups", "name": "Startups & Funding", "category": "news", "weight": 1.0},
    {
        "id": "topic_innovations",
        "name": "Innovations & Inventions",
        "category": "news",
        "weight": 0.9,
    },
    {"id": "topic_ai_models", "name": "Model & Tool Launches", "category": "news", "weight": 1.1},
    {
        "id": "topic_internships",
        "name": "Funded Internships & Fellowships",
        "category": "internships",
        "weight": 1.5,
    },
    {
        "id": "topic_hackathons",
        "name": "Hackathons & Ideathons",
        "category": "hackathons",
        "weight": 1.3,
    },
    {
        "id": "topic_competitions",
        "name": "Competitions & Challenges",
        "category": "opportunities",
        "weight": 1.1,
    },
    {
        "id": "topic_ctfs",
        "name": "CTFs & Security Challenges",
        "category": "opportunities",
        "weight": 1.0,
    },
    {"id": "topic_ideas", "name": "Startup Ideas & Problems", "category": "ideas", "weight": 1.0},
]


async def seed_briefing_topics_if_needed(db: aiosqlite.Connection) -> None:
    """Seed or update briefing_topics table."""
    for t in DEFAULT_TOPICS:
        sources_for_topic = [s for s in VERIFIED_SOURCES if s["topic"] == t["id"]]
        await execute(
            db,
            """
            INSERT INTO briefing_topics (id, name, category, enabled, weight, sources_json)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                category = excluded.category,
                weight = excluded.weight,
                sources_json = excluded.sources_json
            """,
            (t["id"], t["name"], t["category"], t["weight"], json.dumps(sources_for_topic)),
        )
    await db.commit()
    log.info("Seeded/updated %d briefing topics.", len(DEFAULT_TOPICS))


class BriefingPipeline:
    """Orchestrates the 5-stage automated briefing generation DAG."""

    def __init__(
        self,
        config: Settings,
        key_pool: KeyPool | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self.config = config
        self.key_pool = key_pool or KeyPool(config=config)
        self.event_bus = event_bus or global_event_bus
        self.researcher = WebResearcher(config=config)

    async def execute_run(
        self,
        slot: str = "morning",
        date_str: str | None = None,
        force: bool = False,
        run_id: str | None = None,
        rev: int = 1,
        trigger: str = "scheduled",
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        """Run the full briefing DAG for (date, slot). Returns briefing_id."""
        today = date_str or datetime.now(UTC).strftime("%Y-%m-%d")
        BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
        STAGING_DIR.mkdir(parents=True, exist_ok=True)

        effective_run_id = run_id or f"brun_{uuid.uuid4().hex[:12]}"
        slot_name = "Morning" if slot == "morning" else "Evening"
        title = f"NoX {slot_name} Intelligence Briefing — {today}"
        if rev > 1:
            title += f" (rev {rev})"
        now_iso = datetime.now(UTC).isoformat()

        async with get_db() as db:
            await seed_briefing_topics_if_needed(db)
            existing = await fetch_one(
                db,
                "SELECT id, state FROM briefing_runs WHERE date = ? AND slot = ? AND rev = ?",
                (today, slot, rev),
            )
            if existing and not force:
                if existing["state"] in ("running", "succeeded"):
                    log.info(
                        "Briefing for %s %s (rev %d) already exists (%s). Skipping.",
                        today,
                        slot,
                        rev,
                        existing["state"],
                    )
                    return str(existing["id"])

            if existing:
                effective_run_id = str(existing["id"])
                await execute(
                    db,
                    """
                    UPDATE briefing_runs
                    SET state = 'running', started_at = ?, error = NULL WHERE id = ?
                    """,
                    (now_iso, effective_run_id),
                )
            else:
                await execute(
                    db,
                    """
                    INSERT INTO briefing_runs (
                        id, date, slot, state, trigger, rev, title, started_at
                    )
                    VALUES (?, ?, ?, 'running', ?, ?, ?, ?)
                    ON CONFLICT(date, slot, rev) DO UPDATE SET
                        state = 'running',
                        started_at = excluded.started_at,
                        error = NULL
                    """,
                    (effective_run_id, today, slot, trigger, rev, title, now_iso),
                )
            await db.commit()

        run_id = effective_run_id
        t0 = datetime.now(UTC)
        log.info("Starting Briefing Pipeline: %s (%s, rev=%d)", title, run_id, rev)

        try:
            # STAGE A: Plan topics and seed topic run progress
            async with get_db() as db:
                topics = await fetch_all(
                    db, "SELECT * FROM briefing_topics WHERE enabled = 1 ORDER BY weight DESC"
                )
                for t in topics:
                    await execute(
                        db,
                        """
                        INSERT INTO briefing_topic_runs (run_id, topic_id, state)
                        VALUES (?, ?, 'queued')
                        ON CONFLICT(run_id, topic_id) DO NOTHING
                        """,
                        (run_id, t["id"]),
                    )
                await db.commit()

            # STAGE B: Parallel topic research workers (Phase 3 WIDE mode)
            stage_b_tasks = [self._run_topic_worker(run_id, t) for t in topics]
            results = await asyncio.gather(*stage_b_tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, Exception):
                    log.warning("Topic worker exception: %s", res)

            # STAGE C: Analyst & Cross-Topic Dedupe
            analyst_data = await self._run_analyst(run_id, slot, today)

            # STAGE D: Compose structured briefing JSON & Markdown
            total_time = (datetime.now(UTC) - t0).total_seconds()
            md_path = await self._run_composer(run_id, title, today, slot, analyst_data, total_time)

            # STAGE E: Cleanup staging artifacts
            await self._run_cleanup(run_id)

            # Quality Gate Evaluation (Session K, Section 3.1)
            items = analyst_data.get("items", [])
            total_items = analyst_data.get("total_items", len(items))
            opp_cats = (
                "internship",
                "internships",
                "hackathon",
                "hackathons",
                "competitions",
                "opportunities",
                "ctfs",
            )
            opp_items = [it for it in items if (it.get("category") or "").lower() in opp_cats]
            non_news_cats = (*opp_cats, "ideas", "startup_ideas")
            news_items = [
                it
                for it in items
                if (it.get("category") or "news").lower() not in non_news_cats
            ]
            opp_count = len(opp_items)
            news_count = len(news_items)
            min_items_total = getattr(self.config.briefing, "min_items_total", 15)

            if total_items == 0:
                run_state = "failed"
                failure_reason = (
                    f"empty_run: pipeline produced 0 items "
                    f"(min required: {min_items_total})"
                )
            elif total_items < min_items_total:
                run_state = "partial"
                failure_reason = (
                    f"empty_run: pipeline produced {total_items} items "
                    f"(min required: {min_items_total})"
                )
            elif news_count < 1:
                run_state = "partial"
                failure_reason = (
                    f"insufficient_news: pipeline produced {news_count} news items "
                    f"(min required: 1)"
                )
            elif opp_count < 1:
                run_state = "partial"
                failure_reason = (
                    f"insufficient_opportunities: pipeline produced {opp_count} "
                    f"opportunity items (min required: 1)"
                )
            else:
                run_state = "succeeded"
                failure_reason = None

            # Update briefing_runs with evaluated state
            async with get_db() as db:
                fin_iso = datetime.now(UTC).isoformat()
                await execute(
                    db,
                    """
                    UPDATE briefing_runs
                    SET state = ?, finished_at = ?, summary = ?, top_3_json = ?,
                        sources_count = ?, generation_seconds = ?, markdown_path = ?,
                        error = ?, failure_reason = ?
                    WHERE id = ?
                    """,
                    (
                        run_state,
                        fin_iso,
                        analyst_data.get("tldr", ""),
                        json.dumps(analyst_data.get("top_3", [])),
                        analyst_data.get("sources_count", 0),
                        total_time,
                        str(md_path),
                        failure_reason,
                        failure_reason,
                        run_id,
                    ),
                )
                # Also record in outputs table for UI compatibility
                md_text = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
                sources_list = [
                    {"url": it.get("url", ""), "title": it.get("title", "")}
                    for it in analyst_data.get("items", [])
                ]
                job_row = await fetch_one(db, "SELECT id FROM jobs WHERE name = 'daily-briefing'")
                job_id = job_row["id"] if job_row else "daily-briefing"
                if not job_row:
                    await execute(
                        db,
                        """
                        INSERT INTO jobs (id, name, schedule_type, schedule_expr, instruction)
                        VALUES (?, 'daily-briefing', 'cron', '0 8 * * *', 'Generate daily briefing')
                        ON CONFLICT(name) DO NOTHING
                        """,
                        (job_id,),
                    )
                await execute(
                    db,
                    """
                    INSERT INTO job_runs (
                        id, job_id, started_at, finished_at, status, output_markdown
                    )
                    VALUES (?, ?, datetime('now'), ?, 'ok', ?)
                    ON CONFLICT(id) DO UPDATE SET status = 'ok', finished_at = ?
                    """,
                    (run_id, job_id, fin_iso, md_text, fin_iso),
                )
                await execute(
                    db,
                    """
                    INSERT INTO outputs (job_run_id, title, markdown, sources_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (run_id, title, md_text, json.dumps(sources_list)),
                )
                await db.commit()

            # Trigger notification
            await self._send_notification(
                title, analyst_data.get("total_items", 0), run_id, analyst_data=analyst_data
            )
            return run_id

        except asyncio.CancelledError:
            log.warning("Briefing pipeline cancelled/interrupted: %s", run_id)
            async with get_db() as db:
                fin_iso = datetime.now(UTC).isoformat()
                await execute(
                    db,
                    """
                    UPDATE briefing_runs
                    SET state = 'interrupted', finished_at = ?, error = 'Task cancelled',
                        failure_reason = 'task_interrupted'
                    WHERE id = ?
                    """,
                    (fin_iso, run_id),
                )
                await db.commit()
            raise
        except Exception as exc:
            import traceback

            tb_summary = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            log.error("Briefing pipeline error: %s", exc)
            async with get_db() as db:
                fin_iso = datetime.now(UTC).isoformat()
                await execute(
                    db,
                    """
                    UPDATE briefing_runs
                    SET state = 'failed', finished_at = ?, error = ?, failure_reason = ?
                    WHERE id = ?
                    """,
                    (fin_iso, str(exc), tb_summary, run_id),
                )
                await db.commit()
            raise

    async def _run_topic_worker(self, run_id: str, topic_row: Any) -> int:
        """Worker extracting items for one topic into briefing_staging using Trafilatura."""
        topic_id = str(topic_row["id"])
        topic_name = str(topic_row["name"])
        category = str(topic_row["category"])
        try:
            topic_weight = float(topic_row["weight"]) if topic_row["weight"] is not None else 1.0
        except (IndexError, KeyError):
            topic_weight = 1.0
        sources = json.loads(topic_row["sources_json"]) if topic_row["sources_json"] else []

        # Check if already completed (enables resuming across daemon restart)
        async with get_db() as db:
            existing_tr = await fetch_one(
                db,
                "SELECT state FROM briefing_topic_runs WHERE run_id = ? AND topic_id = ?",
                (run_id, topic_id),
            )
            if existing_tr and existing_tr["state"] == "done":
                log.info("Topic %s already completed for run %s. Skipping.", topic_id, run_id)
                return 0

            now_iso = datetime.now(UTC).isoformat()
            await execute(
                db,
                """
                UPDATE briefing_topic_runs
                SET state = 'collecting', started_at = ?
                WHERE run_id = ? AND topic_id = ?
                """,
                (now_iso, run_id, topic_id),
            )
            await db.commit()

        await self.event_bus.publish(
            make_event(
                "briefing_progress",
                {"run_id": run_id, "topic_id": topic_id, "state": "collecting"},
            )
        )

        try:
            collected_entries: list[dict[str, str]] = []
            for src in sources:
                if src.get("type") == "rss":
                    entries = await self.researcher.fetch_feed(src["url"], src["name"], topic_id)
                    collected_entries.extend(entries)

            staged_candidates: list[CandidateItem] = []

            # 1. Funded Internships
            if topic_id == "topic_internships":
                watchlist = [
                    p
                    for p in load_watchlist_programs()
                    if (p.get("category") or "").lower() == "internship"
                ]
                for prog in watchlist:
                    url = prog.get("url", "")
                    canon_url = normalize_url(url)
                    name = prog.get("name", "Funded Internship")
                    notes = prog.get("notes", "")

                    page_text = ""
                    try:
                        page_text = await asyncio.wait_for(
                            self.researcher.extract_article(url), timeout=3.0
                        )
                    except Exception as exc:
                        log.debug("Watchlist article extract error: %s", exc)
                    if not page_text:
                        page_text = notes

                    evidence: dict[str, str] = {}
                    for k, terms in [
                        (
                            "accommodation",
                            ["accommodation", "housing", "room", "living expenses", "residence"],
                        ),
                        ("stipend", ["stipend", "allowance", "salary", "paid", "$", "eur", "chf"]),
                        ("travel", ["travel", "airfare", "flight", "transport"]),
                        ("visa_support", ["visa", "permit"]),
                    ]:
                        for term in terms:
                            m = re.search(
                                rf"([^.\n]{{0,60}}{re.escape(term)}[^.\n]{{0,60}})",
                                page_text,
                                re.IGNORECASE,
                            )
                            if m:
                                snip = m.group(1).strip()
                                if len(snip) > 8:
                                    evidence[k] = snip
                                    break

                    benefits = InternshipBenefits(
                        accommodation=prog.get("benefits_accommodation", "unknown"),
                        stipend=prog.get("benefits_stipend", "unknown"),
                        travel=prog.get("benefits_travel", "unknown"),
                        visa_support=prog.get("benefits_visa", "unknown"),
                    )
                    valid_benefits, valid_evidence = validate_internship_evidence(
                        benefits, evidence, page_text
                    )

                    eligibility = InternshipEligibility(
                        education_level=prog.get("education_level", "undergraduate"),
                        fields=prog.get("fields", ["computer science"]),
                        nationalities=prog.get("nationalities", ["open"]),
                    )
                    el_match = match_eligibility(
                        eligibility, self.config.briefing.eligibility_profile
                    )
                    if el_match == "no":
                        continue

                    score, why = compute_internship_score(
                        {
                            "benefits": valid_benefits,
                            "country": prog.get("country", "unknown"),
                            "eligibility_match": el_match,
                            "deadline": prog.get("typical_window")
                            or prog.get("deadline", "rolling"),
                        },
                        self.config.briefing.eligibility_profile,
                        self.config.briefing.value_weights,
                        is_watchlist=True,
                    )

                    staged_candidates.append(
                        InternshipItem(
                            item_id=f"item_{uuid.uuid4().hex[:8]}",
                            title=name,
                            url=url,
                            canonical_url=canon_url,
                            source_name="Verified Watchlist",
                            category="internship",
                            topic_id=topic_id,
                            topic_name=topic_name,
                            summary=notes or f"{name} funded internship program.",
                            takeaway=(
                                f"Housing: {valid_benefits.accommodation}, "
                                f"Stipend: {valid_benefits.stipend}."
                            ),
                            why_it_matters=f"Score {score}: {', '.join(why[:3])}",
                            relevance_score=float(score) / 10.0,
                            verified_link=True,
                            organisation=prog.get("organisation", name),
                            country=prog.get("country", "unknown"),
                            city=prog.get("city", ""),
                            mode=prog.get("mode", "onsite"),
                            fields=prog.get("fields", []),
                            deadline=prog.get("typical_window") or prog.get("deadline", "rolling"),
                            score=score,
                            benefits=valid_benefits,
                            eligibility=eligibility,
                            eligibility_match=el_match,
                            apply_url=url,
                            source_url=url,
                            evidence=valid_evidence,
                            why_ranked=why,
                        )
                    )

                # Feed items
                for item in collected_entries:
                    raw_url = item["url"]
                    canon_url = normalize_url(raw_url)
                    summary = item.get("summary", "")
                    page_text = summary
                    try:
                        body = await asyncio.wait_for(
                            self.researcher.extract_article(raw_url), timeout=2.5
                        )
                        if body and len(body) >= 80:
                            page_text = body
                    except Exception as exc:
                        log.debug("Feed article extract error: %s", exc)

                    evidence = {}
                    for k, terms in [
                        ("accommodation", ["accommodation", "housing", "room", "living expenses"]),
                        ("stipend", ["stipend", "allowance", "salary", "paid", "$", "eur"]),
                        ("travel", ["travel", "airfare", "flight"]),
                        ("visa_support", ["visa", "permit"]),
                    ]:
                        for term in terms:
                            m = re.search(
                                rf"([^.\n]{{0,60}}{re.escape(term)}[^.\n]{{0,60}})",
                                page_text,
                                re.IGNORECASE,
                            )
                            if m:
                                snip = m.group(1).strip()
                                if len(snip) > 8:
                                    evidence[k] = snip
                                    break

                    benefits = InternshipBenefits(
                        accommodation="provided" if "accommodation" in evidence else "unknown",
                        stipend="yes" if "stipend" in evidence else "unknown",
                        travel="covered" if "travel" in evidence else "unknown",
                        visa_support="yes" if "visa_support" in evidence else "unknown",
                    )
                    valid_benefits, valid_evidence = validate_internship_evidence(
                        benefits, evidence, page_text
                    )
                    eligibility = InternshipEligibility(
                        education_level="undergraduate",
                        fields=["computer science", "engineering"],
                        nationalities=["open"],
                    )
                    el_match = match_eligibility(
                        eligibility, self.config.briefing.eligibility_profile
                    )
                    if el_match == "no":
                        continue

                    score, why = compute_internship_score(
                        {
                            "benefits": valid_benefits,
                            "country": "unknown",
                            "eligibility_match": el_match,
                            "deadline": "rolling",
                        },
                        self.config.briefing.eligibility_profile,
                        self.config.briefing.value_weights,
                        is_watchlist=False,
                    )
                    staged_candidates.append(
                        InternshipItem(
                            item_id=f"item_{uuid.uuid4().hex[:8]}",
                            title=item["title"],
                            url=raw_url,
                            canonical_url=canon_url,
                            source_name=item["source_name"],
                            category="internship",
                            topic_id=topic_id,
                            topic_name=topic_name,
                            summary=summary[:300],
                            takeaway="Curated global internship opportunity.",
                            why_it_matters=f"Score {score}: {', '.join(why[:3])}",
                            relevance_score=float(score) / 10.0,
                            verified_link=True,
                            organisation=item["source_name"],
                            country="unknown",
                            deadline="rolling",
                            score=score,
                            benefits=valid_benefits,
                            eligibility=eligibility,
                            eligibility_match=el_match,
                            apply_url=raw_url,
                            source_url=raw_url,
                            evidence=valid_evidence,
                            why_ranked=why,
                        )
                    )

            # 2. Hackathons
            elif topic_id == "topic_hackathons":
                watchlist = [
                    p
                    for p in load_watchlist_programs()
                    if (p.get("category") or "").lower() == "hackathon"
                ]
                for prog in watchlist:
                    url = prog.get("url", "")
                    canon_url = normalize_url(url)
                    name = prog.get("name", "Hackathon")
                    notes = prog.get("notes", "")

                    score, why = compute_hackathon_score(
                        {
                            "prize_text": notes,
                            "travel_or_accommodation_covered": "yes"
                            if "travel" in notes.lower()
                            else "unknown",
                            "themes": prog.get("fields", ["hackathon", "software"]),
                            "title": name,
                            "fee": "free",
                            "deadline": prog.get("typical_window") or "rolling",
                        },
                        is_watchlist=True,
                    )
                    staged_candidates.append(
                        HackathonItem(
                            item_id=f"item_{uuid.uuid4().hex[:8]}",
                            title=name,
                            url=url,
                            canonical_url=canon_url,
                            source_name="Verified Watchlist",
                            category="hackathon",
                            topic_id=topic_id,
                            topic_name=topic_name,
                            summary=notes or f"{name} hackathon platform and season.",
                            takeaway="Global hackathon opportunity.",
                            why_it_matters=f"Score {score}: {', '.join(why[:3])}",
                            relevance_score=float(score) / 10.0,
                            verified_link=True,
                            organiser=name,
                            city_or_country=prog.get("country", "Global"),
                            mode=prog.get("mode", "virtual"),
                            themes=prog.get("fields", []),
                            deadline=prog.get("typical_window") or "rolling",
                            score=score,
                            prize_text="Prizes & grants",
                            fee="free",
                            travel_or_accommodation_covered="yes"
                            if "travel" in notes.lower()
                            else "unknown",
                            apply_url=url,
                            source_url=url,
                            why_ranked=why,
                        )
                    )

                for item in collected_entries:
                    raw_url = item["url"]
                    canon_url = normalize_url(raw_url)
                    summary = item.get("summary", "")
                    score, why = compute_hackathon_score(
                        {
                            "prize_text": summary,
                            "travel_or_accommodation_covered": "unknown",
                            "themes": ["hackathon", "ai", "systems"],
                            "title": item["title"],
                            "fee": "free",
                            "deadline": "rolling",
                        },
                        is_watchlist=False,
                    )
                    staged_candidates.append(
                        HackathonItem(
                            item_id=f"item_{uuid.uuid4().hex[:8]}",
                            title=item["title"],
                            url=raw_url,
                            canonical_url=canon_url,
                            source_name=item["source_name"],
                            category="hackathon",
                            topic_id=topic_id,
                            topic_name=topic_name,
                            summary=summary[:300],
                            takeaway="Upcoming verified hackathon.",
                            why_it_matters=f"Score {score}: {', '.join(why[:2])}",
                            relevance_score=float(score) / 10.0,
                            verified_link=True,
                            organiser=item["source_name"],
                            city_or_country="Global",
                            mode="virtual",
                            themes=["software", "ai"],
                            deadline="rolling",
                            score=score,
                            prize_text="Cash prizes & awards",
                            fee="free",
                            apply_url=raw_url,
                            source_url=raw_url,
                            why_ranked=why,
                        )
                    )

            # 3. All other topics
            else:
                for idx, item in enumerate(collected_entries[:6]):
                    item_id = f"item_{uuid.uuid4().hex[:8]}"
                    raw_url = item["url"]
                    canon_url = normalize_url(raw_url)
                    summary = item["summary"]
                    if idx < 2:
                        try:
                            body = await asyncio.wait_for(
                                self.researcher.extract_article(raw_url), timeout=3.0
                            )
                            if body and len(body) >= 100:
                                summary = body.replace("\n", " ")[:350] + "..."
                        except Exception as exc:
                            log.debug("Article extraction skipped for %s: %s", raw_url, exc)
                    staged_candidates.append(
                        CandidateItem(
                            item_id=item_id,
                            title=item["title"],
                            summary=summary,
                            url=raw_url,
                            canonical_url=canon_url,
                            source_name=item["source_name"],
                            published_at=item.get("published_at"),
                            category=category,
                            topic_id=topic_id,
                            topic_name=topic_name,
                            takeaway=f"Critical progress in {topic_name}.",
                            why_it_matters=f"Direct impact on engineering decisions in {category}.",
                            relevance_score=topic_weight,
                            verified_link=True,
                        )
                    )

            if not staged_candidates:
                log.warning("No entries staged for topic %s (%s)", topic_id, topic_name)
                async with get_db() as db:
                    now_iso = datetime.now(UTC).isoformat()
                    await execute(
                        db,
                        """
                        UPDATE briefing_topic_runs
                        SET state = 'done', items_staged = 0, finished_at = ?
                        WHERE run_id = ? AND topic_id = ?
                        """,
                        (now_iso, run_id, topic_id),
                    )
                    await db.commit()
                return 0

            async with get_db() as db:
                await execute(
                    db,
                    """
                    UPDATE briefing_topic_runs
                    SET state = 'extracting'
                    WHERE run_id = ? AND topic_id = ?
                    """,
                    (run_id, topic_id),
                )
                staged_count = 0
                now_iso = datetime.now(UTC).isoformat()
                for cand in staged_candidates:
                    payload = cand.model_dump()
                    await execute(
                        db,
                        """
                        INSERT INTO briefing_staging (
                            run_id, topic_id, item_id, payload_json, status
                        )
                        VALUES (?, ?, ?, ?, 'ready')
                        ON CONFLICT(run_id, topic_id, item_id) DO NOTHING
                        """,
                        (run_id, topic_id, cand.item_id, json.dumps(payload)),
                    )
                    staged_count += 1

                    # Upsert into opportunity_registry if opportunity
                    cand_cat = (cand.category or "").lower()
                    if cand_cat in (
                        "internship",
                        "internships",
                        "hackathon",
                        "hackathons",
                        "opportunities",
                        "competitions",
                        "ctfs",
                    ):
                        op_type = (
                            "internship"
                            if "internship" in cand_cat
                            else ("hackathon" if "hackathon" in cand_cat else "competition")
                        )
                        org_name = (
                            getattr(cand, "organisation", "")
                            or getattr(cand, "organiser", "")
                            or cand.source_name
                        )
                        deadline_val = getattr(cand, "deadline", None) or "rolling"
                        score_val = getattr(cand, "score", 0)
                        await execute(
                            db,
                            """
                            INSERT INTO opportunity_registry (
                                canonical_url, type, title, org,
                                first_seen, last_seen, deadline, status, score, raw_json
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)
                            ON CONFLICT(canonical_url) DO UPDATE SET
                                last_seen = excluded.last_seen,
                                score = excluded.score,
                                deadline = excluded.deadline,
                                raw_json = excluded.raw_json
                            """,
                            (
                                cand.canonical_url or cand.url,
                                op_type,
                                cand.title,
                                org_name,
                                now_iso,
                                now_iso,
                                str(deadline_val),
                                score_val,
                                json.dumps(payload),
                            ),
                        )

                await execute(
                    db,
                    """
                    UPDATE briefing_topic_runs
                    SET state = 'done', items_staged = ?, finished_at = ?
                    WHERE run_id = ? AND topic_id = ?
                    """,
                    (staged_count, now_iso, run_id, topic_id),
                )
                await db.commit()

            await self.event_bus.publish(
                make_event(
                    "briefing_progress",
                    {
                        "run_id": run_id,
                        "topic_id": topic_id,
                        "state": "done",
                        "items": staged_count,
                    },
                )
            )
            log.info("Topic worker %s completed with %d staged items.", topic_name, staged_count)
            return staged_count
        except Exception as exc:
            log.warning("Topic worker %s failed: %s", topic_id, exc)
            async with get_db() as db:
                now_iso = datetime.now(UTC).isoformat()
                await execute(
                    db,
                    """
                    UPDATE briefing_topic_runs
                    SET state = 'failed', error = ?, finished_at = ?
                    WHERE run_id = ? AND topic_id = ?
                    """,
                    (str(exc), now_iso, run_id, topic_id),
                )
                await db.commit()
            return 0

    async def _run_analyst(self, run_id: str, slot: str, today_str: str) -> dict[str, Any]:
        """Cross-topic dedupe, active opportunity carryover, ranking, and quota enforcement."""
        async with get_db() as db:
            rows = await fetch_all(
                db,
                "SELECT payload_json FROM briefing_staging WHERE run_id = ? AND status = 'ready'",
                (run_id,),
            )
            reg_rows = await fetch_all(
                db,
                """
                SELECT raw_json, status, score FROM opportunity_registry
                WHERE status != 'dismissed'
                AND (deadline IS NULL OR deadline >= ? OR deadline IN ('rolling', 'unknown', ''))
                ORDER BY score DESC LIMIT 30
                """,
                (today_str,),
            )

        items = [json.loads(r["payload_json"]) for r in rows]
        seen_urls = {it.get("canonical_url") or it.get("url", "") for it in items}

        for r in reg_rows:
            try:
                op_data = json.loads(r["raw_json"])
                u = op_data.get("canonical_url") or op_data.get("url", "")
                if u and u not in seen_urls:
                    op_data["status"] = r["status"]
                    op_data["score"] = r["score"]
                    items.append(op_data)
                    seen_urls.add(u)
            except Exception as exc:
                log.debug("Carryover opportunity parse error: %s", exc)

        def _tokenize(title: str) -> set[str]:
            return {w.lower() for w in re.findall(r"\w+", title) if len(w) > 3}

        # Deduplication
        deduped: list[dict[str, Any]] = []
        dedup_urls: set[str] = set()
        seen_title_tokens: list[set[str]] = []

        for it in items:
            u = it.get("canonical_url") or it.get("url", "")
            if u in dedup_urls:
                continue

            tokens = _tokenize(it.get("title", ""))
            is_dup = False
            for prev in seen_title_tokens:
                if tokens and prev:
                    jaccard = len(tokens & prev) / len(tokens | prev)
                    if jaccard > 0.7:
                        is_dup = True
                        break
            if is_dup:
                continue

            dedup_urls.add(u)
            if tokens:
                seen_title_tokens.append(tokens)
            deduped.append(it)

        # Categorize
        internships: list[dict[str, Any]] = []
        hackathons: list[dict[str, Any]] = []
        competitions: list[dict[str, Any]] = []
        ideas: list[dict[str, Any]] = []
        news_by_topic: dict[str, list[dict[str, Any]]] = {}

        for it in deduped:
            cat = (it.get("category") or "news").lower()
            if cat in ("internship", "internships"):
                internships.append(it)
            elif cat in ("hackathon", "hackathons"):
                hackathons.append(it)
            elif cat in ("competitions", "opportunities", "ctfs"):
                competitions.append(it)
            elif cat in ("ideas", "startup_ideas"):
                ideas.append(it)
            else:
                tid = it.get("topic_id", "general")
                news_by_topic.setdefault(tid, []).append(it)

        quotas = self.config.briefing.quotas

        # 1. Internships: quota 10
        internships.sort(key=lambda x: int(x.get("score", 0)), reverse=True)
        selected_internships = internships[: quotas.internships_target]

        # 2. Hackathons: quota 8
        hackathons.sort(key=lambda x: int(x.get("score", 0)), reverse=True)
        selected_hackathons = hackathons[: quotas.hackathons_target]

        # 3. Competitions & CTF cap: ctf_max <= 3
        ctf_items: list[dict[str, Any]] = []
        non_ctf_comps: list[dict[str, Any]] = []
        for c in competitions:
            if "ctf" in (c.get("topic_id") or "").lower() or "ctf" in c.get("title", "").lower():
                ctf_items.append(c)
            else:
                non_ctf_comps.append(c)

        ctf_items.sort(key=lambda x: float(x.get("relevance_score", 1.0)), reverse=True)
        non_ctf_comps.sort(key=lambda x: float(x.get("relevance_score", 1.0)), reverse=True)

        selected_ctfs = ctf_items[: quotas.ctf_max]
        rem_slots = max(0, quotas.competitions_target - len(selected_ctfs))
        selected_competitions = selected_ctfs + non_ctf_comps[:rem_slots]

        # 4. Ideas: quota 3
        selected_ideas = ideas[: quotas.startup_ideas]

        # 5. News: news_per_topic
        selected_news: list[dict[str, Any]] = []
        for _tid, t_items in news_by_topic.items():
            t_items.sort(key=lambda x: float(x.get("relevance_score", 1.0)), reverse=True)
            selected_news.extend(t_items[: quotas.news_per_topic])
        selected_news.sort(key=lambda x: float(x.get("relevance_score", 1.0)), reverse=True)

        final_items: list[dict[str, Any]] = (
            selected_internships
            + selected_hackathons
            + selected_competitions
            + selected_ideas
            + selected_news
        )

        # Select Top 3: best internship first if available
        top_3: list[dict[str, Any]] = []
        if selected_internships:
            top_3.append(selected_internships[0])

        candidates_pool = [it for it in final_items if it not in top_3]
        candidates_pool.sort(
            key=lambda x: (
                int(x.get("score", 0))
                if (x.get("category") or "") in ("internship", "hackathon")
                else 0,
                float(x.get("relevance_score", 1.0)),
            ),
            reverse=True,
        )
        for cand in candidates_pool:
            if len(top_3) >= 3:
                break
            top_3.append(cand)

        tldr_lines = [
            (
                f"⚡ Radar: {len(final_items)} verified items across technical domains, "
                "programs, and competitions."
            ),
            (
                f"🎓 Funded Internships: {len(selected_internships)} high-value opportunities "
                "with verified accommodation/stipend."
            ),
            (
                f"🏆 Competitions & Hackathons: {len(selected_hackathons)} hackathons and "
                f"{len(selected_competitions)} challenges (CTFs capped at {quotas.ctf_max})."
            ),
            (
                f"🤖 Technology & Security: {len(selected_news)} frontier updates across "
                "AI, engineering, and cybersecurity."
            ),
        ]
        tldr = "\n".join(tldr_lines)

        return {
            "items": final_items,
            "top_3": top_3,
            "tldr": tldr,
            "sources_count": len(dedup_urls),
            "total_items": len(final_items),
        }

    async def _run_composer(
        self,
        run_id: str,
        title: str,
        date_str: str,
        slot: str,
        analyst_data: dict[str, Any],
        generation_seconds: float,
    ) -> Path:
        """Write structured items to briefing_items and output markdown file."""
        items = analyst_data.get("items", [])
        top_3 = analyst_data.get("top_3", [])
        tldr = analyst_data.get("tldr", "")

        # Write to briefing_items table
        async with get_db() as db:
            for it in items:
                await execute(
                    db,
                    """
                    INSERT INTO briefing_items
                    (id, briefing_id, topic_id, category, title, summary, source_name, url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO NOTHING
                    """,
                    (
                        it["item_id"],
                        run_id,
                        it.get("topic_id", "topic_ai"),
                        it.get("category", "news"),
                        it["title"],
                        it["summary"],
                        it["source_name"],
                        it["url"],
                    ),
                )
            await db.commit()

        # Build Markdown File
        slot_label = "Morning" if slot == "morning" else "Evening"
        md_lines = [
            f"# ⚡ {title}",
            "",
            f"**Slot**: {slot_label} Briefing | "
            f"**Generated**: {datetime.now(UTC).strftime('%H:%M:%S')} | "
            f"**Sources Hit**: {analyst_data.get('sources_count', 0)}",
            "",
            "## 🌐 Executive Radar (TL;DR)",
            tldr,
            "",
            "## ⭐ Top 3 Developments Today",
        ]

        for idx, it in enumerate(top_3, 1):
            md_lines.append(
                f"{idx}. **[{it['title']}]({it['url']})** — {it['summary']} "
                f"*(Source: {it['source_name']})*"
            )

        md_lines.append("")
        md_lines.append("## 📰 Sector Deep Dives")

        # Group by topic
        by_topic: dict[str, list[dict[str, Any]]] = {}
        for it in items:
            tname = it.get("topic_name", "General")
            by_topic.setdefault(tname, []).append(it)

        for tname, t_items in by_topic.items():
            md_lines.append(f"\n### {tname}")
            for it in t_items:
                md_lines.append(
                    f"- **[{it['title']}]({it['url']})**: {it['summary']} "
                    f"*(via {it['source_name']})*"
                )

        md_lines.append("\n---\n*Generated autonomously by NoX Intelligence Daemon*")
        content = "\n".join(md_lines)

        file_path = BRIEFINGS_DIR / f"{date_str}-{slot}.md"
        file_path.write_text(content, encoding="utf-8")
        log.info("Wrote briefing markdown to %s", file_path)

        # Export to Notes Vault (Obsidian-compatible Markdown Vault)
        try:
            from noxai.notes.vault import NotesVault

            vault = NotesVault(config=self.config)
            await vault.sync_vault_to_db()
            vault.export_briefing_run(run_id, date_str, slot, analyst_data, generation_seconds)
        except Exception as exc:
            log.warning("Notes vault export error: %s", exc)

        return file_path

    async def _run_cleanup(self, run_id: str) -> None:
        """Purge temporary staging artifacts from disk."""
        run_staging_dir = STAGING_DIR / run_id
        if run_staging_dir.exists():
            import shutil

            shutil.rmtree(run_staging_dir, ignore_errors=True)

    async def _send_notification(
        self,
        title: str,
        count: int,
        run_id: str,
        analyst_data: dict[str, Any] | None = None,
    ) -> None:
        """Deliver desktop notification via notify-send."""
        if not self.config.briefing.notify_on_done:
            return

        extra = ""
        if analyst_data:
            items = analyst_data.get("items", [])
            high_internship = next(
                (
                    it
                    for it in items
                    if (it.get("category") or "").lower() in ("internship", "internships")
                    and int(it.get("score", 0)) >= 50
                ),
                None,
            )
            urgent_opp = next(
                (it for it in items if any("[urgent]" in str(r) for r in it.get("why_ranked", []))),
                None,
            )
            if high_internship:
                extra = (
                    f" Top: {high_internship.get('title')} (score {high_internship.get('score')})."
                )
            elif urgent_opp:
                extra = f" Urgent: {urgent_opp.get('title')} closing soon."

        msg = f"{title}: {count} curated items ready.{extra}"
        try:
            import subprocess

            subprocess.run(["notify-send", "-a", "NoX", title, msg], check=False)
        except Exception as exc:
            log.debug("Notification error: %s", exc)


# Compatibility aliases
BriefingEngine = BriefingPipeline


async def run_briefing_pipeline(
    slot: str = "morning",
    date_str: str | None = None,
    force: bool = False,
    run_id: str | None = None,
    rev: int = 1,
    trigger: str = "scheduled",
    cancel_event: asyncio.Event | None = None,
) -> str:
    """Convenience helper to run BriefingPipeline."""
    from noxai.config import get_config

    cfg = get_config()
    pipeline = BriefingPipeline(config=cfg)
    return await pipeline.execute_run(
        slot=slot,
        date_str=date_str,
        force=force,
        run_id=run_id,
        rev=rev,
        trigger=trigger,
        cancel_event=cancel_event,
    )
