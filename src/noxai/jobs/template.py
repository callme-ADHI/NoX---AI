"""Deterministic Template Planner for NoX Backup Jobs (Phase 2)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from noxai.jobs.schema import JobPlan, JobStep, StepKind, StepRetryPolicy


def get_default_backup_folders() -> list[str]:
    return [
        "~/Documents/Certificates",
        "~/Documents/Study",
        "~/Documents/Obsidian Vault",
    ]


def calculate_folder_summary(folder_path: str) -> dict[str, Any]:
    """Calculate upload preview summary: files count, total size, remote, private repo."""
    path = Path(os.path.expanduser(folder_path))
    exists = path.exists() and path.is_dir()

    file_count = 0
    total_size_bytes = 0
    remote_url: str | None = None
    is_git_repo = (path / ".git").exists()

    if exists:
        try:
            for root, dirs, files in os.walk(path):
                # Skip .git directory in file count
                if ".git" in dirs:
                    dirs.remove(".git")
                for f in files:
                    file_count += 1
                    try:
                        total_size_bytes += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
        except Exception:
            pass

    if is_git_repo:
        try:
            p = subprocess.run(
                ["git", "-C", str(path), "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if p.returncode == 0:
                remote_url = p.stdout.strip()
        except Exception:
            pass

    is_private = None
    if remote_url and "github.com" in remote_url:
        # Check via unauth GET
        try:
            import httpx

            # e.g. git@github.com:owner/repo.git or https://github.com/owner/repo
            match = None
            if "github.com:" in remote_url:
                repo_part = remote_url.split("github.com:")[-1].removesuffix(".git")
            elif "github.com/" in remote_url:
                repo_part = remote_url.split("github.com/")[-1].removesuffix(".git")
            else:
                repo_part = None

            if repo_part:
                api_url = f"https://api.github.com/repos/{repo_part}"
                r = httpx.get(api_url, timeout=5.0)
                if r.status_code == 200:
                    is_private = False  # Public repo!
                elif r.status_code == 404:
                    is_private = True  # Private or non-existent
        except Exception:
            pass

    return {
        "folder": folder_path,
        "exists": exists,
        "is_git_repo": is_git_repo,
        "file_count": file_count,
        "total_size_bytes": total_size_bytes,
        "total_size_mb": round(total_size_bytes / (1024 * 1024), 2),
        "remote": remote_url,
        "is_private": is_private,
    }


def create_backup_template_plan(
    job_id: str,
    folders: list[str] | None = None,
    remotes: dict[str, str] | None = None,
    version: int = 1,
) -> JobPlan:
    """Construct the canonical deterministic backup plan (Phase 2 & ADR-L1)."""
    target_folders = folders or get_default_backup_folders()
    remotes_map = remotes or {}

    steps: list[JobStep] = []
    idx = 1

    network_retry = StepRetryPolicy(
        max_attempts=5,
        backoff_base_seconds=5.0,
        max_backoff_seconds=300.0,
    )
    strict_retry = StepRetryPolicy(max_attempts=1)

    for fpath in target_folders:
        clean_path = fpath.strip("\"'")
        alias = Path(clean_path).name.strip("\"'")
        folder_remote = remotes_map.get(clean_path) or remotes_map.get(alias)

        folder_steps = [
            # 1. git.preflight
            JobStep(
                step_index=idx,
                kind=StepKind.GIT_PREFLIGHT.value,
                folder_alias=alias,
                folder_path=clean_path,
                remote=folder_remote,
                timeout_seconds=30,
                retry=network_retry,
            ),
            # 2. git.ensure_repo
            JobStep(
                step_index=idx + 1,
                kind=StepKind.GIT_ENSURE_REPO.value,
                folder_alias=alias,
                folder_path=clean_path,
                remote=folder_remote,
                timeout_seconds=30,
                retry=strict_retry,
            ),
            # 3. git.verify_remote_private
            JobStep(
                step_index=idx + 2,
                kind=StepKind.GIT_VERIFY_REMOTE_PRIVATE.value,
                folder_alias=alias,
                folder_path=clean_path,
                remote=folder_remote,
                timeout_seconds=30,
                retry=network_retry,
            ),
            # 4. scan.large_files
            JobStep(
                step_index=idx + 3,
                kind=StepKind.SCAN_LARGE_FILES.value,
                folder_alias=alias,
                folder_path=clean_path,
                remote=folder_remote,
                timeout_seconds=60,
                retry=strict_retry,
            ),
            # 5. scan.secrets
            JobStep(
                step_index=idx + 4,
                kind=StepKind.SCAN_SECRETS.value,
                folder_alias=alias,
                folder_path=clean_path,
                remote=folder_remote,
                timeout_seconds=60,
                retry=strict_retry,
            ),
            # 6. fs.scan_changes
            JobStep(
                step_index=idx + 5,
                kind=StepKind.FS_SCAN_CHANGES.value,
                folder_alias=alias,
                folder_path=clean_path,
                remote=folder_remote,
                timeout_seconds=30,
                retry=strict_retry,
            ),
            # 7. git.stage
            JobStep(
                step_index=idx + 6,
                kind=StepKind.GIT_STAGE.value,
                folder_alias=alias,
                folder_path=clean_path,
                remote=folder_remote,
                timeout_seconds=60,
                retry=strict_retry,
            ),
            # 8. git.commit
            JobStep(
                step_index=idx + 7,
                kind=StepKind.GIT_COMMIT.value,
                folder_alias=alias,
                folder_path=clean_path,
                remote=folder_remote,
                timeout_seconds=30,
                retry=strict_retry,
            ),
            # 9. git.push
            JobStep(
                step_index=idx + 8,
                kind=StepKind.GIT_PUSH.value,
                folder_alias=alias,
                folder_path=clean_path,
                remote=folder_remote,
                timeout_seconds=120,
                retry=network_retry,
            ),
            # 10. git.verify_pushed
            JobStep(
                step_index=idx + 9,
                kind=StepKind.GIT_VERIFY_PUSHED.value,
                folder_alias=alias,
                folder_path=clean_path,
                remote=folder_remote,
                timeout_seconds=30,
                retry=network_retry,
            ),
        ]
        steps.extend(folder_steps)
        idx += len(folder_steps)

    # Final summary notification step
    steps.append(
        JobStep(
            step_index=idx,
            kind=StepKind.NOTIFY.value,
            folder_alias="all",
            folder_path="~/Documents",
            timeout_seconds=15,
            retry=strict_retry,
        )
    )

    return JobPlan(
        job_id=job_id,
        version=version,
        source="template",
        status="draft",
        description="Daily GitHub backup of personal documents",
        steps=steps,
        metadata={
            "folders": target_folders,
            "remotes": remotes_map,
        },
    )
