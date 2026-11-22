-- Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
-- commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
-- Migration 014: Step-Level Trace Logging and Prompt Feedback (Workstream C)

CREATE TABLE IF NOT EXISTS traces (
    id                  TEXT    PRIMARY KEY,
    started_at          TEXT    NOT NULL,
    finished_at         TEXT,
    source              TEXT    NOT NULL CHECK(source IN ('chat', 'briefing', 'job', 'schedule')),
    schedule_run_id     TEXT,
    request_text        TEXT,
    intent              TEXT,
    engine              TEXT,
    model               TEXT,
    key_slot_index      INTEGER,
    steps               TEXT    NOT NULL DEFAULT '[]',
    token_counts        TEXT,
    final_status        TEXT    NOT NULL CHECK(final_status IN ('running', 'success', 'failed', 'interrupted', 'cancelled')),
    error_text          TEXT,
    feedback            TEXT
);

CREATE INDEX IF NOT EXISTS idx_traces_started_at ON traces(started_at);
CREATE INDEX IF NOT EXISTS idx_traces_source ON traces(source);
CREATE INDEX IF NOT EXISTS idx_traces_final_status ON traces(final_status);
CREATE INDEX IF NOT EXISTS idx_traces_schedule_run_id ON traces(schedule_run_id);

CREATE TABLE IF NOT EXISTS prompt_feedback (
    id                  TEXT    PRIMARY KEY,
    topic               TEXT    NOT NULL,
    trace_id            TEXT    NOT NULL,
    note                TEXT,
    created_at          TEXT    NOT NULL,
    applied             INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_prompt_feedback_topic_applied ON prompt_feedback(topic, applied);

CREATE TABLE IF NOT EXISTS prompt_versions (
    id                  TEXT    PRIMARY KEY,
    scope               TEXT    NOT NULL,
    version             INTEGER NOT NULL,
    text                TEXT    NOT NULL,
    changelog           TEXT,
    created_by          TEXT    NOT NULL DEFAULT 'seed',
    parent_version      INTEGER,
    status              TEXT    NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'candidate', 'reverted')),
    metrics_json        TEXT    NOT NULL DEFAULT '{}',
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_prompt_versions_scope_ver ON prompt_versions(scope, version);

-- Seed traces_cleanup default daily schedule at 03:00 IST
INSERT OR IGNORE INTO schedules (
    id, name, kind, expression, timezone, enabled, next_run_at, catchup_policy, target_action, config_json
) VALUES (
    'sched_traces_cleanup',
    'traces_cleanup',
    'cron',
    '0 3 * * *',
    'Asia/Kolkata',
    1,
    NULL,
    'skip',
    'traces_cleanup',
    '{"description": "Daily 90-day retention pruning and 100k cap for traces"}'
);
