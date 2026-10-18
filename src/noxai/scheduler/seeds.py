"""Seed data initialization for NoX jobs and verified RSS sources per Section 6.7."""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

from noxai.db import execute, fetch_one
from noxai.scheduler.time_utils import compute_next_run

if TYPE_CHECKING:
    import aiosqlite

log = logging.getLogger(__name__)

CURRENT_SEED_VERSION = "1"

SEED_JOBS: list[dict[str, Any]] = [
    {
        "name": "daily-briefing",
        "description": "Morning cybersecurity, AI/tech, and Kerala startup briefing",
        "schedule_type": "cron",
        "schedule_expr": "30 7 * * *",
        "timezone": "Asia/Kolkata",
        "instruction": (
            "Produce my morning briefing for {today}.\n"
            "Sections: (1) Cybersecurity: major vulnerabilities, breaches, advisories, "
            "notable tools. (2) AI and tech: model releases, open-source and local-AI "
            "tooling, major industry news. (3) India and Kerala tech and startup "
            "ecosystem: funding, events, programs, government schemes.\n"
            'Use feed sources tagged "news" first (rss_fetch with unseen_only=true), '
            "then web_search to fill gaps. Skip items already seen.\n"
            "Output: start with a 3-line TL;DR. Then each section with at most 6 items. "
            "Each item: headline, a 2-sentence summary, one line on why it matters to "
            "a cybersecurity student, and the link.\n"
            "If a section has nothing new, say so in one line."
        ),
        "config": {
            "allow_cloud": True,
            "delivery": ["ui", "desktop"],
            "sources": ["news"],
            "output_format": "briefing",
            "max_runtime_seconds": 300,
            "catchup": "once",
            "kind": "agent",
        },
    },
    {
        "name": "opportunities-digest",
        "description": "Daily digest of CTFs, hackathons, internships and fellowships",
        "schedule_type": "cron",
        "schedule_expr": "0 8 * * *",
        "timezone": "Asia/Kolkata",
        "instruction": (
            "Find current opportunities for a B.Tech CSE (cybersecurity) student in Kerala, "
            "India: hackathons, CTF competitions, internships, fellowships, grants, "
            "startup programs, and student competitions. India-wide, remote, and "
            "Kerala-specific all count.\n"
            'Use feed sources tagged "opportunities", then web_search. '
            "Open each promising page with fetch_page and confirm the deadline and "
            "eligibility before listing it. Ignore expired items and anything you could "
            "not verify.\n"
            "Output: a ranked list (best fit first). Each item: name, organizer, "
            "deadline, eligibility, why it fits, link. Mark anything with a deadline "
            "inside 7 days as URGENT. Skip items already seen."
        ),
        "config": {
            "allow_cloud": True,
            "delivery": ["ui", "desktop"],
            "sources": ["opportunities"],
            "output_format": "digest",
            "max_runtime_seconds": 300,
            "catchup": "once",
            "kind": "agent",
        },
    },
    {
        "name": "memory-consolidation",
        "description": "Nightly memory consolidation routine",
        "schedule_type": "cron",
        "schedule_expr": "0 3 * * *",
        "timezone": "Asia/Kolkata",
        "instruction": (
            "Consolidate memory episodes, resolve conflicting facts, and archive stale entries."
        ),
        "config": {
            "allow_cloud": False,
            "delivery": ["ui"],
            "sources": [],
            "max_runtime_seconds": 600,
            "catchup": "skip",
            "kind": "agent",
        },
    },
]

# Verified live RSS sources (per spec §6.7 requirement: drop dead ones and record in DECISIONS.md)
VERIFIED_SEED_SOURCES: list[dict[str, Any]] = [
    {
        "name": "The Hacker News",
        "url": "https://feeds.feedburner.com/TheHackersNews",
        "kind": "rss",
        "tags": ["news", "cyber"],
    },
    {
        "name": "BleepingComputer",
        "url": "https://www.bleepingcomputer.com/feed/",
        "kind": "rss",
        "tags": ["news", "cyber"],
    },
    {
        "name": "Krebs on Security",
        "url": "https://krebsonsecurity.com/feed/",
        "kind": "rss",
        "tags": ["news", "cyber"],
    },
    {
        "name": "Hacker News frontpage",
        "url": "https://hnrss.org/frontpage",
        "kind": "rss",
        "tags": ["news", "tech"],
    },
    {
        "name": "CTFtime upcoming events",
        "url": "https://ctftime.org/event/list/upcoming/rss/",
        "kind": "rss",
        "tags": ["opportunities", "ctf"],
    },
    {
        "name": "Inc42 (India startup news)",
        "url": "https://inc42.com/feed/",
        "kind": "rss",
        "tags": ["news", "india", "startup"],
    },
    {
        "name": "YourStory (India tech news)",
        "url": "https://yourstory.com/feed",
        "kind": "rss",
        "tags": ["news", "india", "tech"],
    },
    {
        "name": "The Hindu Technology",
        "url": "https://www.thehindu.com/sci-tech/technology/feeder/default.rss",
        "kind": "rss",
        "tags": ["news", "tech", "india"],
    },
]


async def seed_database_if_needed(db: aiosqlite.Connection) -> bool:
    """Insert initial seed jobs and verified sources if not already seeded."""
    row = await fetch_one(db, "SELECT key FROM seed_state WHERE key = 'seeds_v1'")
    if row is not None:
        log.debug("Database already seeded (key=%s)", row["key"])
        return False

    log.info("Seeding initial jobs and verified sources into database...")

    # 1. Insert seed jobs
    for job_def in SEED_JOBS:
        job_id = f"job_seed_{uuid.uuid4().hex[:12]}"
        next_dt = compute_next_run(
            job_def["schedule_type"],
            job_def["schedule_expr"],
            job_def["timezone"],
        )
        next_run_iso = next_dt.isoformat() if next_dt else None
        cfg_json = json.dumps(job_def["config"])

        await execute(
            db,
            """
            INSERT INTO jobs (
                id, name, description, schedule_type, schedule_expr, timezone,
                instruction, config_json, enabled, created_by, version, next_run_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'seed', 1, ?)
            ON CONFLICT(name) DO NOTHING
            """,
            (
                job_id,
                job_def["name"],
                job_def["description"],
                job_def["schedule_type"],
                job_def["schedule_expr"],
                job_def["timezone"],
                job_def["instruction"],
                cfg_json,
                next_run_iso,
            ),
        )

        # Snapshot version 1
        snapshot = {
            "id": job_id,
            "name": job_def["name"],
            "description": job_def["description"],
            "schedule_type": job_def["schedule_type"],
            "schedule_expr": job_def["schedule_expr"],
            "timezone": job_def["timezone"],
            "instruction": job_def["instruction"],
            "config_json": cfg_json,
            "enabled": 1,
            "version": 1,
        }
        await execute(
            db,
            """
            INSERT INTO job_versions (job_id, version, snapshot_json, changed_by, change_note)
            VALUES (?, 1, ?, 'seed', 'Initial system seed')
            """,
            (job_id, json.dumps(snapshot)),
        )

    # 2. Insert verified sources
    for src in VERIFIED_SEED_SOURCES:
        tags_json = json.dumps(src["tags"])
        await execute(
            db,
            """
            INSERT INTO sources (name, url, kind, tags, enabled)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(url) DO UPDATE SET tags = excluded.tags
            """,
            (src["name"], src["url"], src["kind"], tags_json),
        )

    # 3. Mark seed complete
    await execute(
        db,
        "INSERT INTO seed_state (key) VALUES ('seeds_v1')",
    )
    await db.commit()
    log.info("Database seeding complete.")
    return True
