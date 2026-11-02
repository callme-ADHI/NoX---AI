-- Migration 010: Converge Settings and Briefing Quality Gates (Session K)
-- Reconciles settings table and ensures cloud.enabled default converges
-- whether a database ran original migration 009 or modified migration 009.

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT OR IGNORE INTO settings (key, value) VALUES ('cloud.enabled', 'true');
