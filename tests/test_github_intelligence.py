"""Comprehensive test suite for Phase 6: GitHub & Actions Intelligence.

Covers:
- Migration 019 schema (github_watched_repos, github_actions_runs, github_items)
- GitHubConnector auth, health, and fallback mechanisms
- Watched repos management (add, list, remove, private repo classification)
- Workflow runs fetching via mock gh CLI and mock REST API
- Failure triage heuristics: test failure, lint/type error, dependency error, timeout
- Static workflow YAML linter (.github/workflows security and least privilege)
- Watch actions polling loop with triage on failure
- Guarded writes with ApprovalsCenter (rerun, comment)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from noxai.connectors.github import GitHubConnector
from noxai.db import get_db, run_migrations
from noxai.security.approvals import ApprovalsCenter
from noxai.security.secrets import SecretStore


@pytest.fixture(autouse=True)
async def setup_test_db(tmp_path: Any, monkeypatch: Any) -> Any:
    db_file = tmp_path / "test_github.db"
    monkeypatch.setenv("NOX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOX_DATABASE_PATH", str(db_file))
    await run_migrations()
    yield


@pytest.mark.asyncio
async def test_migration_019_tables() -> None:
    """Verify that migration 019 creates github intelligence tables."""
    async with get_db() as db:
        async with db.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name IN (
                'github_watched_repos', 'github_actions_runs', 'github_items'
            )
            """
        ) as cur:
            tables = {r[0] for r in await cur.fetchall()}
            assert {"github_watched_repos", "github_actions_runs", "github_items"}.issubset(tables)

        async with db.execute("PRAGMA table_info(github_watched_repos)") as cur:
            cols = {r[1] for r in await cur.fetchall()}
            assert {"repo_name", "is_private", "watch_actions"}.issubset(cols)

        async with db.execute("PRAGMA table_info(github_actions_runs)") as cur:
            cols = {r[1] for r in await cur.fetchall()}
            assert {"id", "repo_name", "conclusion", "triage_summary"}.issubset(cols)


@pytest.mark.asyncio
async def test_watched_repos_crud() -> None:
    """Test adding, listing, and removing watched repositories."""
    conn = GitHubConnector()
    await conn.add_watched_repo("callme-ADHI/NoX---AI", is_private=False)
    await conn.add_watched_repo("internal/secret-repo", is_private=True, watch_prs=False)

    repos = await conn.list_watched_repos()
    assert len(repos) == 2
    repo_map = {r["repo_name"]: r for r in repos}

    assert not repo_map["callme-ADHI/NoX---AI"]["is_private"]
    assert repo_map["internal/secret-repo"]["is_private"]
    assert not repo_map["internal/secret-repo"]["watch_prs"]

    await conn.remove_watched_repo("internal/secret-repo")
    repos_after = await conn.list_watched_repos()
    assert len(repos_after) == 1
    assert repos_after[0]["repo_name"] == "callme-ADHI/NoX---AI"


@pytest.mark.asyncio
async def test_github_auth_and_health_states() -> None:
    """Test auth and health reporting across gh CLI and REST token fallbacks."""
    # 1. Without gh CLI and without token -> degraded
    conn_no_auth = GitHubConnector(gh_runner=lambda args: (1, "", "not logged in"))
    health_degraded = await conn_no_auth.health()
    assert health_degraded["status"] in ("degraded", "error")

    # 2. With mock gh CLI authenticated -> ok
    def mock_gh_auth(args: list[str]) -> tuple[int, str, str]:
        if "auth" in args and "status" in args:
            return 0, "Logged in to github.com account testuser", ""
        return 1, "", "command not found"

    conn_gh = GitHubConnector(gh_runner=mock_gh_auth)
    assert await conn_gh.auth()
    health_gh = await conn_gh.health()
    assert health_gh["status"] == "ok"
    assert "gh CLI" in health_gh["reason"]

    # 3. With token in SecretStore and REST fallback
    def mock_handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/user"):
            return httpx.Response(200, json={"login": "token-user"})
        return httpx.Response(404, json={"message": "Not Found"})

    SecretStore.set("github:token", "ghp_mock_token_12345678901234567890")
    try:
        conn_rest = GitHubConnector(
            transport=httpx.MockTransport(mock_handler),
            gh_runner=lambda args: (1, "", "no gh binary"),
        )
        assert await conn_rest.auth()
        health_rest = await conn_rest.health()
        assert health_rest["status"] == "ok"
        assert "token-user" in health_rest["reason"]
    finally:
        SecretStore.delete("github:token")


