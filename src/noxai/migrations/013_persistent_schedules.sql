-- Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
-- commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
-- Migration 013: Persistent Daemon-Owned Scheduler (Workstream B)

CREATE TABLE IF NOT EXISTS schedules (
    id              TEXT    PRIMARY KEY,
    name            TEXT    UNIQUE NOT NULL,
    kind            TEXT    NOT NULL CHECK(kind IN ('cron', 'interval')),
    expression      TEXT    NOT NULL,
    timezone        TEXT    NOT NULL DEFAULT 'Asia/Kolkata',
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_run_at     TEXT,
    next_run_at     TEXT,
    last_status     TEXT,
    catchup_policy  TEXT    NOT NULL DEFAULT 'run_once' CHECK(catchup_policy IN ('run_once', 'skip', 'run_all')),
    target_action   TEXT    NOT NULL,
    config_json     TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS schedule_runs (
    id              TEXT    PRIMARY KEY,
    schedule_id     TEXT    NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
    started_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at     TEXT,
    status          TEXT    NOT NULL CHECK(status IN ('running', 'success', 'failed', 'cancelled', 'missed', 'interrupted')),
    error           TEXT,
    trace_id        TEXT,
    log_path        TEXT,
    heartbeat_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_schedules_next_run ON schedules(enabled, next_run_at);
CREATE INDEX IF NOT EXISTS idx_schedule_runs_schedule_id ON schedule_runs(schedule_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_schedule_runs_status ON schedule_runs(status);

-- Seed initial default schedules idempotently
INSERT OR IGNORE INTO schedules (id, name, kind, expression, timezone, enabled, catchup_policy, target_action, config_json, created_at)
VALUES
    ('sched_briefing_morning', 'briefing_morning', 'cron', '0 4 * * *', 'Asia/Kolkata', 1, 'run_once', 'briefing_morning', '{"slot": "morning", "description": "Morning intelligence briefing at 04:00 IST"}', datetime('now')),
    ('sched_briefing_evening', 'briefing_evening', 'cron', '0 18 * * *', 'Asia/Kolkata', 1, 'run_once', 'briefing_evening', '{"slot": "evening", "description": "Evening intelligence briefing at 18:00 IST"}', datetime('now')),
    ('sched_backup_daily',     'backup_daily',     'cron', '0 20 * * *', 'Asia/Kolkata', 1, 'run_once', 'backup_daily',     '{"job_id": "job_06725cb80054", "description": "Daily GitHub backup of documents after 20:00 IST"}', datetime('now'));
