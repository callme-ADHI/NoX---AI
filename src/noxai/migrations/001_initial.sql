-- NoX initial database schema (migration 001)
-- Covers all tables defined in spec §7.
-- WAL mode and foreign keys are enabled by the connection helper, not here.
-- FTS5 tables use content= for linked tables where applicable.
-- Vector tables: vec_facts and vec_episodes are created as regular blob
-- tables here; the vec backend upgrades them at startup if sqlite-vec loads.

-- ── Migration tracking (bootstrapped by the runner before this runs) ────────
-- schema_migrations already exists; no-op if included here would fail.

-- ── Conversations ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conversations (
    id          TEXT    PRIMARY KEY,           -- UUID
    title       TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    deleted_at  TEXT                           -- soft delete
);

-- ── Messages ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS messages (
    id              TEXT    PRIMARY KEY,
    conversation_id TEXT    NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT    NOT NULL CHECK(role IN ('user','assistant','system','tool')),
    content         TEXT    NOT NULL,
    tool_call_id    TEXT,
    tool_name       TEXT,
    taint_flags     INTEGER NOT NULL DEFAULT 0,  -- bitmask of TaintFlag
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, created_at);

-- ── Runs ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS runs (
    id              TEXT    PRIMARY KEY,
    kind            TEXT    NOT NULL CHECK(kind IN ('chat','job','resume')),
    conversation_id TEXT    REFERENCES conversations(id),
    job_run_id      TEXT,                          -- set for job runs
    status          TEXT    NOT NULL DEFAULT 'running'
                            CHECK(status IN ('running','done','failed','cancelled','interrupted')),
    allow_cloud     INTEGER NOT NULL DEFAULT 0,    -- 1 = cloud-eligible
    started_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at     TEXT,
    error           TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_conversation ON runs(conversation_id);
CREATE INDEX IF NOT EXISTS idx_runs_status       ON runs(status);

-- ── Task nodes (planner sub-tasks within a run) ───────────────────────────────
CREATE TABLE IF NOT EXISTS task_nodes (
    id              TEXT    PRIMARY KEY,
    run_id          TEXT    NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    parent_id       TEXT    REFERENCES task_nodes(id),
    node_index      INTEGER NOT NULL DEFAULT 0,
    title           TEXT    NOT NULL,
    objective       TEXT    NOT NULL,
    kind            TEXT    NOT NULL DEFAULT 'research'
                            CHECK(kind IN ('research','fetch','compute','action')),
    depends_on_json TEXT    NOT NULL DEFAULT '[]',  -- JSON array of node ids
    status          TEXT    NOT NULL DEFAULT 'pending'
                            CHECK(status IN ('pending','running','done','failed','skipped')),
    output_json     TEXT,                           -- validated worker JSON blob
    error           TEXT,
    started_at      TEXT,
    finished_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_task_nodes_run ON task_nodes(run_id, node_index);

-- ── Facts ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS facts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT    NOT NULL,
    value       TEXT    NOT NULL,
    category    TEXT    NOT NULL DEFAULT 'other'
                        CHECK(category IN ('profile','preference','project','person','device','other')),
    confidence  REAL    NOT NULL DEFAULT 1.0 CHECK(confidence BETWEEN 0 AND 1),
    source      TEXT    NOT NULL DEFAULT 'chat'
                        CHECK(source IN ('chat','job','manual')),
    pinned      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT,
    use_count   INTEGER NOT NULL DEFAULT 0,
    deleted_at  TEXT,                              -- soft delete
    embedding   BLOB                               -- float32 numpy fallback
);

CREATE INDEX IF NOT EXISTS idx_facts_key        ON facts(key);
CREATE INDEX IF NOT EXISTS idx_facts_category   ON facts(category);
CREATE INDEX IF NOT EXISTS idx_facts_pinned     ON facts(pinned);
CREATE INDEX IF NOT EXISTS idx_facts_deleted_at ON facts(deleted_at);

-- ── Fact history (previous values on update) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS facts_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id     INTEGER NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
    key         TEXT    NOT NULL,
    value       TEXT    NOT NULL,
    confidence  REAL    NOT NULL,
    changed_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    changed_by  TEXT    NOT NULL DEFAULT 'agent'
);

