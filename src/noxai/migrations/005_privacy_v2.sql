-- Migration 005: Privacy Policy v2 (facts visibility and egress ledger)

ALTER TABLE facts ADD COLUMN visibility TEXT NOT NULL DEFAULT 'local_only';

CREATE TABLE IF NOT EXISTS egress_ledger (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    provider            TEXT NOT NULL,
    model               TEXT NOT NULL,
    prompt_tokens_est   INTEGER NOT NULL DEFAULT 0,
    data_class          TEXT NOT NULL,              -- 'public', 'chat', 'local_only', 'secret'
    decision            TEXT NOT NULL,              -- 'allowed', 'blocked', 'rerouted_local'
    reason              TEXT,
    sha256_hash         TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_egress_ledger_decision  ON egress_ledger(decision);
CREATE INDEX IF NOT EXISTS idx_egress_ledger_timestamp ON egress_ledger(timestamp);
