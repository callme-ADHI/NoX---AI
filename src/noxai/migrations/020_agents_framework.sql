-- Migration 020: Agents Framework, Presets, Learning Loop & Untrusted Skills (Phase 7)

-- 1. Agents Table
CREATE TABLE IF NOT EXISTS agents (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    mode                TEXT NOT NULL CHECK(mode IN ('on_demand', 'scheduled', 'continuous')),
    cron_expression     TEXT,
    enabled             INTEGER NOT NULL DEFAULT 1,
    config_json         TEXT NOT NULL DEFAULT '{}',
    prompt_template     TEXT NOT NULL,
    budget_tokens       INTEGER NOT NULL DEFAULT 50000,
    budget_cost_usd     REAL NOT NULL DEFAULT 1.0,
    status              TEXT NOT NULL DEFAULT 'idle' CHECK(status IN ('idle', 'running', 'paused', 'failed', 'stopped')),
    last_run_at         TEXT,
    last_error          TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_agents_mode ON agents(mode);
CREATE INDEX IF NOT EXISTS idx_agents_enabled ON agents(enabled);

-- 2. Agent Execution Runs
CREATE TABLE IF NOT EXISTS agent_runs (
    id                  TEXT PRIMARY KEY,
    agent_id            TEXT NOT NULL,
    trigger             TEXT NOT NULL CHECK(trigger IN ('manual', 'scheduled', 'continuous', 'event')),
    status              TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running', 'succeeded', 'failed', 'cancelled')),
    input_prompt        TEXT NOT NULL,
    output_result       TEXT,
    tokens_used         INTEGER NOT NULL DEFAULT 0,
    cost_usd            REAL NOT NULL DEFAULT 0.0,
    duration_ms         INTEGER NOT NULL DEFAULT 0,
    error_msg           TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    finished_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_agent ON agent_runs(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs(status);
CREATE INDEX IF NOT EXISTS idx_agent_runs_created ON agent_runs(created_at);

-- 3. Untrusted Skill Installations
CREATE TABLE IF NOT EXISTS skill_installations (
    id                      TEXT PRIMARY KEY,
    name                    TEXT NOT NULL UNIQUE,
    source_url              TEXT,
    install_path            TEXT NOT NULL,
    integrity_hash          TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'disabled' CHECK(status IN ('staged', 'installed', 'enabled', 'disabled')),
    security_findings_json  TEXT NOT NULL DEFAULT '[]',
    created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_skill_inst_status ON skill_installations(status);

-- 4. Prompt Optimizations (Learning Loop)
CREATE TABLE IF NOT EXISTS prompt_optimizations (
    id                  TEXT PRIMARY KEY,
    target_type         TEXT NOT NULL,
    target_name         TEXT NOT NULL,
    baseline_prompt     TEXT NOT NULL,
    candidate_prompt    TEXT NOT NULL,
    score_delta         REAL NOT NULL DEFAULT 0.0,
    regression_count    INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'evaluating' CHECK(status IN ('evaluating', 'promoted', 'reverted')),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_prompt_opt_target ON prompt_optimizations(target_type, target_name);
