-- Migration 016: Security & Privacy Foundation (Phase 3)
-- Audit events with hash chaining, pending action approvals, and security state

-- 1. Tamper-evident Audit Events
CREATE TABLE IF NOT EXISTS audit_events (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    actor               TEXT NOT NULL, -- 'user', 'agent', 'connector', 'channel', 'system'
    action              TEXT NOT NULL, -- 'cloud_egress', 'read', 'write', 'approval_granted', 'approval_denied', 'approval_revoked', 'panic_engaged', 'panic_resumed', 'permission_denied', 'delete', etc.
    target              TEXT NOT NULL,
    decision            TEXT NOT NULL, -- 'allowed', 'denied', 'blocked', 'approved', 'revoked', 'executed'
    summary_redacted    TEXT NOT NULL,
    prev_hash           TEXT NOT NULL,
    hash                TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_events_ts ON audit_events(ts);
CREATE INDEX IF NOT EXISTS idx_audit_events_actor ON audit_events(actor);
CREATE INDEX IF NOT EXISTS idx_audit_events_action ON audit_events(action);
CREATE INDEX IF NOT EXISTS idx_audit_events_target ON audit_events(target);

-- 2. Scoped Action Approvals Center
CREATE TABLE IF NOT EXISTS pending_approvals (
    id                  TEXT PRIMARY KEY,
    action_type         TEXT NOT NULL,
    payload_json        TEXT NOT NULL,
    preview             TEXT NOT NULL,
    scope               TEXT NOT NULL,
    confirmation_code   TEXT,
    status              TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending', 'approved', 'denied', 'consumed', 'expired', 'revoked')),
    expires_at          TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    resolved_at         TEXT,
    resolved_by         TEXT
);

CREATE INDEX IF NOT EXISTS idx_pending_approvals_status ON pending_approvals(status);
CREATE INDEX IF NOT EXISTS idx_pending_approvals_expires ON pending_approvals(expires_at);

-- 3. Security State (Kill switch and security flags)
CREATE TABLE IF NOT EXISTS security_state (
    key                 TEXT PRIMARY KEY,
    value               TEXT NOT NULL,
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
