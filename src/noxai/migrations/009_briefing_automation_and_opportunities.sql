-- Migration 009: Briefing Automation, Topic Runs, Settings & Opportunity Registry
-- Rebuilds briefing_runs with trigger, rev, state including 'missed', and UNIQUE(date, slot, rev).
-- Adds briefing_topic_runs for live per-topic progress.
-- Ensures settings table exists.
-- Creates opportunity_registry for internship, hackathon, and competition tracking.
-- Disables legacy daily-briefing job.

PRAGMA foreign_keys=OFF;

CREATE TABLE IF NOT EXISTS briefing_runs_new (
    id                  TEXT PRIMARY KEY,           -- brun_<uuid>
    date                TEXT NOT NULL,              -- YYYY-MM-DD
    slot                TEXT NOT NULL CHECK(slot IN ('morning', 'evening')),
    state               TEXT NOT NULL DEFAULT 'queued' CHECK(state IN ('queued', 'running', 'succeeded', 'failed', 'partial', 'cancelled', 'missed')),
    trigger             TEXT NOT NULL DEFAULT 'scheduled' CHECK(trigger IN ('scheduled', 'manual')),
    rev                 INTEGER NOT NULL DEFAULT 1,
    task_id             TEXT REFERENCES tasks(id),
    title               TEXT NOT NULL,
    summary             TEXT,
    top_3_json          TEXT,
    sources_count       INTEGER NOT NULL DEFAULT 0,
    generation_seconds  REAL NOT NULL DEFAULT 0.0,
    markdown_path       TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at          TEXT,
    finished_at         TEXT,
    error               TEXT,
    UNIQUE(date, slot, rev)
);

INSERT INTO briefing_runs_new (
    id, date, slot, state, trigger, rev, task_id, title, summary, top_3_json,
    sources_count, generation_seconds, markdown_path, created_at, started_at, finished_at, error
)
SELECT
    id, date, slot, state, 'scheduled' AS trigger, 1 AS rev, task_id, title, summary, top_3_json,
    sources_count, generation_seconds, markdown_path, created_at, started_at, finished_at, error
FROM briefing_runs;

DROP TABLE briefing_runs;
ALTER TABLE briefing_runs_new RENAME TO briefing_runs;
CREATE INDEX IF NOT EXISTS idx_briefing_runs_date_slot_rev ON briefing_runs(date, slot, rev);

CREATE TABLE IF NOT EXISTS briefing_topic_runs (
    run_id              TEXT NOT NULL REFERENCES briefing_runs(id) ON DELETE CASCADE,
    topic_id            TEXT NOT NULL,
    state               TEXT NOT NULL DEFAULT 'queued' CHECK(state IN ('queued', 'collecting', 'extracting', 'done', 'failed', 'skipped')),
    items_staged        INTEGER NOT NULL DEFAULT 0,
    error               TEXT,
    started_at          TEXT,
    finished_at         TEXT,
    PRIMARY KEY(run_id, topic_id)
);
CREATE INDEX IF NOT EXISTS idx_briefing_topic_runs_run ON briefing_topic_runs(run_id);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS opportunity_registry (
    canonical_url       TEXT PRIMARY KEY,
    type                TEXT NOT NULL, -- internship, hackathon, competition
    title               TEXT NOT NULL,
    org                 TEXT NOT NULL,
    first_seen          TEXT NOT NULL,
    last_seen           TEXT NOT NULL,
    deadline            TEXT,
    status              TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new', 'interested', 'applied', 'interview', 'offer', 'rejected', 'dismissed')),
    score               INTEGER NOT NULL DEFAULT 0,
    last_changed        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    raw_json            TEXT
);
CREATE INDEX IF NOT EXISTS idx_opportunity_status ON opportunity_registry(status);

-- Disable legacy daily-briefing job so it never double-runs with BriefingScheduler
UPDATE jobs SET enabled = 0, schedule_expr = 'disabled' WHERE name = 'daily-briefing';

PRAGMA foreign_keys=ON;
