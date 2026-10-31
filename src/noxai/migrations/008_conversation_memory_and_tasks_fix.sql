-- Migration 008: Conversation Memory System (CMS) & Tasks Table Fix

-- 1. Rebuild tasks table with 'interrupted' in status CHECK constraint
PRAGMA foreign_keys=OFF;

CREATE TABLE tasks_new (
    id                  TEXT PRIMARY KEY,           -- task_<uuid>
    title               TEXT NOT NULL,
    prompt              TEXT NOT NULL,
    lane                TEXT NOT NULL DEFAULT 'local'
                        CHECK(lane IN ('local', 'cloud', 'scheduler')),
    priority            TEXT NOT NULL DEFAULT 'normal'
                        CHECK(priority IN ('low', 'normal', 'high', 'urgent')),
    status              TEXT NOT NULL DEFAULT 'queued'
                        CHECK(status IN ('queued', 'running', 'paused', 'waiting_approval', 'done', 'failed', 'cancelled', 'interrupted')),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    started_at          TEXT,
    finished_at         TEXT,
    error               TEXT,
    result_json         TEXT,
    progress            REAL NOT NULL DEFAULT 0.0 CHECK(progress BETWEEN 0.0 AND 1.0),
    approval_id         INTEGER REFERENCES approvals(id),
    cancellation_token  TEXT,
    run_id              TEXT REFERENCES runs(id),
    kind                TEXT NOT NULL DEFAULT 'agent',
    parent_id           TEXT,
    payload_json        TEXT,
    result_ref          TEXT,
    attempts            INTEGER NOT NULL DEFAULT 0,
    max_attempts        INTEGER NOT NULL DEFAULT 3,
    heartbeat_at        TEXT,
    owner               TEXT NOT NULL DEFAULT 'system'
);

INSERT INTO tasks_new (
    id, title, prompt, lane, priority, status,
    created_at, started_at, finished_at, error, result_json,
    progress, approval_id, cancellation_token, run_id,
    kind, parent_id, payload_json, result_ref, attempts, max_attempts, heartbeat_at, owner
)
SELECT
    id, title, prompt, lane, priority, status,
    created_at, started_at, finished_at, error, result_json,
    progress, approval_id, cancellation_token, run_id,
    kind, parent_id, payload_json, result_ref, attempts, max_attempts, heartbeat_at, owner
FROM tasks;

DROP TABLE tasks;
ALTER TABLE tasks_new RENAME TO tasks;

CREATE INDEX IF NOT EXISTS idx_tasks_status_priority_created
    ON tasks(status, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_lane
    ON tasks(lane);
CREATE INDEX IF NOT EXISTS idx_tasks_run_id
    ON tasks(run_id);
CREATE INDEX IF NOT EXISTS idx_tasks_parent_id
    ON tasks(parent_id);
CREATE INDEX IF NOT EXISTS idx_tasks_heartbeat
    ON tasks(heartbeat_at);

PRAGMA foreign_keys=ON;

-- 2. Conversation Summaries (L3 rolling folds)
CREATE TABLE IF NOT EXISTS conv_summaries (
    id                  TEXT PRIMARY KEY,           -- csum_<uuid>
    conversation_id     TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    fold_index          INTEGER NOT NULL,
    start_msg_id        TEXT,
    end_msg_id          TEXT,
    summary_markdown    TEXT NOT NULL,
    key_facts_json      TEXT NOT NULL DEFAULT '[]',
    active_topics_json  TEXT NOT NULL DEFAULT '[]',
    token_count         INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_conv_summaries_conv
    ON conv_summaries(conversation_id, fold_index);

-- 3. Conversation State (L1 working state & scratchpad)
CREATE TABLE IF NOT EXISTS conv_state (
    conversation_id     TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
    active_goals        TEXT NOT NULL DEFAULT '[]',
    working_hypotheses  TEXT NOT NULL DEFAULT '[]',
    open_questions      TEXT NOT NULL DEFAULT '[]',
    entities_in_focus   TEXT NOT NULL DEFAULT '[]',
    user_preferences    TEXT NOT NULL DEFAULT '[]',
    last_interaction    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    meta_json           TEXT NOT NULL DEFAULT '{}',
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- 4. Message Chunks & FTS5 (L2 semantic retrieval)
CREATE TABLE IF NOT EXISTS msg_chunks (
    id                  TEXT PRIMARY KEY,           -- chk_<uuid>
    message_id          TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    conversation_id     TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    chunk_index         INTEGER NOT NULL DEFAULT 0,
    chunk_text          TEXT NOT NULL,
    token_count         INTEGER NOT NULL DEFAULT 0,
    embedding           BLOB,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_msg_chunks_conv
    ON msg_chunks(conversation_id);
CREATE INDEX IF NOT EXISTS idx_msg_chunks_msg
    ON msg_chunks(message_id);

CREATE VIRTUAL TABLE IF NOT EXISTS fts_msg_chunks USING fts5(
    chunk_text,
    content='msg_chunks',
    content_rowid='rowid',
    tokenize='porter ascii'
);

CREATE TRIGGER IF NOT EXISTS msg_chunks_ai AFTER INSERT ON msg_chunks BEGIN
  INSERT INTO fts_msg_chunks(rowid, chunk_text) VALUES (new.rowid, new.chunk_text);
END;

CREATE TRIGGER IF NOT EXISTS msg_chunks_ad AFTER DELETE ON msg_chunks BEGIN
  INSERT INTO fts_msg_chunks(fts_msg_chunks, rowid, chunk_text) VALUES('delete', old.rowid, old.chunk_text);
END;

CREATE TRIGGER IF NOT EXISTS msg_chunks_au AFTER UPDATE ON msg_chunks BEGIN
  INSERT INTO fts_msg_chunks(fts_msg_chunks, rowid, chunk_text) VALUES('delete', old.rowid, old.chunk_text);
  INSERT INTO fts_msg_chunks(rowid, chunk_text) VALUES (new.rowid, new.chunk_text);
END;

-- 5. Facts linkage to conversations
ALTER TABLE facts ADD COLUMN conversation_id TEXT;
CREATE INDEX IF NOT EXISTS idx_facts_conversation ON facts(conversation_id);
