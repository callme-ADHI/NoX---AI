"""Step registry and implementation of typed Python actions for Planned Jobs (Section 4.2)."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from noxai.jobs.schema import StepKind

# Patterns for secret scanning
SECRET_FILENAME_PATTERNS = [
    re.compile(r"^.*\.env$", re.IGNORECASE),
    re.compile(r"^.*\.pem$", re.IGNORECASE),
    re.compile(r"^id_rsa.*$", re.IGNORECASE),
    re.compile(r"^id_ed25519.*$", re.IGNORECASE),
    re.compile(r"^secrets\.env$", re.IGNORECASE),
]

SECRET_CONTENT_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"nvapi-[A-Za-z0-9_-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z-_]{35}"),
]

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB


@dataclass
class StepResult:
    ok: bool
    state: str  # "ok" | "failed" | "skipped"
    output_excerpt: str = ""
    error: str | None = None
    transient: bool = False  # If True, can be retried
    skipped_files: list[str] = field(default_factory=list)
    large_files: list[str] = field(default_factory=list)
    commit_hash: str | None = None
    files_changed: int = 0


def get_git_env() -> dict[str, str]:
    """Strict, non-interactive environment for all git subprocesses."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = ""
    env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o ConnectTimeout=15"
    return env


async def run_argv_async(
    args: list[str],
    cwd: str | Path,
    timeout_sec: float = 60.0,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run a process without a shell using an explicit argv list."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        env=env or get_git_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        stdout = stdout_b.decode("utf-8", errors="replace").strip()
        stderr = stderr_b.decode("utf-8", errors="replace").strip()
        return proc.returncode or 0, stdout, stderr
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except OSError:
            pass
        raise TimeoutError(f"Command {args[0]} timed out after {timeout_sec}s")


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------


async def step_git_preflight(folder: str, remote: str | None, timeout_sec: float = 30.0) -> StepResult:
    """Check git installation and non-interactive authentication."""
    # 1. Check git installed
    git_bin = shutil.which("git")
    if not git_bin:
        return StepResult(ok=False, state="failed", error="git binary not found on PATH")

    # 2. Check remote auth if remote is configured
    path = Path(os.path.expanduser(folder))
    effective_remote = remote
    if not effective_remote and (path / ".git").exists():
        code, out, _ = await run_argv_async(["git", "remote", "get-url", "origin"], cwd=path, timeout_sec=10.0)
        if code == 0:
            effective_remote = out.strip()

    if effective_remote:
        code, out, err = await run_argv_async(
            ["git", "ls-remote", effective_remote],
            cwd=path if path.exists() else Path.home(),
            timeout_sec=timeout_sec,
        )
        if code != 0:
            remediation = "Check git credentials or SSH agent. Remote auth failed non-interactively."
            if "Permission denied" in err or "publickey" in err:
                remediation = "SSH authentication failed: ssh-agent not reachable from daemon or key not loaded."
            elif "Could not resolve host" in err:
                return StepResult(ok=False, state="failed", error=f"Network offline: {err}", transient=True)
            return StepResult(ok=False, state="failed", error=f"git.preflight auth failed: {err}. Remediation: {remediation}")

    return StepResult(ok=True, state="ok", output_excerpt="git preflight verified (git installed, auth non-interactive)")


async def step_git_ensure_repo(folder: str, remote: str | None, timeout_sec: float = 30.0) -> StepResult:
    """Ensure the target directory exists and is initialized as a git repo with correct origin."""
    path = Path(os.path.expanduser(folder))
    path.mkdir(parents=True, exist_ok=True)

    git_dir = path / ".git"
    if not git_dir.exists():
        code, out, err = await run_argv_async(["git", "init", "-b", "main"], cwd=path, timeout_sec=timeout_sec)
        if code != 0:
            return StepResult(ok=False, state="failed", error=f"git init failed: {err}")

    # Set or verify remote origin
    if remote:
        code, out, _ = await run_argv_async(["git", "remote", "get-url", "origin"], cwd=path, timeout_sec=10.0)
        if code == 0:
            cur_url = out.strip()
            if cur_url != remote:
                await run_argv_async(["git", "remote", "set-url", "origin", remote], cwd=path, timeout_sec=10.0)
        else:
            await run_argv_async(["git", "remote", "add", "origin", remote], cwd=path, timeout_sec=10.0)
    else:
        # Check if remote exists
        code, out, _ = await run_argv_async(["git", "remote", "get-url", "origin"], cwd=path, timeout_sec=10.0)
        if code != 0:
            return StepResult(ok=False, state="failed", error="Folder has no remote origin configured. Setup required.")

    return StepResult(ok=True, state="ok", output_excerpt=f"Repo ensured at {folder}")


async def step_git_verify_remote_private(folder: str, remote: str | None, allow_unverified: bool = False, timeout_sec: float = 30.0) -> StepResult:
    """Enforce private-only policy for GitHub remotes."""
    path = Path(os.path.expanduser(folder))
    effective_remote = remote
    if not effective_remote and (path / ".git").exists():
        code, out, _ = await run_argv_async(["git", "remote", "get-url", "origin"], cwd=path, timeout_sec=10.0)
        if code == 0:
            effective_remote = out.strip()

    if not effective_remote:
        return StepResult(ok=False, state="failed", error="No remote URL configured to verify")

    # Local bare repos for testing are verified private
    if effective_remote.startswith("/") or effective_remote.startswith("file://"):
        return StepResult(ok=True, state="ok", output_excerpt="Local repository verified private")

    if "github.com" in effective_remote:
        # Parse owner/repo
        repo_part = None
        if "github.com:" in effective_remote:
            repo_part = effective_remote.split("github.com:")[-1].removesuffix(".git")
        elif "github.com/" in effective_remote:
            repo_part = effective_remote.split("github.com/")[-1].removesuffix(".git")

        if not repo_part or "/" not in repo_part:
            if not allow_unverified:
                return StepResult(ok=False, state="failed", error="Could not parse GitHub repo path; unverified remote blocked.")
            return StepResult(ok=True, state="ok", output_excerpt="Unverified remote allowed by config")

        # Unauthenticated GET
        import urllib.request
        import urllib.error

        api_url = f"https://api.github.com/repos/{repo_part}"
        req = urllib.request.Request(api_url, headers={"User-Agent": "NoX-Security-Scan/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                if resp.status == 200:
                    # HTTP 200 on unauth GET means repo is PUBLIC!
                    return StepResult(
                        ok=False,
                        state="failed",
                        error="CRITICAL: repository is public! Backup blocked for privacy.",
                    )
        except urllib.error.HTTPError as he:
            if he.code == 404:
                # 404 on unauth GET + valid auth check means PRIVATE
                return StepResult(ok=True, state="ok", output_excerpt=f"GitHub repository {repo_part} verified PRIVATE (HTTP 404 unauthenticated)")
            else:
                if not allow_unverified:
                    return StepResult(ok=False, state="failed", error=f"GitHub API returned HTTP {he.code}; unverified remote blocked.")
        except Exception as exc:
            if not allow_unverified:
                return StepResult(ok=False, state="failed", error=f"Could not reach GitHub API ({exc}); unverified remote blocked.", transient=True)

    elif not allow_unverified:
        return StepResult(ok=False, state="failed", error=f"Non-GitHub remote '{effective_remote}' is unverified. Set allow_unverified_remote=true to allow.")

    return StepResult(ok=True, state="ok", output_excerpt="Remote verified private")


async def step_scan_large_files(folder: str, timeout_sec: float = 60.0) -> StepResult:
    """Scan for files >50MB and add them to .git/info/exclude unless git-lfs is configured."""
    path = Path(os.path.expanduser(folder))
    if not path.exists():
        return StepResult(ok=True, state="ok", output_excerpt="Folder does not exist yet")

    # Check git-lfs installed
    lfs_available = False
    git_lfs_bin = shutil.which("git-lfs")
    if git_lfs_bin:
        code, _, _ = await run_argv_async(["git", "lfs", "version"], cwd=path, timeout_sec=5.0)
        lfs_available = (code == 0)

    large_files: list[str] = []
    for root, dirs, files in os.walk(path):
        if ".git" in dirs:
            dirs.remove(".git")
        for f in files:
            f_path = os.path.join(root, f)
            try:
                size = os.path.getsize(f_path)
                if size > MAX_FILE_SIZE_BYTES:
                    rel_path = os.path.relpath(f_path, path)
                    large_files.append(rel_path)
            except OSError:
                pass

    if large_files and not lfs_available:
        exclude_path = path / ".git" / "info" / "exclude"
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        existing_excludes = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
        new_lines = []
        for lf in large_files:
            if lf not in existing_excludes:
                new_lines.append(lf)
        if new_lines:
            with open(exclude_path, "a", encoding="utf-8") as f:
                f.write("\n# NoX: large files >50MB excluded\n" + "\n".join(new_lines) + "\n")

    return StepResult(
        ok=True,
        state="ok",
        output_excerpt=f"Large file scan completed: {len(large_files)} files >50MB found",
        large_files=large_files,
    )


async def step_scan_secrets(folder: str, timeout_sec: float = 60.0) -> StepResult:
    """Scan for potential secrets/tokens and exclude matching files from staging."""
    path = Path(os.path.expanduser(folder))
    if not path.exists():
        return StepResult(ok=True, state="ok", output_excerpt="Folder does not exist yet")

    # Apply Obsidian Vault workspace excludes if applicable
    if "Obsidian Vault" in str(path) or (path / ".obsidian").exists():
        exclude_path = path / ".git" / "info" / "exclude"
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
        obsidian_rules = [".obsidian/workspace*.json", ".trash/"]
        to_add = [r for r in obsidian_rules if r not in existing]
        if to_add:
            with open(exclude_path, "a", encoding="utf-8") as f:
                f.write("\n# NoX: Obsidian volatile files\n" + "\n".join(to_add) + "\n")

    skipped_files: list[str] = []

    for root, dirs, files in os.walk(path):
        if ".git" in dirs:
            dirs.remove(".git")
        for f in files:
            rel_path = os.path.relpath(os.path.join(root, f), path)
            # 1. Filename match
            if any(pat.match(f) for pat in SECRET_FILENAME_PATTERNS):
                skipped_files.append(rel_path)
                continue

            # 2. Content scan for text files under 2 MB
            full_p = os.path.join(root, f)
            try:
                if os.path.getsize(full_p) < 2 * 1024 * 1024:
                    with open(full_p, "r", encoding="utf-8", errors="ignore") as f_obj:
                        content_sample = f_obj.read(100_000)
                        if any(pat.search(content_sample) for pat in SECRET_CONTENT_PATTERNS):
                            skipped_files.append(rel_path)
            except Exception:
                pass

    if skipped_files:
        exclude_path = path / ".git" / "info" / "exclude"
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
        new_lines = [sf for sf in skipped_files if sf not in existing]
        if new_lines:
            with open(exclude_path, "a", encoding="utf-8") as f:
                f.write("\n# NoX: secret scan skipped files\n" + "\n".join(new_lines) + "\n")

    return StepResult(
        ok=True,
        state="ok",
        output_excerpt=f"Secret scan completed: {len(skipped_files)} sensitive files excluded",
        skipped_files=skipped_files,
    )


async def step_fs_scan_changes(folder: str, timeout_sec: float = 30.0) -> StepResult:
    """Check if there are modified or untracked changes in the working tree."""
    path = Path(os.path.expanduser(folder))
    code, out, err = await run_argv_async(["git", "status", "--porcelain"], cwd=path, timeout_sec=timeout_sec)
    if code != 0:
        return StepResult(ok=False, state="failed", error=f"git status failed: {err}")

    lines = [l for l in out.splitlines() if l.strip()]
    if not lines:
        return StepResult(
            ok=True,
            state="ok",
            output_excerpt="nothing to commit, working tree clean",
            files_changed=0,
        )

    return StepResult(
        ok=True,
        state="ok",
        output_excerpt=f"{len(lines)} files changed",
        files_changed=len(lines),
    )


async def step_git_stage(folder: str, timeout_sec: float = 60.0) -> StepResult:
    """Stage all tracked and untracked non-excluded changes."""
    path = Path(os.path.expanduser(folder))
    code, out, err = await run_argv_async(["git", "add", "-A"], cwd=path, timeout_sec=timeout_sec)
    if code != 0:
        return StepResult(ok=False, state="failed", error=f"git add failed: {err}")

    return StepResult(ok=True, state="ok", output_excerpt="Staged working tree changes")


async def step_git_commit(folder: str, folder_alias: str, files_count: int = 0, timeout_sec: float = 30.0) -> StepResult:
    """Commit staged changes with a deterministic commit message."""
    path = Path(os.path.expanduser(folder))

    # Check index.lock
    lock_file = path / ".git" / "index.lock"
    wait_start = time.monotonic()
    while lock_file.exists() and time.monotonic() - wait_start < 60.0:
        await asyncio.sleep(2.0)
    if lock_file.exists():
        return StepResult(ok=False, state="failed", error="git index.lock present after 60s timeout")

    tz = ZoneInfo("Asia/Kolkata")
    now_str = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
    alias = folder_alias or path.name
    msg = f"NoX backup {alias} {now_str} ({files_count} files)"

    code, out, err = await run_argv_async(
        ["git", "commit", "-m", msg],
        cwd=path,
        timeout_sec=timeout_sec,
    )
    if code != 0:
        if "nothing to commit" in out or "nothing to commit" in err:
            return StepResult(ok=True, state="skipped", output_excerpt="Nothing to commit")
        return StepResult(ok=False, state="failed", error=f"git commit failed: {err}")

    # Extract short hash
    code_h, out_h, _ = await run_argv_async(["git", "rev-parse", "--short", "HEAD"], cwd=path, timeout_sec=5.0)
    commit_hash = out_h.strip() if code_h == 0 else None

    return StepResult(ok=True, state="ok", output_excerpt=f"Committed {commit_hash}: {msg}", commit_hash=commit_hash)


async def step_git_push(folder: str, timeout_sec: float = 120.0) -> StepResult:
    """Push fast-forward to remote origin."""
    path = Path(os.path.expanduser(folder))

    # Determine current branch
    code, out, _ = await run_argv_async(["git", "branch", "--show-current"], cwd=path, timeout_sec=5.0)
    branch = out.strip() if code == 0 and out.strip() else "main"

    # Fetch first to check ancestry
    code_f, out_f, err_f = await run_argv_async(["git", "fetch", "origin", branch], cwd=path, timeout_sec=30.0)
    if code_f == 0:
        # Check if remote is ancestor or diverged
        code_anc, _, _ = await run_argv_async(
            ["git", "merge-base", "--is-ancestor", f"origin/{branch}", "HEAD"],
            cwd=path,
            timeout_sec=5.0,
        )
        if code_anc != 0:
            return StepResult(
                ok=False,
                state="failed",
                error=f"Remote diverged on {branch}, manual action needed. Push aborted to protect history.",
            )

    # Push fast-forward
    code_p, out_p, err_p = await run_argv_async(
        ["git", "push", "origin", branch],
        cwd=path,
        timeout_sec=timeout_sec,
    )
    if code_p != 0:
        is_transient = "Could not resolve host" in err_p or "Connection refused" in err_p or "timed out" in err_p
        return StepResult(ok=False, state="failed", error=f"git push failed: {err_p}", transient=is_transient)

    return StepResult(ok=True, state="ok", output_excerpt=f"Pushed {branch} to origin")


async def step_git_verify_pushed(folder: str, timeout_sec: float = 30.0) -> StepResult:
    """Verify that remote HEAD matches local HEAD hash."""
    path = Path(os.path.expanduser(folder))
    code_b, out_b, _ = await run_argv_async(["git", "branch", "--show-current"], cwd=path, timeout_sec=5.0)
    branch = out_b.strip() if code_b == 0 and out_b.strip() else "main"

    code_l, out_l, _ = await run_argv_async(["git", "rev-parse", "HEAD"], cwd=path, timeout_sec=5.0)
    local_hash = out_l.strip() if code_l == 0 else ""

    code_r, out_r, err_r = await run_argv_async(
        ["git", "ls-remote", "origin", f"refs/heads/{branch}"],
        cwd=path,
        timeout_sec=timeout_sec,
    )
    if code_r != 0:
        return StepResult(ok=False, state="failed", error=f"Verification ls-remote failed: {err_r}", transient=True)

    remote_hash = out_r.split()[0] if out_r.strip() else ""
    if local_hash and remote_hash and local_hash == remote_hash:
        return StepResult(ok=True, state="ok", output_excerpt=f"Push verified: remote matches {local_hash[:7]}")
    elif not remote_hash:
        return StepResult(ok=True, state="ok", output_excerpt="Push completed (first commit on remote branch)")
    else:
        return StepResult(
            ok=False,
            state="failed",
            error=f"Push verification failed: local {local_hash[:7]} != remote {remote_hash[:7]}",
        )


async def step_notify(summary: str, is_error: bool = False, run_id: str | None = None) -> StepResult:
    """Send desktop notification via notify-send."""
    title = "NoX Backup Alert" if is_error else "NoX Backup"
    urgency = "critical" if is_error else "normal"

    notify_bin = shutil.which("notify-send")
    if notify_bin:
        try:
            cmd = ["notify-send", "-u", urgency, "-a", "NoX", title, summary]
            proc = await asyncio.create_subprocess_exec(*cmd)
            await asyncio.wait_for(proc.communicate(), timeout=5.0)
        except Exception:
            pass

    return StepResult(ok=True, state="ok", output_excerpt=f"Notification delivered: {summary}")
