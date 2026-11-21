-- Migration 017: Connector Framework, Mail, Calendar (Phase 4)

-- 1. Connectors registry
CREATE TABLE IF NOT EXISTS connectors (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    type                TEXT NOT NULL CHECK(type IN ('gmail', 'imap', 'calendar', 'telegram', 'slack', 'discord', 'github', 'feeds', 'custom')),
    enabled             INTEGER NOT NULL DEFAULT 1,
    config_json         TEXT NOT NULL DEFAULT '{}',
    health_status       TEXT NOT NULL DEFAULT 'ok' CHECK(health_status IN ('ok', 'degraded', 'error')),
    health_reason       TEXT NOT NULL DEFAULT '',
    last_sync_at        TEXT,
    last_error          TEXT,
    privacy_class       TEXT NOT NULL DEFAULT 'local_only' CHECK(privacy_class IN ('local_only', 'cloud_allowed')),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_connectors_type ON connectors(type);
CREATE INDEX IF NOT EXISTS idx_connectors_enabled ON connectors(enabled);

-- 2. Mail items (sanitized, local_only by default, capped at 20KB text)
CREATE TABLE IF NOT EXISTS mail_items (
    id                  TEXT PRIMARY KEY,
    account             TEXT NOT NULL,
    message_id          TEXT NOT NULL,
    thread_id           TEXT,
    labels_json         TEXT NOT NULL DEFAULT '[]',
    from_name           TEXT,
    from_domain         TEXT,
    date                TEXT,
    subject             TEXT,
    text_sanitized      TEXT, -- capped at 20KB
    flags_json          TEXT NOT NULL DEFAULT '{}',
    extracted_fields_json TEXT NOT NULL DEFAULT '{}', -- category, deadline, event, action_items
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at          TEXT,
    UNIQUE(account, message_id)
);

CREATE INDEX IF NOT EXISTS idx_mail_items_account ON mail_items(account);
CREATE INDEX IF NOT EXISTS idx_mail_items_date ON mail_items(date);
CREATE INDEX IF NOT EXISTS idx_mail_items_deleted_at ON mail_items(deleted_at);

-- 3. Mail attachments metadata
CREATE TABLE IF NOT EXISTS mail_attachments (
    id                  TEXT PRIMARY KEY,
    mail_item_id        TEXT NOT NULL REFERENCES mail_items(id) ON DELETE CASCADE,
    filename            TEXT NOT NULL,
    content_type        TEXT NOT NULL,
    size_bytes          INTEGER NOT NULL,
    text_extracted      TEXT, -- capped at 1MB
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_mail_attachments_item ON mail_attachments(mail_item_id);

-- 4. Calendar events
CREATE TABLE IF NOT EXISTS calendar_events (
    id                  TEXT PRIMARY KEY,
    account             TEXT NOT NULL,
    event_id            TEXT NOT NULL,
    title               TEXT NOT NULL,
    start_time          TEXT NOT NULL,
    end_time            TEXT NOT NULL,
    location            TEXT NOT NULL DEFAULT '',
    description         TEXT NOT NULL DEFAULT '',
    source              TEXT NOT NULL CHECK(source IN ('gcal', 'ics')),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(account, event_id, source)
);

CREATE INDEX IF NOT EXISTS idx_calendar_events_start ON calendar_events(start_time);
CREATE INDEX IF NOT EXISTS idx_calendar_events_account ON calendar_events(account);
