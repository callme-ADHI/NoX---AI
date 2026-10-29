-- Migration 007: Detached Task Supervisor, KeyPool Metrics, Briefing Engine & Feedback

-- 1. Upgrade tasks table for TaskSupervisor requirements
ALTER TABLE tasks ADD COLUMN kind TEXT NOT NULL DEFAULT 'agent';
ALTER TABLE tasks ADD COLUMN parent_id TEXT;
ALTER TABLE tasks ADD COLUMN payload_json TEXT;
ALTER TABLE tasks ADD COLUMN result_ref TEXT;
ALTER TABLE tasks ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3;
ALTER TABLE tasks ADD COLUMN heartbeat_at TEXT;
ALTER TABLE tasks ADD COLUMN owner TEXT NOT NULL DEFAULT 'system';

CREATE INDEX IF NOT EXISTS idx_tasks_parent_id ON tasks(parent_id);
CREATE INDEX IF NOT EXISTS idx_tasks_heartbeat ON tasks(heartbeat_at);

-- 2. Daily keypool counters and metrics (no secrets stored)
CREATE TABLE IF NOT EXISTS keypool_daily_metrics (
    date                TEXT NOT NULL,              -- YYYY-MM-DD
    key_label           TEXT NOT NULL,              -- e.g. k1, k2
    masked_key          TEXT NOT NULL,              -- e.g. nvapi-****abcd
    requests_count      INTEGER NOT NULL DEFAULT 0,
    tokens_in           INTEGER NOT NULL DEFAULT 0,
    tokens_out          INTEGER NOT NULL DEFAULT 0,
    errors_count        INTEGER NOT NULL DEFAULT 0,
    rate_limits_429     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(date, key_label)
);

-- 3. Topics for briefing pipeline
CREATE TABLE IF NOT EXISTS briefing_topics (
    id                  TEXT PRIMARY KEY,           -- e.g. topic_ai, topic_cybersecurity
    name                TEXT NOT NULL,
    category            TEXT NOT NULL,              -- 'news' | 'opportunities' | 'ideas'
    enabled             INTEGER NOT NULL DEFAULT 1,
    weight              REAL NOT NULL DEFAULT 1.0,
    sources_json        TEXT NOT NULL DEFAULT '[]',
    prompt_file         TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- 4. Briefing runs table with unique idempotent slots per date
CREATE TABLE IF NOT EXISTS briefing_runs (
    id                  TEXT PRIMARY KEY,           -- brun_<uuid>
    date                TEXT NOT NULL,              -- YYYY-MM-DD
    slot                TEXT NOT NULL CHECK(slot IN ('morning', 'evening')),
    state               TEXT NOT NULL DEFAULT 'queued' CHECK(state IN ('queued', 'running', 'succeeded', 'failed', 'partial', 'cancelled')),
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
    UNIQUE(date, slot)
);

CREATE INDEX IF NOT EXISTS idx_briefing_runs_date_slot ON briefing_runs(date, slot);

-- 5. Staging items store for intermediate extraction
CREATE TABLE IF NOT EXISTS briefing_staging (
    run_id              TEXT NOT NULL,
    topic_id            TEXT NOT NULL,
    item_id             TEXT NOT NULL,
    payload_json        TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY(run_id, topic_id, item_id)
);

-- 6. Briefing structured final items
CREATE TABLE IF NOT EXISTS briefing_items (
    id                  TEXT PRIMARY KEY,
    briefing_id         TEXT NOT NULL REFERENCES briefing_runs(id),
    topic_id            TEXT NOT NULL,
    category            TEXT NOT NULL,
    title               TEXT NOT NULL,
    summary             TEXT NOT NULL,
    why_it_matters      TEXT,
    tags_json           TEXT NOT NULL DEFAULT '[]',
    published_at        TEXT,
    source_name         TEXT NOT NULL,
    url                 TEXT NOT NULL,
    canonical_url       TEXT,
    link_status         TEXT NOT NULL DEFAULT 'verified',
    relevance_note      TEXT,
    deadline            TEXT,
    event_dates         TEXT,
    mode                TEXT,
    location            TEXT,
    eligibility         TEXT,
    prize               TEXT,
    registration_url    TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_briefing_items_briefing ON briefing_items(briefing_id);

-- 7. Prompt versions for self-rewriting briefing prompts
CREATE TABLE IF NOT EXISTS prompt_versions (
    id                  TEXT PRIMARY KEY,           -- pv_<uuid>
    scope               TEXT NOT NULL,              -- 'topic_<id>' or 'composer'
    version             INTEGER NOT NULL,
    text                TEXT NOT NULL,
    changelog           TEXT,
    created_by          TEXT NOT NULL DEFAULT 'seed', -- 'seed' | 'user' | 'auto-feedback'
    parent_version      INTEGER,
    status              TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'candidate', 'reverted')),
    metrics_json        TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_prompt_versions_scope ON prompt_versions(scope, version);

-- 8. Feedback data model
CREATE TABLE IF NOT EXISTS briefing_feedback (
    id                  TEXT PRIMARY KEY,           -- fb_<uuid>
    briefing_id         TEXT NOT NULL,
    topic_id            TEXT,
    item_id             TEXT,
    rating              TEXT NOT NULL CHECK(rating IN ('like', 'dislike')),
    reason_text         TEXT,
    tags_json           TEXT DEFAULT '[]',
    applied_in_prompt_version TEXT REFERENCES prompt_versions(id),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_briefing_feedback_topic ON briefing_feedback(topic_id, rating);
