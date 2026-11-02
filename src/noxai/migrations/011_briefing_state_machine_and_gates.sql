-- Migration 011: Briefing State Machine and Gates (Session K, Phase 1)
-- Rebuilds briefing_runs table to support 'interrupted' state and failure_reason column.

PRAGMA foreign_keys = OFF;

CREATE TABLE IF NOT EXISTS briefing_runs_new (
    id                  TEXT PRIMARY KEY,           -- brun_<uuid>
    date                TEXT NOT NULL,              -- YYYY-MM-DD
    slot                TEXT NOT NULL CHECK(slot IN ('morning', 'evening')),
    state               TEXT NOT NULL DEFAULT 'queued' CHECK(state IN ('queued', 'running', 'succeeded', 'failed', 'partial', 'cancelled', 'missed', 'interrupted')),
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
    failure_reason      TEXT,
    UNIQUE(date, slot, rev)
);

INSERT INTO briefing_runs_new (
    id, date, slot, state, trigger, rev, task_id, title, summary, top_3_json,
    sources_count, generation_seconds, markdown_path, created_at, started_at, finished_at, error, failure_reason
)
SELECT
    id, date, slot, state, trigger, rev, task_id, title, summary, top_3_json,
    sources_count, generation_seconds, markdown_path, created_at, started_at, finished_at, error,
    COALESCE(error, '') AS failure_reason
FROM briefing_runs;

DROP TABLE briefing_runs;
ALTER TABLE briefing_runs_new RENAME TO briefing_runs;
CREATE INDEX IF NOT EXISTS idx_briefing_runs_date_slot_rev ON briefing_runs(date, slot, rev);

PRAGMA foreign_keys = ON;