CREATE INDEX IF NOT EXISTS idx_facts_history_fact ON facts_history(fact_id);

-- ── Episodes ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS episodes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL DEFAULT (datetime('now')),
    kind        TEXT    NOT NULL DEFAULT 'conversation'
                        CHECK(kind IN ('conversation','job','daily_summary','consolidation','manual')),
    task_id     TEXT,                              -- run_id or job_run_id
    summary     TEXT    NOT NULL,
    importance  REAL    NOT NULL DEFAULT 0.5 CHECK(importance BETWEEN 0 AND 1),
    archived_at TEXT,
    embedding   BLOB                               -- float32 numpy fallback
);

CREATE INDEX IF NOT EXISTS idx_episodes_ts          ON episodes(ts);
CREATE INDEX IF NOT EXISTS idx_episodes_kind        ON episodes(kind);
CREATE INDEX IF NOT EXISTS idx_episodes_archived_at ON episodes(archived_at);

-- ── Vector tables (numpy blob fallback; upgraded to sqlite-vec at startup) ──
-- These are thin wrapper tables that hold row references and blob embeddings.
-- When sqlite-vec is available, the loader creates virtual vec0 tables instead.

CREATE TABLE IF NOT EXISTS vec_facts (
    fact_id     INTEGER PRIMARY KEY REFERENCES facts(id) ON DELETE CASCADE,
    embedding   BLOB    NOT NULL                  -- float32 LE bytes
);

CREATE TABLE IF NOT EXISTS vec_episodes (
    episode_id  INTEGER PRIMARY KEY REFERENCES episodes(id) ON DELETE CASCADE,
    embedding   BLOB    NOT NULL
);

-- ── FTS5 for full-text search ────────────────────────────────────────────────
CREATE VIRTUAL TABLE IF NOT EXISTS fts_facts USING fts5(
    key,
    value,
    content='facts',
    content_rowid='id',
    tokenize='porter ascii'
);

CREATE VIRTUAL TABLE IF NOT EXISTS fts_episodes USING fts5(
    summary,
    content='episodes',
    content_rowid='id',
    tokenize='porter ascii'
);

-- ── Jobs ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT    PRIMARY KEY,
    name            TEXT    NOT NULL UNIQUE,
    description     TEXT    NOT NULL DEFAULT '',
    schedule_type   TEXT    NOT NULL CHECK(schedule_type IN ('cron','interval','once')),
    schedule_expr   TEXT    NOT NULL,
    timezone        TEXT    NOT NULL DEFAULT 'Asia/Kolkata',
    instruction     TEXT    NOT NULL,
    config_json     TEXT    NOT NULL DEFAULT '{}',
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_by      TEXT    NOT NULL DEFAULT 'seed'
                            CHECK(created_by IN ('seed','owner','agent')),
    version         INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    last_run_at     TEXT,
    next_run_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_name        ON jobs(name);
CREATE INDEX IF NOT EXISTS idx_jobs_next_run_at ON jobs(next_run_at, enabled);

-- ── Job versions ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS job_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT    NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    version     INTEGER NOT NULL,
    snapshot_json TEXT  NOT NULL,                  -- full job row as JSON
    changed_by  TEXT    NOT NULL DEFAULT 'agent',
    change_note TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_job_versions_job ON job_versions(job_id, version DESC);

-- ── Job runs ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS job_runs (
    id              TEXT    PRIMARY KEY,
    job_id          TEXT    NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    started_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at     TEXT,
    status          TEXT    NOT NULL DEFAULT 'running'
                            CHECK(status IN ('running','ok','failed','skipped','interrupted')),
    task_run_id     TEXT    REFERENCES runs(id),
    output_markdown TEXT,
    error           TEXT
);

