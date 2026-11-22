-- Migration 018: Channels and Social Subsystems (Phase 5)

-- 1. Channels (Telegram, Slack, Discord)
CREATE TABLE IF NOT EXISTS channels (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    type                TEXT NOT NULL CHECK(type IN ('telegram', 'slack', 'discord', 'custom')),
    enabled             INTEGER NOT NULL DEFAULT 1,
    config_json         TEXT NOT NULL DEFAULT '{}',
    pairing_code        TEXT,
    owner_id            TEXT,
    allowlist_json      TEXT NOT NULL DEFAULT '[]',
    health_status       TEXT NOT NULL DEFAULT 'ok' CHECK(health_status IN ('ok', 'degraded', 'error')),
    health_reason       TEXT NOT NULL DEFAULT '',
    last_seen_at        TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_channels_type ON channels(type);
CREATE INDEX IF NOT EXISTS idx_channels_enabled ON channels(enabled);

-- 2. Social Items (Feeds, RSS, Bluesky, Mastodon, Reddit, Slack, Discord)
CREATE TABLE IF NOT EXISTS social_items (
    id                  TEXT PRIMARY KEY,
    source              TEXT NOT NULL,
    channel_or_feed     TEXT NOT NULL,
    author              TEXT NOT NULL,
    title               TEXT,
    content_sanitized   TEXT NOT NULL,
    url                 TEXT,
    published_at        TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    deleted_at          TEXT
);

CREATE INDEX IF NOT EXISTS idx_social_items_source ON social_items(source);
CREATE INDEX IF NOT EXISTS idx_social_items_published ON social_items(published_at);
CREATE INDEX IF NOT EXISTS idx_social_items_deleted ON social_items(deleted_at);
