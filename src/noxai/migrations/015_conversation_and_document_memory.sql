-- Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
-- commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
-- Migration 015: Continuous Conversation Memory and Document Recall (Workstream D)

-- 1. Conversation Memory Schema Extensions
ALTER TABLE messages ADD COLUMN token_estimate INTEGER DEFAULT 0;
ALTER TABLE messages ADD COLUMN turn_index INTEGER DEFAULT 0;
ALTER TABLE messages ADD COLUMN topic_label TEXT DEFAULT '';
ALTER TABLE messages ADD COLUMN text TEXT;

UPDATE messages SET text = content WHERE text IS NULL;

CREATE INDEX IF NOT EXISTS idx_messages_conv_turn ON messages(conversation_id, turn_index);

-- 2. Compact Rolling Conversation Summaries
CREATE TABLE IF NOT EXISTS conversation_summaries (
    id                  TEXT PRIMARY KEY,
    conversation_id     TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    covers_turn_range   TEXT NOT NULL,
    text                TEXT NOT NULL,
    topic_label         TEXT DEFAULT '',
    token_count         INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_conv_summaries_c_id ON conversation_summaries(conversation_id, created_at);

-- 3. Messages FTS5 Full-Text Search
CREATE VIRTUAL TABLE IF NOT EXISTS fts_messages USING fts5(
    content,
    topic_label,
    content='messages',
    content_rowid='rowid',
    tokenize='porter ascii'
);

-- Backfill existing messages into fts_messages
INSERT INTO fts_messages(rowid, content, topic_label)
SELECT rowid, content, coalesce(topic_label, '') FROM messages;

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO fts_messages(rowid, content, topic_label) VALUES (new.rowid, new.content, coalesce(new.topic_label, ''));
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO fts_messages(fts_messages, rowid, content, topic_label) VALUES('delete', old.rowid, old.content, coalesce(old.topic_label, ''));
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
  INSERT INTO fts_messages(fts_messages, rowid, content, topic_label) VALUES('delete', old.rowid, old.content, coalesce(old.topic_label, ''));
  INSERT INTO fts_messages(rowid, content, topic_label) VALUES (new.rowid, new.content, coalesce(new.topic_label, ''));
END;

-- 4. Document Memory Schema (D2)
CREATE TABLE IF NOT EXISTS documents (
    id          TEXT PRIMARY KEY,
    path        TEXT NOT NULL UNIQUE,
    mtime       REAL NOT NULL,
    size        INTEGER NOT NULL,
    sha256      TEXT NOT NULL,
    indexed_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    source_root TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_root ON documents(source_root);

CREATE TABLE IF NOT EXISTS chunks (
    id          TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    text        TEXT NOT NULL,
    heading_path TEXT NOT NULL DEFAULT '',
    char_start  INTEGER NOT NULL DEFAULT 0,
    char_end    INTEGER NOT NULL DEFAULT 0,
    line_start  INTEGER NOT NULL DEFAULT 1,
    line_end    INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id);

CREATE VIRTUAL TABLE IF NOT EXISTS fts_document_chunks USING fts5(
    text,
    heading_path,
    content='chunks',
    content_rowid='rowid',
    tokenize='porter ascii'
);

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO fts_document_chunks(rowid, text, heading_path) VALUES (new.rowid, new.text, new.heading_path);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO fts_document_chunks(fts_document_chunks, rowid, text, heading_path) VALUES('delete', old.rowid, old.text, old.heading_path);
END;

CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
  INSERT INTO fts_document_chunks(fts_document_chunks, rowid, text, heading_path) VALUES('delete', old.rowid, old.text, old.heading_path);
  INSERT INTO fts_document_chunks(rowid, text, heading_path) VALUES (new.rowid, new.text, new.heading_path);
END;

-- 5. Seed Periodic Document Indexing Schedule (every 30 mins)
INSERT OR IGNORE INTO schedules (
    id, name, kind, expression, timezone, enabled, next_run_at, catchup_policy, target_action, config_json
) VALUES (
    'sched_memory_index',
    'memory_index',
    'cron',
    '*/30 * * * *',
    'Asia/Kolkata',
    1,
    NULL,
    'skip',
    'memory_index',
    '{"description": "Periodic incremental indexing of notes and document memory roots"}'
);
