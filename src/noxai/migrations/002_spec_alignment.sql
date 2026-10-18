-- 002_spec_alignment.sql
-- Add missing columns to approvals table per spec §7

ALTER TABLE approvals ADD COLUMN args_hash TEXT;
ALTER TABLE approvals ADD COLUMN reason TEXT;
ALTER TABLE approvals ADD COLUMN expires_at TEXT;
ALTER TABLE approvals ADD COLUMN decided_at TEXT;
ALTER TABLE approvals ADD COLUMN decided_by TEXT;

-- Rename status check: 'cancelled' -> 'consumed'
-- SQLite cannot modify CHECK constraints; create new table and copy
CREATE TABLE approvals_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name TEXT NOT NULL,
    args_json TEXT NOT NULL,
    args_hash TEXT,
    reason TEXT,
    requested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    expires_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','approved','denied','consumed','expired')),
    decided_at TEXT,
    decided_by TEXT,
    run_id TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);
INSERT INTO approvals_new (id, tool_name, args_json, args_hash, reason, requested_at, expires_at, status, decided_at, decided_by, run_id)
    SELECT id, tool, args_json, args_hash, reason, created_at, expires_at,
           CASE status WHEN 'cancelled' THEN 'denied' ELSE status END,
           decided_at, decided_by, run_id
    FROM approvals;
DROP TABLE approvals;
ALTER TABLE approvals_new RENAME TO approvals;

CREATE INDEX IF NOT EXISTS idx_approvals_run    ON approvals(run_id);
CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status);
