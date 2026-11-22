-- Migration 021: Expand connector health status check constraints for honest operational states

CREATE TABLE IF NOT EXISTS connectors_new (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    type                TEXT NOT NULL CHECK(type IN ('gmail', 'imap', 'calendar', 'telegram', 'slack', 'discord', 'github', 'feeds', 'custom')),
    enabled             INTEGER NOT NULL DEFAULT 1,
    config_json         TEXT NOT NULL DEFAULT '{}',
    health_status       TEXT NOT NULL DEFAULT 'NOT_CONFIGURED' CHECK(health_status IN ('ok', 'degraded', 'error', 'NOT_CONFIGURED', 'CONFIGURED_UNTESTED', 'HEALTHY', 'DEGRADED', 'AUTH_REQUIRED', 'FAILED', 'DISABLED')),
    health_reason       TEXT NOT NULL DEFAULT '',
    last_sync_at        TEXT,
    last_error          TEXT,
    privacy_class       TEXT NOT NULL DEFAULT 'local_only' CHECK(privacy_class IN ('local_only', 'cloud_allowed')),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT INTO connectors_new SELECT id, name, type, enabled, config_json,
    CASE
        WHEN health_status = 'ok' THEN 'NOT_CONFIGURED'
        ELSE health_status
    END,
    health_reason, last_sync_at, last_error, privacy_class, created_at, updated_at
FROM connectors;

DROP TABLE connectors;
ALTER TABLE connectors_new RENAME TO connectors;

CREATE INDEX IF NOT EXISTS idx_connectors_type ON connectors(type);
CREATE INDEX IF NOT EXISTS idx_connectors_enabled ON connectors(enabled);
