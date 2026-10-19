-- Migration 006: Task Center (background tasks, lanes, state transitions, approvals, cancellation)

CREATE TABLE IF NOT EXISTS tasks (
    id                  TEXT PRIMARY KEY,           -- task_<uuid>
    title               TEXT NOT NULL,
    prompt              TEXT NOT NULL,
    lane                TEXT NOT NULL DEFAULT 'local'
                        CHECK(lane IN ('local', 'cloud', 'scheduler')),
    priority            TEXT NOT NULL DEFAULT 'normal'
                        CHECK(priority IN ('low', 'normal', 'high', 'urgent')),
    status              TEXT NOT NULL DEFAULT 'queued'
                        CHECK(status IN ('queued', 'running', 'paused', 'waiting_approval', 'done', 'failed', 'cancelled')),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at          TEXT,
    finished_at         TEXT,
    error               TEXT,
    result_json         TEXT,
    progress            REAL NOT NULL DEFAULT 0.0 CHECK(progress BETWEEN 0.0 AND 1.0),
    approval_id         INTEGER REFERENCES approvals(id),
    cancellation_token  TEXT,
    run_id              TEXT REFERENCES runs(id)
);

CREATE INDEX IF NOT EXISTS idx_tasks_status_priority_created
    ON tasks(status, priority, created_at);

CREATE INDEX IF NOT EXISTS idx_tasks_lane
    ON tasks(lane);

CREATE INDEX IF NOT EXISTS idx_tasks_run_id
    ON tasks(run_id);
