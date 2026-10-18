"""Realistic database population harness for NoX testing.

Fills a migrated SQLite database with realistic data in every table,
including BLOB embedding columns with invalid UTF-8 sequences.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
import uuid
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MIGRATIONS_DIR = REPO_ROOT / "src" / "noxai" / "migrations"


def _make_fake_embedding_blob(dim: int = 768) -> bytes:
    """Create a raw float32 binary blob of specified dimension containing invalid UTF-8 bytes."""
    floats = [float(i % 17 - 8) * 0.12345 for i in range(dim)]
    raw_bytes = struct.pack(f"{dim}f", *floats)
    invalid_prefix = b"\xff\xfe\x80\xc0"
    return invalid_prefix + raw_bytes[len(invalid_prefix) :]


def run_migrations_sync(conn: sqlite3.Connection) -> None:
    """Run all migrations against sync sqlite3 connection."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id          INTEGER PRIMARY KEY,
            filename    TEXT    NOT NULL UNIQUE,
            applied_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()

    migration_files = sorted(
        MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"),
        key=lambda p: int(p.stem.split("_")[0]),
    )
    for mf in migration_files:
        row = conn.execute(
            "SELECT id FROM schema_migrations WHERE filename = ?", (mf.name,)
        ).fetchone()
        if row is None:
            conn.executescript(mf.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (filename) VALUES (?)", (mf.name,))
            conn.commit()


def populate_realistic_db(db_path: Path | str) -> None:
    """Populate an SQLite database with realistic rows across all tables."""
    conn = sqlite3.connect(str(db_path))
    run_migrations_sync(conn)

    now = datetime.now(UTC).isoformat()
    embedding_blob = _make_fake_embedding_blob(768)

    # 1. Conversations (12 conversations)
    conversations = []
    for i in range(1, 13):
        cid = f"conv-{i:03d}"
        title = f"Conversation {i}: AI Architecture & 🚀 Automation {i}"
        conversations.append((cid, title, now, now))
    conn.executemany(
        "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        conversations,
    )

    # 2. Messages (500 messages across the 12 conversations)
    messages = []
    long_content_50kb = "A" * 50_000
    for i in range(500):
        cid = f"conv-{(i % 12) + 1:03d}"
        role = ["user", "assistant", "system", "tool"][i % 4]
        if i == 42:
            content = f"Unicode test: こんにちは 🚀 🧠 Sparkles ✨\n{long_content_50kb}"
        else:
            content = f"Message {i} in {cid}: Sample agent dialogue message with details."
        messages.append((str(uuid.uuid4()), cid, role, content, 0, now))
    conn.executemany(
        """INSERT INTO messages (id, conversation_id, role, content, taint_flags, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        messages,
    )

    # 3. Runs (30 runs in various statuses)
    runs = []
    statuses = ["running", "done", "failed", "cancelled", "interrupted"]
    kinds = ["chat", "job", "resume"]
    for i in range(1, 31):
        rid = f"run-{i:03d}"
        cid = f"conv-{(i % 12) + 1:03d}"
        status = statuses[i % len(statuses)]
        kind = kinds[i % len(kinds)]
        error = "Simulated run error message" if status == "failed" else None
        runs.append(
            (rid, kind, cid, f"jobrun-{i}", status, 1 if i % 2 == 0 else 0, now, now, error)
        )
    conn.executemany(
        """INSERT INTO runs (id, kind, conversation_id, job_run_id, status, allow_cloud,
           started_at, finished_at, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        runs,
    )

    # 4. Task nodes (for runs)
    nodes = []
    node_statuses = ["pending", "running", "done", "failed", "skipped"]
    node_kinds = ["research", "fetch", "compute", "action"]
    for i in range(1, 61):
        nid = f"node-{i:03d}"
        rid = f"run-{(i % 30) + 1:03d}"
        status = node_statuses[i % len(node_statuses)]
        kind = node_kinds[i % len(node_kinds)]
        nodes.append(
            (
                nid,
                rid,
                None,
                i,
                f"Task Node {i}",
                f"Objective for node {i}",
                kind,
                "[]",
                status,
                json.dumps({"result": f"Output data {i}"}),
                None,
                now,
                now,
            )
        )
    conn.executemany(
        """INSERT INTO task_nodes (id, run_id, parent_id, node_index, title, objective, kind,
           depends_on_json, status, output_json, error, started_at, finished_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        nodes,
    )

    # 5. Facts (40 facts with embedding BLOBs)
    categories = ["profile", "preference", "project", "person", "device", "other"]
    facts = []
    for i in range(1, 41):
        pinned = 1 if i <= 10 else 0
        cat = categories[i % len(categories)]
        val = f"Fact value {i} with emoji 🎯 and unicode: café"
        facts.append(
            (f"key_{i}", val, cat, 0.95, "chat", pinned, now, now, now, i, None, embedding_blob)
        )
    conn.executemany(
        """INSERT INTO facts (key, value, category, confidence, source, pinned,
           created_at, updated_at, last_used_at, use_count, deleted_at, embedding)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        facts,
    )

    # Populate vec_facts
    for i in range(1, 41):
        conn.execute(
            "INSERT INTO vec_facts (fact_id, embedding) VALUES (?, ?)", (i, embedding_blob)
        )

    # 6. Episodes (120 episodes with embedding BLOBs)
    episodes = []
    ep_kinds = ["conversation", "job", "daily_summary", "consolidation", "manual"]
    for i in range(1, 121):
        kind = ep_kinds[i % len(ep_kinds)]
        summary = f"Episode {i} summary: Discussed project details, automation and news. 🌟"
        episodes.append((now, kind, f"run-{(i % 30) + 1:03d}", summary, 0.8, None, embedding_blob))
    conn.executemany(
        """INSERT INTO episodes (ts, kind, task_id, summary, importance, archived_at, embedding)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        episodes,
    )

    # Populate vec_episodes
    for i in range(1, 121):
        conn.execute(
            "INSERT INTO vec_episodes (episode_id, embedding) VALUES (?, ?)", (i, embedding_blob)
        )

    # 7. Jobs (3 seeds + 5 user/agent-created jobs)
    jobs = [
        (
            "job_seed_daily",
            "daily-briefing",
            "Morning tech & security briefing",
            "cron",
            "30 7 * * *",
            "Asia/Kolkata",
            "Generate daily briefing",
            "{}",
            1,
            "seed",
            1,
            now,
            now,
            now,
            now,
        ),
        (
            "job_seed_mem",
            "memory-consolidation",
            "Consolidate facts and episodes",
            "cron",
            "0 3 * * *",
            "Asia/Kolkata",
            "Consolidate memory",
            "{}",
            1,
            "seed",
            1,
            now,
            now,
            now,
            now,
        ),
        (
            "job_seed_opp",
            "opportunities-digest",
            "Daily digest of grants & RFPs",
            "cron",
            "0 8 * * *",
            "Asia/Kolkata",
            "Generate opportunities digest",
            "{}",
            1,
            "seed",
            1,
            now,
            now,
            now,
            now,
        ),
    ]
    for i in range(1, 6):
        jobs.append(
            (
                f"job_custom_{i}",
                f"custom-job-{i}",
                f"Custom automation job {i}",
                "interval",
                "30m",
                "Asia/Kolkata",
                f"Execute custom task {i}",
                "{}",
                1,
                "owner",
                3,
                now,
                now,
                now,
                now,
            )
        )
    conn.executemany(
        """INSERT INTO jobs (id, name, description, schedule_type, schedule_expr, timezone,
           instruction, config_json, enabled, created_by, version, created_at,
           updated_at, last_run_at, next_run_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        jobs,
    )

    # 8. Job versions (3 versions for each custom job)
    job_versions = []
    for i in range(1, 6):
        for v in range(1, 4):
            snapshot = json.dumps(
                {"name": f"custom-job-{i}", "schedule_expr": f"{v * 15}m", "version": v}
            )
            job_versions.append(
                (f"job_custom_{i}", v, snapshot, "owner", f"Version edit v{v}", now)
            )
    conn.executemany(
        """INSERT INTO job_versions (job_id, version, snapshot_json, changed_by,
           change_note, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        job_versions,
    )

    # 9. Job runs
    job_runs = []
    for i in range(1, 21):
        jid = "job_seed_daily" if i <= 10 else "job_custom_1"
        status = "ok" if i % 4 != 0 else "failed"
        job_runs.append(
            (f"jobrun-{i}", jid, now, now, status, f"run-{i:03d}", f"Output of job run {i}", None)
        )
    conn.executemany(
        """INSERT INTO job_runs (id, job_id, started_at, finished_at, status,
           task_run_id, output_markdown, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        job_runs,
    )

    # 10. Outputs (with large markdown and sources_json)
    outputs = []
    for i in range(1, 21):
        sources = [{"url": f"https://example.com/item/{i}", "title": f"Source {i}"}]
        md = f"# Output Report {i}\n\nDetailed briefing and analysis.\n\n" + (
            long_content_50kb if i == 5 else "Content body."
        )
        outputs.append(
            (
                f"jobrun-{i}",
                f"Briefing Report {i}",
                md,
                json.dumps(sources),
                1 if i % 2 == 0 else -1,
                "Great job" if i % 2 == 0 else "Needs more detail",
                now,
            )
        )
    conn.executemany(
        """INSERT INTO outputs (job_run_id, title, markdown, sources_json, feedback,
           feedback_note, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        outputs,
    )

    # 11. Sources (some dead)
    sources_data = [
        ("Hacker News", "https://news.ycombinator.com/rss", "rss", '["tech"]', 1, now, None, now),
        (
            "Arch Linux News",
            "https://archlinux.org/feeds/news/",
            "rss",
            '["linux"]',
            1,
            now,
            None,
            now,
        ),
        (
            "Dead Feed",
            "https://deadlink.example.org/feed.xml",
            "rss",
            "[]",
            0,
            now,
            "HTTP 404 Not Found",
            now,
        ),
    ]
    conn.executemany(
        """INSERT INTO sources (name, url, kind, tags, enabled, last_ok_at, last_error, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        sources_data,
    )

    # 12. Seen items
    seen = []
    for i in range(1, 51):
        url = f"https://example.com/item/{i}"
        uhash = hashlib.sha256(url.encode()).hexdigest()
        seen.append((uhash, url, 1, now))
    conn.executemany(
        "INSERT INTO seen_items (url_hash, url, source_id, seen_at) VALUES (?, ?, ?, ?)",
        seen,
    )

    # 13. Audit log (200 rows of all decision types)
    audit_rows = []
    decisions = ["auto", "approved", "denied", "blocked", "timeout"]
    tiers = ["read", "write", "dangerous"]
    tools = ["read_file", "write_file", "run_shell", "web_search", "fetch_page"]
    for i in range(1, 201):
        dec = decisions[i % len(decisions)]
        tier = tiers[i % len(tiers)]
        tool = tools[i % len(tools)]
        approver = "owner" if dec in ["approved", "denied"] else "system"
        audit_rows.append(
            (
                now,
                f"run-{(i % 30) + 1:03d}",
                tool,
                json.dumps({"param": f"val_{i}"}),
                tier,
                dec,
                approver,
                f"Result summary for audit {i}",
                125,
                1 if dec in ["auto", "approved"] else 0,
            )
        )
    conn.executemany(
        """INSERT INTO audit_log (ts, run_id, tool, args_json, tier, decision, approver,
           result_summary, duration_ms, ok)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        audit_rows,
    )

    # 14. Approvals (all statuses: pending, approved, denied, consumed, expired)
    appr_statuses = ["pending", "approved", "denied", "consumed", "expired"]
    approvals = []
    for i in range(1, 31):
        status = appr_statuses[i % len(appr_statuses)]
        tool = "write_file" if i % 2 == 0 else "run_shell"
        approvals.append(
            (
                tool,
                json.dumps({"path": f"/tmp/test_{i}.txt"}),
                f"hash_{i:04x}",
                f"Approval reason {i}",
                now,
                now,
                status,
                now if status != "pending" else None,
                "owner" if status in ["approved", "consumed"] else None,
                f"run-{(i % 30) + 1:03d}",
            )
        )
    conn.executemany(
        """INSERT INTO approvals (tool_name, args_json, args_hash, reason, requested_at,
           expires_at, status, decided_at, decided_by, run_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        approvals,
    )

    # 15. Preferences
    prefs = [
        ("theme", "dark", now),
        ("notifications", "enabled", now),
        ("timezone", "Asia/Kolkata", now),
    ]
    conn.executemany(
        "INSERT INTO preferences (key, value, updated_at) VALUES (?, ?, ?)",
        prefs,
    )

    # 16. Skills & skill versions
    conn.execute(
        """INSERT INTO skills_index (name, description, filename, embedding, updated_at)
           VALUES (?, ?, ?, ?, ?)""",
        ("researcher", "Deep research skill", "researcher.toml", embedding_blob, now),
    )
    conn.execute(
        """INSERT INTO skill_versions (skill_name, body, changed_by, change_note, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        ("researcher", "name = 'researcher'\n", "owner", "Initial version", now),
    )

    # 17. Settings
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
        ("gate.mode", "token_only", now),
    )
    conn.execute(
        "INSERT OR REPLACE INTO seed_state (key, applied_at) VALUES (?, ?)", ("seeds_v1", now)
    )

    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    conn.close()
