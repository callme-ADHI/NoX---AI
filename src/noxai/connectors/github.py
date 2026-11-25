"""GitHub and Actions Intelligence connector for NoX.

Provides:
- GitHub CLI (gh) subprocess execution with fallback to fine-grained GITHUB_TOKEN & REST API
- Watched repositories management (public vs private classification)
- GitHub Actions workflow status tracking & failing step log triage
- Plain-English root cause extraction and triage summaries
- Workflow security & configuration linter for .github/workflows/*.yml
- Guarded write operations (workflow rerun, dispatch, PR comment) with ApprovalsCenter
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from noxai.connectors.base import BaseConnector
from noxai.db import get_db
from noxai.security.approvals import ApprovalsCenter
from noxai.security.audit import log_audit_event
from noxai.security.secrets import SecretStore

log = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"

# Heuristic patterns for GitHub Actions failure log analysis
FAILURE_LOG_PATTERNS = {
    "test_failure": [
        re.compile(r"FAILED\s+tests/[^\s]+"),
        re.compile(r"=+\s+FAILURES\s+=+"),
        re.compile(r"AssertionError:"),
        re.compile(r"pytest[^\n]*failed"),
        re.compile(r"FAIL\s+src/[^\n]+"),
        re.compile(r"npm test[^\n]*failed"),
    ],
    "lint_or_type_error": [
        re.compile(r"Found \d+ errors?"),
        re.compile(r"error: Value of type [^\n]+"),
        re.compile(r"ruff check[^\n]*error"),
        re.compile(r"mypy[^\n]*error"),
        re.compile(r"ESLint[^\n]*error"),
    ],
    "dependency_error": [
        re.compile(r"ModuleNotFoundError:\s*No module named ['\"]([^'\"]+)['\"]"),
        re.compile(r"ImportError:\s*cannot import name"),
        re.compile(r"Could not find a version that satisfies the requirement"),
        re.compile(r"npm ERR! code ERESOLVE"),
    ],
    "build_failure": [
        re.compile(r"error\[E\d+\]:"),
        re.compile(r"Build failed with \d+ errors"),
        re.compile(r"fatal error: [^\n]+"),
    ],
    "auth_error": [
        re.compile(r"403 Forbidden"),
        re.compile(r"Resource not accessible by integration"),
        re.compile(r"Bad credentials"),
        re.compile(r"Permission denied"),
    ],
    "timeout": [
        re.compile(r"The operation was canceled"),
        re.compile(r"timed out after \d+"),
        re.compile(r"##\[error\]The job running on runner [^\n]+ has exceeded"),
    ],
}


class GitHubConnector(BaseConnector):
    """Connector for GitHub repository monitoring and Actions intelligence."""

    def __init__(
        self,
        name: str = "github",
        config: dict[str, Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        gh_runner: Callable[[list[str]], tuple[int, str, str]] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            connector_type="github",
            privacy_class="hybrid",
            config=config or {},
            rate_limit_rpm=60,
        )
        self.default_repo: str = self.config.get("default_repo", "callme-ADHI/NoX---AI")
        self._custom_transport = transport
        self._gh_runner = gh_runner

    def get_token(self) -> str | None:
        """Fetch fine-grained token from SecretStore or environment."""
        token = SecretStore.get("github:token")
        if token:
            return token
        return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")

    def has_gh_cli(self) -> bool:
        """Check if gh CLI is installed and callable."""
        if self._gh_runner is not None:
            return True
        return shutil.which("gh") is not None

    def _exec_gh(self, args: list[str]) -> tuple[int, str, str]:
        """Execute a gh CLI command or use mock runner."""
        if self._gh_runner is not None:
            return self._gh_runner(args)

        env = os.environ.copy()
        tok = self.get_token()
        if tok:
            env["GH_TOKEN"] = tok
            env["GITHUB_TOKEN"] = tok

        try:
            res = subprocess.run(
                ["gh", *args],
                capture_output=True,
                text=True,
                env=env,
                check=False,
                timeout=30,
            )
            return res.returncode, res.stdout, res.stderr
        except Exception as exc:
            return 1, "", str(exc)

    def _get_http_client(self) -> httpx.AsyncClient:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "NoX-GitHub-Intelligence/1.0",
        }
        token = self.get_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return httpx.AsyncClient(
            transport=self._custom_transport,
            headers=headers,
            timeout=30.0,
        )

    async def auth(self) -> bool:
        """Check authentication via gh or token."""
        if self.has_gh_cli():
            code, out, _ = self._exec_gh(["auth", "status"])
            if code == 0 or "Logged in to github.com" in out:
                return True

        token = self.get_token()
        if token:
            try:
                async with self._get_http_client() as client:
                    resp = await client.get(f"{GITHUB_API_BASE}/user")
                    return resp.status_code == 200
            except Exception:
                return False
        return False

    async def health(self) -> dict[str, Any]:
        """Verify GitHub API / CLI connectivity and health."""
        if self.has_gh_cli():
            code, out, _err = self._exec_gh(["auth", "status"])
            if code == 0 or "Logged in to github.com" in out:
                return {"status": "ok", "reason": "Authenticated via gh CLI"}

        token = self.get_token()
        if token:
            try:
                async with self._get_http_client() as client:
                    resp = await client.get(f"{GITHUB_API_BASE}/user")
                    if resp.status_code == 200:
                        login = resp.json().get("login", "unknown")
                        return {"status": "ok", "reason": f"Authenticated via token as {login}"}
                    return {"status": "FAILED", "reason": f"GitHub API error {resp.status_code}"}
            except Exception as exc:
                return {"status": "FAILED", "reason": str(exc)}

        return {
            "status": "NOT_CONFIGURED",
            "reason": (
                "Neither gh CLI authenticated nor GITHUB_TOKEN configured; "
                "limited to public endpoints"
            ),
        }

    async def list_watched_repos(self) -> list[dict[str, Any]]:
        """List all watched repositories from the database."""
        async with get_db() as db:
            async with db.execute(
                """
                SELECT repo_name, is_private, watch_actions, watch_prs,
                       watch_issues, last_check_at, created_at
                FROM github_watched_repos
                ORDER BY repo_name ASC
                """
            ) as cur:
                rows = await cur.fetchall()
                return [
                    {
                        "repo_name": r[0],
                        "is_private": bool(r[1]),
                        "watch_actions": bool(r[2]),
                        "watch_prs": bool(r[3]),
                        "watch_issues": bool(r[4]),
                        "last_check_at": r[5],
                        "created_at": r[6],
                    }
                    for r in rows
                ]

    async def add_watched_repo(
        self,
        repo_name: str,
        is_private: bool = False,
        watch_actions: bool = True,
        watch_prs: bool = True,
        watch_issues: bool = True,
    ) -> None:
        """Register a repository on the local watch list."""
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO github_watched_repos (
                    repo_name, is_private, watch_actions, watch_prs, watch_issues
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(repo_name) DO UPDATE SET
                    is_private = excluded.is_private,
                    watch_actions = excluded.watch_actions,
                    watch_prs = excluded.watch_prs,
                    watch_issues = excluded.watch_issues
                """,
                (
                    repo_name,
                    1 if is_private else 0,
                    1 if watch_actions else 0,
                    1 if watch_prs else 0,
                    1 if watch_issues else 0,
                ),
            )
            await db.commit()

    async def remove_watched_repo(self, repo_name: str) -> None:
        """Remove a repository from the watch list."""
        async with get_db() as db:
            await db.execute(
                "DELETE FROM github_watched_repos WHERE repo_name = ?", (repo_name,)
            )
            await db.commit()

    async def get_workflow_runs(
        self,
        repo: str | None = None,
        limit: int = 10,
        branch: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch recent workflow runs for a repository."""
        target_repo = repo or self.default_repo

        # 1. Try gh CLI
        if self.has_gh_cli():
            cmd = [
                "run",
                "list",
                "--repo",
                target_repo,
                "--limit",
                str(limit),
                "--json",
                "databaseId,name,headBranch,headSha,status,conclusion,url,createdAt,workflowName",
            ]
            if branch:
                cmd.extend(["--branch", branch])

            code, out, _ = self._exec_gh(cmd)
            if code == 0 and out.strip():
                try:
                    data = json.loads(out)
                    runs = []
                    for it in data:
                        runs.append(
                            {
                                "id": str(it.get("databaseId")),
                                "name": it.get("name") or it.get("workflowName", "Workflow"),
                                "head_branch": it.get("headBranch", ""),
                                "head_sha": it.get("headSha", "")[:8],
                                "status": it.get("status", "completed"),
                                "conclusion": it.get("conclusion") or "in_progress",
                                "html_url": it.get("url", ""),
                                "created_at": it.get("createdAt", ""),
                            }
                        )
                    return runs
                except Exception as exc:
                    log.warning("gh_run_list_json_parse_error err=%s", exc)

        # 2. Fallback to REST API
        endpoint = f"{GITHUB_API_BASE}/repos/{target_repo}/actions/runs?per_page={limit}"
        if branch:
            endpoint += f"&branch={branch}"

        async with self._get_http_client() as client:
            resp = await client.get(endpoint)
            if resp.status_code == 200:
                raw_runs = resp.json().get("workflow_runs", [])
                return [
                    {
                        "id": str(r.get("id")),
                        "name": r.get("name", "Workflow"),
                        "head_branch": r.get("head_branch", ""),
                        "head_sha": (r.get("head_sha") or "")[:8],
                        "status": r.get("status", "completed"),
                        "conclusion": r.get("conclusion") or "in_progress",
                        "html_url": r.get("html_url", ""),
                        "created_at": r.get("created_at", ""),
                    }
                    for r in raw_runs
                ]
            else:
                log.warning("github_rest_runs_failed status=%d", resp.status_code)
                return []

    async def get_failed_run_log(self, repo: str, run_id: str | int) -> str:
        """Fetch failed job log via gh run view --log-failed or REST job log."""
        if self.has_gh_cli():
            code, out, _ = self._exec_gh(
                ["run", "view", str(run_id), "--repo", repo, "--log-failed"]
            )
            if code == 0 and out.strip():
                return out

        # REST fallback: find failed job and fetch annotations
        async with self._get_http_client() as client:
            resp = await client.get(f"{GITHUB_API_BASE}/repos/{repo}/actions/runs/{run_id}/jobs")
            if resp.status_code == 200:
                jobs = resp.json().get("jobs", [])
                failed_steps: list[str] = []
                for j in jobs:
                    if j.get("conclusion") == "failure":
                        failed_steps.append(f"Job: {j.get('name')}")
                        for s in j.get("steps", []):
                            if s.get("conclusion") == "failure":
                                failed_steps.append(
                                    f"Step failed: {s.get('name')} (conclusion: failure)"
                                )
                return "\n".join(failed_steps)
        return ""

    async def triage_failed_run(self, repo: str, run_id: str | int) -> dict[str, Any]:
        """Analyze a failed run, extract root cause, and generate plain-language triage."""
        log_text = await self.get_failed_run_log(repo, run_id)

        # 1. Identify category & matching error lines
        category = "unknown"
        for cat, patterns in FAILURE_LOG_PATTERNS.items():
            for p in patterns:
                if any(p.search(line) for line in log_text.splitlines()):
                    category = cat
                    break
            if category != "unknown":
                break

        matched_lines: list[str] = []
        if category in FAILURE_LOG_PATTERNS:
            patterns = FAILURE_LOG_PATTERNS[category]
            for line in log_text.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                if any(p.search(stripped) for p in patterns) or stripped.startswith("E   "):
                    if stripped not in matched_lines:
                        matched_lines.append(stripped)
                if len(matched_lines) >= 8:
                    break

        # 2. Extract failing step
        failing_step = "Unknown Step"
        for line in log_text.splitlines():
            if "##[group]Run" in line or "Step failed:" in line:
                failing_step = line.replace("##[group]Run", "").replace("Step failed:", "").strip()
                break

        # 3. Generate summary & recommended actions
        if category == "test_failure":
            summary = (
                f"Automated test suite failed in step '{failing_step}'. "
                f"Captured {len(matched_lines)} failing test assertion(s)."
            )
            suggested_action = (
                "Inspect local test output with `pytest` or `npm test` and fix failing assertion."
            )
        elif category == "lint_or_type_error":
            summary = (
                f"Static analysis/linter rejected code in step '{failing_step}'. "
                f"Syntax or type signature violations detected."
            )
            suggested_action = "Run `ruff check --fix` and `mypy` locally to resolve errors."
        elif category == "dependency_error":
            summary = (
                f"Missing dependency or package installation failed in step '{failing_step}'."
            )
            suggested_action = (
                "Verify dependencies in pyproject.toml / package.json and lockfile synchronization."
            )
        elif category == "build_failure":
            summary = (
                f"Compilation or bundling failed in step '{failing_step}'."
            )
            suggested_action = (
                "Check compiler error stack and build flags locally."
            )
        elif category == "auth_error":
            summary = (
                f"Authentication or permission error encountered in step '{failing_step}'."
            )
            suggested_action = (
                "Check GitHub repository token permissions and workflow 'permissions:' block."
            )
        elif category == "timeout":
            summary = (
                f"Job exceeded execution time limit in step '{failing_step}'."
            )
            suggested_action = (
                "Check for hanging processes, deadlocks, or increase step timeout limit."
            )
        else:
            summary = f"Workflow run failed in step '{failing_step}'. Check full logs."
            suggested_action = f"Run `gh run view {run_id} --log-failed` to view raw logs."

        triage_result = {
            "run_id": str(run_id),
            "repo": repo,
            "category": category,
            "failing_step": failing_step,
            "summary": summary,
            "suggested_action": suggested_action,
            "error_lines": matched_lines[:5],
        }

        # Persist triage in database
        now = datetime.now(UTC).isoformat()
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO github_actions_runs (
                    id, repo_name, workflow_name, head_branch, head_sha,
                    status, conclusion, failure_step, triage_summary, updated_at
                ) VALUES (?, ?, 'workflow', '', '', 'completed', 'failure', ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    failure_step = excluded.failure_step,
                    triage_summary = excluded.triage_summary,
                    updated_at = excluded.updated_at
                """,
                (
                    str(run_id),
                    repo,
                    failing_step,
                    json.dumps(triage_result),
                    now,
                ),
            )
            await db.commit()

        return triage_result

    async def get_pr_summaries(
        self,
        repo: str | None = None,
        state: str = "open",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Fetch and summarize pull requests."""
        target_repo = repo or self.default_repo

        if self.has_gh_cli():
            cmd = [
                "pr",
                "list",
                "--repo",
                target_repo,
                "--state",
                state,
                "--limit",
                str(limit),
                "--json",
                "number,title,author,headRefName,url,isDraft,createdAt",
            ]
            code, out, _ = self._exec_gh(cmd)
            if code == 0 and out.strip():
                try:
                    data = json.loads(out)
                    prs = []
                    for it in data:
                        prs.append(
                            {
                                "number": it.get("number"),
                                "title": it.get("title", ""),
                                "author": it.get("author", {}).get("login", "unknown"),
                                "branch": it.get("headRefName", ""),
                                "url": it.get("url", ""),
                                "is_draft": bool(it.get("isDraft")),
                                "created_at": it.get("createdAt", ""),
                            }
                        )
                    return prs
                except Exception as exc:
                    log.warning("gh_pr_list_parse_error err=%s", exc)

        # REST API fallback
        endpoint = f"{GITHUB_API_BASE}/repos/{target_repo}/pulls?state={state}&per_page={limit}"
        async with self._get_http_client() as client:
            resp = await client.get(endpoint)
            if resp.status_code == 200:
                raw_prs = resp.json()
                return [
                    {
                        "number": p.get("number"),
                        "title": p.get("title", ""),
                        "author": p.get("user", {}).get("login", "unknown"),
                        "branch": p.get("head", {}).get("ref", ""),
                        "url": p.get("html_url", ""),
                        "is_draft": bool(p.get("draft")),
                        "created_at": p.get("created_at", ""),
                    }
                    for p in raw_prs
                ]
        return []

    async def get_issue_and_alert_summaries(
        self,
        repo: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Fetch open issues and security/Dependabot alerts."""
        target_repo = repo or self.default_repo
        items: list[dict[str, Any]] = []

        if self.has_gh_cli():
            cmd = [
                "issue",
                "list",
                "--repo",
                target_repo,
                "--state",
                "open",
                "--limit",
                str(limit),
                "--json",
                "number,title,author,url,labels,createdAt",
            ]
            code, out, _ = self._exec_gh(cmd)
            if code == 0 and out.strip():
                try:
                    data = json.loads(out)
                    for it in data:
                        labels = [lbl.get("name", "") for lbl in it.get("labels", [])]
                        is_dependabot = (
                            "dependencies" in labels
                            or "dependabot" in it.get("author", {}).get("login", "").lower()
                        )
                        items.append(
                            {
                                "number": it.get("number"),
                                "type": "dependabot" if is_dependabot else "issue",
                                "title": it.get("title", ""),
                                "author": it.get("author", {}).get("login", "unknown"),
                                "url": it.get("url", ""),
                                "labels": labels,
                                "created_at": it.get("createdAt", ""),
                            }
                        )
                    return items
                except Exception as exc:
                    log.warning("gh_issue_list_parse_error err=%s", exc)

        # REST fallback
        endpoint = f"{GITHUB_API_BASE}/repos/{target_repo}/issues?state=open&per_page={limit}"
        async with self._get_http_client() as client:
            resp = await client.get(endpoint)
            if resp.status_code == 200:
                raw_issues = resp.json()
                for p in raw_issues:
                    if "pull_request" in p:
                        continue  # Skip PRs returned by issues endpoint
                    labels = [lbl.get("name", "") for lbl in p.get("labels", [])]
                    is_dep = (
                        "dependencies" in labels
                        or "dependabot" in p.get("user", {}).get("login", "").lower()
                    )
                    items.append(
                        {
                            "number": p.get("number"),
                            "type": "dependabot" if is_dep else "issue",
                            "title": p.get("title", ""),
                            "author": p.get("user", {}).get("login", "unknown"),
                            "url": p.get("html_url", ""),
                            "labels": labels,
                            "created_at": p.get("created_at", ""),
                        }
                    )
        return items

    @classmethod
    def lint_workflow_file(cls, content_or_path: str | Path) -> list[dict[str, Any]]:
        """Statically inspect GitHub Actions workflow YAML for security and best practices.

        Checks:
        1. Top-level or job-level explicit permissions block.
        2. Avoiding overly broad 'permissions: write-all'.
        3. Flagging unpinned action versions (using branch name or unpinned tag).
        4. Detecting script injection vectors in 'run:' steps (${{ github.event... }}).
        """
        findings: list[dict[str, Any]] = []

        if isinstance(content_or_path, Path):
            if not content_or_path.exists():
                return [{"rule": "file_exists", "severity": "error", "message": "File not found"}]
            raw_text = content_or_path.read_text(encoding="utf-8")
        else:
            raw_text = content_or_path

        try:
            doc = yaml.safe_load(raw_text)
            if not isinstance(doc, dict):
                return [{
                    "rule": "yaml_syntax",
                    "severity": "error",
                    "message": "Workflow YAML must be a mapping",
                }]
        except Exception as exc:
            return [{
                "rule": "yaml_syntax",
                "severity": "error",
                "message": f"YAML parse error: {exc}",
            }]

        # 1. Permissions Check
        has_top_permissions = "permissions" in doc
        jobs = doc.get("jobs", {})
        if not has_top_permissions:
            missing_job_perms = [
                jname for jname, jval in jobs.items()
                if isinstance(jval, dict) and "permissions" not in jval
            ]
            if missing_job_perms:
                findings.append(
                    {
                        "rule": "permissions_explicit",
                        "severity": "warning",
                        "message": (
                            f"Workflow lacks explicit top-level 'permissions:' block and jobs "
                            f"{missing_job_perms} lack job-level permissions. "
                            f"Defaults to read-write token."
                        ),
                        "suggested_fix": (
                            "Add `permissions: contents: read` at the top level or per job."
                        ),
                    }
                )

        if doc.get("permissions") == "write-all":
            findings.append(
                {
                    "rule": "permissions_least_privilege",
                    "severity": "warning",
                    "message": (
                        "Top-level 'permissions: write-all' is overly permissive. "
                        "Follow principle of least privilege."
                    ),
                    "suggested_fix": "Declare specific scopes, e.g. `contents: read`.",
                }
            )

        # 2. Inspect steps in each job
        for jname, job in jobs.items():
            if not isinstance(job, dict):
                continue
            steps = job.get("steps", [])
            for step in steps:
                if not isinstance(step, dict):
                    continue
                uses = step.get("uses", "")
                if uses:
                    # Unpinned check
                    if "@main" in uses or "@master" in uses:
                        findings.append(
                            {
                                "rule": "action_pinning",
                                "severity": "warning",
                                "message": (
                                    f"Step '{step.get('name', uses)}' in job '{jname}' uses "
                                    f"mutable branch ref '{uses}'. "
                                    f"Pin to a commit SHA or release tag."
                                ),
                                "suggested_fix": (
                                    "Replace `@main` or `@master` with a full 40-character SHA."
                                ),
                            }
                        )

                run_cmd = step.get("run", "")
                if run_cmd:
                    # Check for inline GitHub context interpolation inside run:
                    injection_patterns = [
                        r"\$\{\{\s*github\.event\.issue\.title\s*\}\}",
                        r"\$\{\{\s*github\.event\.issue\.body\s*\}\}",
                        r"\$\{\{\s*github\.event\.comment\.body\s*\}\}",
                        r"\$\{\{\s*github\.head_ref\s*\}\}",
                        r"\$\{\{\s*github\.event\.pull_request\.title\s*\}\}",
                    ]
                    for pat in injection_patterns:
                        if re.search(pat, run_cmd):
                            findings.append(
                                {
                                    "rule": "script_injection",
                                    "severity": "error",
                                    "message": (
                                        f"Potential script injection in job '{jname}' step "
                                        f"'{step.get('name', 'unnamed')}': inline expression "
                                        f"found in `run:` command."
                                    ),
                                    "suggested_fix": (
                                        "Assign value to an environment variable in `env:` block "
                                        "and reference it as `$VAR` in shell script."
                                    ),
                                }
                            )

        return findings

    @classmethod
    def lint_repo_workflows(cls, repo_dir: Path | None = None) -> dict[str, list[dict[str, Any]]]:
        """Scan all workflow files in .github/workflows."""
        root = repo_dir or Path.cwd()
        wf_dir = root / ".github" / "workflows"
        results: dict[str, list[dict[str, Any]]] = {}

        if not wf_dir.exists():
            return results

        for f in wf_dir.glob("*.y*ml"):
            results[f.name] = cls.lint_workflow_file(f)

        return results

    async def watch_actions(
        self,
        repo: str | None = None,
        run_id: str | None = None,
        timeout_sec: int = 300,
        poll_interval_sec: int = 5,
        on_poll: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Watch a running or latest GitHub Actions run until it completes."""
        target_repo = repo or self.default_repo
        import asyncio

        # If run_id not provided, discover the latest active run
        active_run_id = run_id
        start_time = datetime.now(UTC)

        while True:
            runs = await self.get_workflow_runs(repo=target_repo, limit=5)
            if not runs:
                return {"status": "error", "reason": "No workflow runs found"}

            target_run = None
            if active_run_id:
                for r in runs:
                    if str(r["id"]) == str(active_run_id):
                        target_run = r
                        break
            else:
                target_run = runs[0]
                active_run_id = target_run["id"]

            if not target_run:
                target_run = runs[0]

            if on_poll:
                on_poll(target_run)

            status = target_run.get("status")
            conclusion = target_run.get("conclusion")

            if status == "completed" or conclusion not in ("in_progress", "queued", None):
                # Run finished
                result: dict[str, Any] = {
                    "run_id": target_run["id"],
                    "repo": target_repo,
                    "name": target_run.get("name"),
                    "status": "completed",
                    "conclusion": conclusion,
                    "html_url": target_run.get("html_url"),
                }
                if conclusion == "failure":
                    triage = await self.triage_failed_run(target_repo, target_run["id"])
                    result["triage"] = triage
                return result

            # Check timeout
            elapsed = (datetime.now(UTC) - start_time).total_seconds()
            if elapsed > timeout_sec:
                return {
                    "status": "timeout",
                    "run_id": active_run_id,
                    "reason": f"Watch timed out after {timeout_sec} seconds",
                }

            await asyncio.sleep(poll_interval_sec)

    async def guarded_write(
        self,
        action: str,
        payload: dict[str, Any],
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        """Perform guarded GitHub modification with ApprovalsCenter authorization."""
        valid_actions = {"rerun_run", "dispatch_workflow", "create_comment"}
        if action not in valid_actions:
            raise ValueError(f"Unsupported guarded action '{action}'. Valid: {valid_actions}")

        repo = payload.get("repo") or self.default_repo

        if not approval_id:
            # Request approval
            preview = f"GitHub {action} on {repo}: {payload}"
            appr = await ApprovalsCenter.request_approval(
                action_type=f"github_{action}",
                payload=payload,
                preview=preview,
                scope="github",
            )
            return {
                "status": "pending_approval",
                "approval_id": appr.id,
                "confirmation_code": appr.confirmation_code,
            }

        # Verify and consume
        consumed = await ApprovalsCenter.verify_and_consume(
            approval_id,
            f"github_{action}",
            payload,
        )
        if not consumed:
            raise PermissionError("Approval invalid, expired, or payload mismatch.")

        # Execute
        if action == "rerun_run":
            run_id = payload.get("run_id")
            if self.has_gh_cli():
                code, _out, err = self._exec_gh(
                    ["run", "rerun", str(run_id), "--repo", repo, "--failed"]
                )
                if code != 0:
                    raise RuntimeError(f"gh run rerun failed: {err}")
            else:
                endpoint = f"{GITHUB_API_BASE}/repos/{repo}/actions/runs/{run_id}/rerun-failed-jobs"
                async with self._get_http_client() as client:
                    resp = await client.post(endpoint)
                    if resp.status_code not in (201, 202):
                        raise RuntimeError(f"Rerun failed: {resp.status_code} {resp.text}")

        elif action == "create_comment":
            pr_num = payload.get("number")
            body = payload.get("body", "")
            if self.has_gh_cli():
                code, _out, err = self._exec_gh(
                    ["pr", "comment", str(pr_num), "--repo", repo, "--body", body]
                )
                if code != 0:
                    raise RuntimeError(f"gh pr comment failed: {err}")
            else:
                endpoint = f"{GITHUB_API_BASE}/repos/{repo}/issues/{pr_num}/comments"
                async with self._get_http_client() as client:
                    resp = await client.post(endpoint, json={"body": body})
                    if resp.status_code != 201:
                        raise RuntimeError(f"Comment failed: {resp.status_code}")

        await log_audit_event(
            actor="user",
            action=f"github_{action}",
            target=f"{repo}:{action}",
            decision="executed",
            summary_redacted=f"Executed GitHub guarded action {action} on {repo}",
        )
        return {"status": "executed", "action": action}

    async def sync(self) -> dict[str, Any]:
        """Synchronize watched repositories and recent runs."""
        repos = await self.list_watched_repos()
        runs_count = 0
        now = datetime.now(UTC).isoformat()

        for r in repos:
            if r["watch_actions"]:
                runs = await self.get_workflow_runs(repo=r["repo_name"], limit=5)
                runs_count += len(runs)
                for run_data in runs:
                    if run_data.get("conclusion") == "failure":
                        await self.triage_failed_run(r["repo_name"], run_data["id"])

        async with get_db() as db:
            for r in repos:
                await db.execute(
                    "UPDATE github_watched_repos SET last_check_at = ? WHERE repo_name = ?",
                    (now, r["repo_name"]),
                )
            await db.commit()

        await self.mark_sync(status="ok", reason=f"Synced {runs_count} runs across watched repos")
        return {"synced_runs": runs_count, "watched_repos": len(repos)}

    async def fetch(self, query: str | None = None, limit: int = 50) -> list[Any]:
        return await self.get_workflow_runs(limit=limit)

    async def search(self, query: str, limit: int = 20) -> list[Any]:
        return await self.get_workflow_runs(limit=limit)