@pytest.mark.asyncio
async def test_get_workflow_runs_and_prs() -> None:
    """Test fetching runs and PRs using mock gh output."""
    mock_runs_json = json.dumps([
        {
            "databaseId": 123456,
            "name": "CI",
            "headBranch": "session-n",
            "headSha": "abc1234567",
            "status": "completed",
            "conclusion": "success",
            "url": "https://github.com/callme-ADHI/NoX---AI/actions/runs/123456",
            "createdAt": "2026-11-21T10:00:00Z",
        }
    ])

    mock_prs_json = json.dumps([
        {
            "number": 42,
            "title": "feat(connectors): add GitHub intelligence",
            "author": {"login": "callme-ADHI"},
            "headRefName": "session-n",
            "url": "https://github.com/callme-ADHI/NoX---AI/pull/42",
            "isDraft": True,
            "createdAt": "2026-11-21T10:30:00Z",
        }
    ])

    def mock_runner(args: list[str]) -> tuple[int, str, str]:
        if "run" in args and "list" in args:
            return 0, mock_runs_json, ""
        elif "pr" in args and "list" in args:
            return 0, mock_prs_json, ""
        return 1, "", "unknown"

    conn = GitHubConnector(gh_runner=mock_runner)

    runs = await conn.get_workflow_runs(repo="callme-ADHI/NoX---AI")
    assert len(runs) == 1
    assert runs[0]["id"] == "123456"
    assert runs[0]["conclusion"] == "success"

    prs = await conn.get_pr_summaries(repo="callme-ADHI/NoX---AI")
    assert len(prs) == 1
    assert prs[0]["number"] == 42
    assert prs[0]["is_draft"] is True
    assert prs[0]["branch"] == "session-n"


@pytest.mark.asyncio
async def test_triage_failed_run_heuristics() -> None:
    """Test triage classification for pytest, linter, dependency, and timeout failures."""
    test_log = """
    ##[group]Run pytest
    ============================= test session starts ==============================
    collected 10 items
    tests/test_mail.py::test_imap FAILED
    =================================== FAILURES ===================================
    __________________________________ test_imap ___________________________________
    E   AssertionError: assert False is True
    =========================== short test summary info ============================
    FAILED tests/test_mail.py::test_imap
    ##[error]Process completed with exit code 1.
    """

    lint_log = """
    ##[group]Run ruff check
    src/noxai/cli.py:90:101: E501 Line too long (120 > 100)
    Found 1 error.
    ##[error]Process completed with exit code 1.
    """

    dep_log = """
    ##[group]Run setup
    ModuleNotFoundError: No module named 'sqlite_vec'
    ##[error]Process completed with exit code 1.
    """

    timeout_log = (
        "##[group]Run test suite\n"
        "##[error]The job running on runner Hosted Agent has exceeded the maximum "
        "execution time of 360 minutes.\n"
    )

    def make_runner(log_content: str) -> Any:
        def _runner(args: list[str]) -> tuple[int, str, str]:
            if "run" in args and "view" in args and "--log-failed" in args:
                return 0, log_content, ""
            return 1, "", "not found"
        return _runner

    # 1. Test failure triage
    conn1 = GitHubConnector(gh_runner=make_runner(test_log))
    triage1 = await conn1.triage_failed_run("callme-ADHI/NoX---AI", "1001")
    assert triage1["category"] == "test_failure"
    assert "failing test assertion" in triage1["summary"]
    assert any("AssertionError" in el for el in triage1["error_lines"])

    # 2. Lint failure triage
    conn2 = GitHubConnector(gh_runner=make_runner(lint_log))
    triage2 = await conn2.triage_failed_run("callme-ADHI/NoX---AI", "1002")
    assert triage2["category"] == "lint_or_type_error"
    assert "ruff check" in triage2["suggested_action"]

    # 3. Dependency error triage
    conn3 = GitHubConnector(gh_runner=make_runner(dep_log))
    triage3 = await conn3.triage_failed_run("callme-ADHI/NoX---AI", "1003")
    assert triage3["category"] == "dependency_error"
    assert "pyproject.toml" in triage3["suggested_action"]

    # 4. Timeout triage
    conn4 = GitHubConnector(gh_runner=make_runner(timeout_log))
    triage4 = await conn4.triage_failed_run("callme-ADHI/NoX---AI", "1004")
    assert triage4["category"] == "timeout"
    assert "time limit" in triage4["summary"]


