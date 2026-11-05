-- Migration 012: Planned Jobs Engine, Step-by-Step Execution, Notifications, and Delete Controls

-- 1. Create job_plans table for versioned step plans (cloud or deterministic template)
CREATE TABLE IF NOT EXISTS job_plans (
    job_id      TEXT    NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL DEFAULT 1,
    steps_json  TEXT    NOT NULL,
    source      TEXT    NOT NULL CHECK(source IN ('cloud', 'template')),
    status      TEXT    NOT NULL DEFAULT 'draft' CHECK(status IN ('draft', 'approved', 'superseded')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (job_id, version)
);
CREATE INDEX IF NOT EXISTS idx_job_plans_job ON job_plans(job_id, version DESC);

-- 2. Migrate job_runs to support extended status set and heartbeat columns
ALTER TABLE job_runs RENAME TO _job_runs_old;

CREATE TABLE job_runs (
    id                      TEXT    PRIMARY KEY,
    job_id                  TEXT    NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    started_at              TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at             TEXT,
    status                  TEXT    NOT NULL DEFAULT 'queued'
                            CHECK(status IN ('queued','running','ok','succeeded','partial','failed','needs_setup','waiting_approval','stalled','cancelled','interrupted','skipped')),
    task_run_id             TEXT    REFERENCES runs(id),
    output_markdown         TEXT,
    error                   TEXT,
    heartbeat_at            TEXT,
    current_step_index      INTEGER,
    current_step_kind       TEXT,
    current_folder          TEXT,
    plan_version            INTEGER,
    due_slot                TEXT
);

INSERT INTO job_runs (
    id, job_id, started_at, finished_at, status, task_run_id, output_markdown, error
)
SELECT id, job_id, started_at, finished_at, status, task_run_id, output_markdown, error
FROM _job_runs_old;

DROP TABLE _job_runs_old;

CREATE INDEX idx_job_runs_job       ON job_runs(job_id, started_at DESC);
CREATE INDEX idx_job_runs_status    ON job_runs(status);
CREATE INDEX idx_job_runs_heartbeat ON job_runs(heartbeat_at);

-- Recreate outputs to point to new job_runs table
ALTER TABLE outputs RENAME TO _outputs_old;

CREATE TABLE outputs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_run_id      TEXT    NOT NULL REFERENCES job_runs(id) ON DELETE CASCADE,
    title           TEXT    NOT NULL DEFAULT '',
    markdown        TEXT    NOT NULL,
    sources_json    TEXT    NOT NULL DEFAULT '[]',
    feedback        INTEGER,
    feedback_note   TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO outputs (id, job_run_id, title, markdown, sources_json, feedback, feedback_note, created_at)
SELECT id, job_run_id, title, markdown, sources_json, feedback, feedback_note, created_at
FROM _outputs_old;

DROP TABLE _outputs_old;

CREATE INDEX idx_outputs_job_run ON outputs(job_run_id);
CREATE INDEX idx_outputs_created ON outputs(created_at DESC);

-- 3. Create job_run_steps table for step-by-step audit & persistence
CREATE TABLE IF NOT EXISTS job_run_steps (
    id                      TEXT    PRIMARY KEY,
    run_id                  TEXT    NOT NULL REFERENCES job_runs(id) ON DELETE CASCADE,
    step_index              INTEGER NOT NULL,
    kind                    TEXT    NOT NULL,
    folder_alias            TEXT,
    params_redacted         TEXT    NOT NULL DEFAULT '{}',
    state                   TEXT    NOT NULL DEFAULT 'pending'
                            CHECK(state IN ('pending', 'running', 'ok', 'failed', 'skipped')),
    attempts                INTEGER NOT NULL DEFAULT 0,
    started_at              TEXT,
    finished_at             TEXT,
    output_excerpt_redacted TEXT,
    error                   TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_run_steps_run ON job_run_steps(run_id, step_index);

-- 4. Soft delete columns on jobs, tasks, and briefing_runs
ALTER TABLE jobs ADD COLUMN deleted_at TEXT;
ALTER TABLE tasks ADD COLUMN deleted_at TEXT;
ALTER TABLE briefing_runs ADD COLUMN deleted_at TEXT;

-- 5. Notifications table for in-app notification center
CREATE TABLE IF NOT EXISTS notifications (
    id           TEXT    PRIMARY KEY,
    level        TEXT    NOT NULL DEFAULT 'info' CHECK(level IN ('info', 'normal', 'warning', 'critical', 'error')),
    title        TEXT    NOT NULL,
    message      TEXT    NOT NULL,
    category     TEXT    NOT NULL DEFAULT 'jobs',
    payload_json TEXT    NOT NULL DEFAULT '{}',
    read         INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at DESC);
