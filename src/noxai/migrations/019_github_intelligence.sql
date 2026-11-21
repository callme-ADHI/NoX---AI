-- Migration 019: GitHub and Actions Intelligence (Phase 6)

-- 1. Watched Repositories
CREATE TABLE IF NOT EXISTS github_watched_repos (
    repo_name           TEXT PRIMARY KEY,
    is_private          INTEGER NOT NULL DEFAULT 0,
    watch_actions       INTEGER NOT NULL DEFAULT 1,
    watch_prs           INTEGER NOT NULL DEFAULT 1,
    watch_issues        INTEGER NOT NULL DEFAULT 1,
    last_check_at       TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_gh_watched_repos_private ON github_watched_repos(is_private);

-- 2. GitHub Actions Runs & Triage
CREATE TABLE IF NOT EXISTS github_actions_runs (
    id                  TEXT PRIMARY KEY,
    repo_name           TEXT NOT NULL,
    workflow_name       TEXT NOT NULL,
    head_branch         TEXT NOT NULL,
    head_sha            TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'completed',
    conclusion          TEXT,
    html_url            TEXT,
    run_started_at      TEXT,
    failure_step        TEXT,
    triage_summary      TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_gh_actions_repo ON github_actions_runs(repo_name);
CREATE INDEX IF NOT EXISTS idx_gh_actions_conclusion ON github_actions_runs(conclusion);
CREATE INDEX IF NOT EXISTS idx_gh_actions_started ON github_actions_runs(run_started_at);

-- 3. GitHub PRs, Issues, and Alerts
CREATE TABLE IF NOT EXISTS github_items (
    id                  TEXT PRIMARY KEY,
    repo_name           TEXT NOT NULL,
    item_type           TEXT NOT NULL CHECK(item_type IN ('pr', 'issue', 'alert', 'dependabot')),
    number              INTEGER,
    title               TEXT NOT NULL,
    author              TEXT NOT NULL,
    state               TEXT NOT NULL DEFAULT 'open',
    html_url            TEXT,
    summary             TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_gh_items_repo ON github_items(repo_name);
CREATE INDEX IF NOT EXISTS idx_gh_items_type ON github_items(item_type);
CREATE INDEX IF NOT EXISTS idx_gh_items_state ON github_items(state);