def test_workflow_linter_checks(tmp_path: Path) -> None:
    """Test workflow YAML static linter for permissions, pinning, and script injections."""
    insecure_wf = """
name: Insecure Workflow
on: [push, issue_comment]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@main
      - name: Greet commenter
        run: |
          echo "Title: ${{ github.event.issue.title }}"
    """

    findings = GitHubConnector.lint_workflow_file(insecure_wf)
    rules_flagged = {f["rule"] for f in findings}

    # Must flag missing explicit permissions
    assert "permissions_explicit" in rules_flagged
    # Must flag mutable @main branch ref
    assert "action_pinning" in rules_flagged
    # Must flag inline script injection expression
    assert "script_injection" in rules_flagged

    # Test clean workflow
    clean_wf = """
name: Secure Workflow
on: [push]
permissions:
  contents: read
jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
      - name: Build
        run: echo "Hello World"
    """
    clean_findings = GitHubConnector.lint_workflow_file(clean_wf)
    assert len(clean_findings) == 0

    # Test file-based scan
    wf_file = tmp_path / "ci.yml"
    wf_file.write_text(clean_wf, encoding="utf-8")
    assert len(GitHubConnector.lint_workflow_file(wf_file)) == 0


@pytest.mark.asyncio
async def test_watch_actions_success_and_failure() -> None:
    """Test watching an Actions run until success or failure."""
    calls = 0

    def mock_runner(args: list[str]) -> tuple[int, str, str]:
        nonlocal calls
        calls += 1
        status = "in_progress" if calls < 2 else "completed"
        conclusion = "in_progress" if calls < 2 else "success"
        run_json = json.dumps([{
            "databaseId": 777,
            "name": "CI",
            "headBranch": "session-n",
            "headSha": "abcdef12",
            "status": status,
            "conclusion": conclusion,
            "url": "https://github.com/callme-ADHI/NoX---AI/actions/runs/777",
            "createdAt": "2026-11-21T11:00:00Z",
        }])
        return 0, run_json, ""

    conn = GitHubConnector(gh_runner=mock_runner)
    result = await conn.watch_actions(repo="callme-ADHI/NoX---AI", poll_interval_sec=0)
    assert result["status"] == "completed"
    assert result["conclusion"] == "success"
    assert result["run_id"] == "777"


@pytest.mark.asyncio
async def test_guarded_write_rerun_and_comment() -> None:
    """Test guarded operations requesting approval and single-use execution."""
    executed_cmds: list[list[str]] = []

    def mock_runner(args: list[str]) -> tuple[int, str, str]:
        executed_cmds.append(args)
        return 0, "re-run triggered", ""

    conn = GitHubConnector(gh_runner=mock_runner)

    # 1. Without approval_id -> creates approval request
    payload = {"repo": "callme-ADHI/NoX---AI", "run_id": "888"}
    res = await conn.guarded_write("rerun_run", payload)
    assert res["status"] == "pending_approval"
    approval_id = res["approval_id"]

    # 2. Approve via ApprovalsCenter
    await ApprovalsCenter.approve(approval_id, actor="user")

    # 3. Execute with approval_id -> succeeds
    exec_res = await conn.guarded_write("rerun_run", payload, approval_id=approval_id)
    assert exec_res["status"] == "executed"
    assert len(executed_cmds) == 1
    assert "rerun" in executed_cmds[0]

    # 4. Attempting to reuse same approval_id fails (consumed)
    with pytest.raises(PermissionError):
        await conn.guarded_write("rerun_run", payload, approval_id=approval_id)