CREATE INDEX IF NOT EXISTS idx_job_runs_job    ON job_runs(job_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_job_runs_status ON job_runs(status);

-- ── Outputs (briefings) ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS outputs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_run_id      TEXT    NOT NULL REFERENCES job_runs(id) ON DELETE CASCADE,
    title           TEXT    NOT NULL DEFAULT '',
    markdown        TEXT    NOT NULL,
    sources_json    TEXT    NOT NULL DEFAULT '[]',
    feedback        INTEGER,                        -- 1 | -1 | NULL
    feedback_note   TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_outputs_job_run ON outputs(job_run_id);
CREATE INDEX IF NOT EXISTS idx_outputs_created ON outputs(created_at DESC);

-- ── News/opportunity sources ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    url         TEXT    NOT NULL UNIQUE,
    kind        TEXT    NOT NULL CHECK(kind IN ('rss','web')),
    tags        TEXT    NOT NULL DEFAULT '[]',       -- JSON array of tag strings
    enabled     INTEGER NOT NULL DEFAULT 1,
    last_ok_at  TEXT,
    last_error  TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Seen items (dedupe for RSS/web) ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS seen_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url_hash    TEXT    NOT NULL UNIQUE,             -- SHA-256 hex of the URL
    url         TEXT    NOT NULL,
    source_id   INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    seen_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_seen_items_hash ON seen_items(url_hash);

-- ── Preferences ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS preferences (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT    NOT NULL UNIQUE,
    value       TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_preferences_key ON preferences(key);

-- ── Skills index ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS skills_index (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    description TEXT    NOT NULL,
    filename    TEXT    NOT NULL UNIQUE,
    embedding   BLOB,                               -- float32 of name+description
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Skill versions ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS skill_versions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name  TEXT    NOT NULL,
    body        TEXT    NOT NULL,
    changed_by  TEXT    NOT NULL DEFAULT 'agent',
    change_note TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_skill_versions_name ON skill_versions(skill_name, created_at DESC);

-- ── Approvals ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS approvals (
    id          TEXT    PRIMARY KEY,               -- UUID
    run_id      TEXT    NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    tool        TEXT    NOT NULL,
    args_json   TEXT    NOT NULL,
    tier        TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','approved','denied','timeout','cancelled')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT,
    resolver    TEXT                               -- 'owner' | 'timeout' | 'system'
);

CREATE INDEX IF NOT EXISTS idx_approvals_run    ON approvals(run_id);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);

-- ── Audit log ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT    NOT NULL DEFAULT (datetime('now')),
    run_id          TEXT    REFERENCES runs(id),
    tool            TEXT    NOT NULL,
    args_json       TEXT    NOT NULL DEFAULT '{}',
    tier            TEXT    NOT NULL,
    decision        TEXT    NOT NULL
                            CHECK(decision IN ('auto','approved','denied','blocked','timeout')),
    approver        TEXT    NOT NULL DEFAULT 'system'
                            CHECK(approver IN ('system','owner')),
    result_summary  TEXT,
    duration_ms     INTEGER,
    ok              INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_audit_log_ts     ON audit_log(ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_run    ON audit_log(run_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_tool   ON audit_log(tool);

-- ── Safety whitelist (read-only from agent; set only via UI click) ─────────────
CREATE TABLE IF NOT EXISTS safety_whitelist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tool        TEXT    NOT NULL UNIQUE,
    added_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    added_by    TEXT    NOT NULL DEFAULT 'owner'  -- always 'owner' for Phase 1
);

-- ── Settings (non-safety; readable/writable via /api/settings) ───────────────
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT    PRIMARY KEY,
    value       TEXT    NOT NULL,                  -- JSON encoded
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Seed state (tracks which seeds have been applied) ─────────────────────────
CREATE TABLE IF NOT EXISTS seed_state (
    key         TEXT    PRIMARY KEY,               -- e.g. 'seeds_v1'
    applied_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Insert default settings ───────────────────────────────────────────────────
INSERT OR IGNORE INTO settings (key, value) VALUES
    ('theme', '"dark"'),
    ('accent_color', '"#7c9cff"'),
    ('notifications_desktop', 'true'),
    ('notifications_ui', 'true');
