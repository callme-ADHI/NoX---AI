"""Unit tests for Planned Jobs Step Execution Actions (Section 4).

Tests git actions against local bare repos, secret scanning, large file detection,
and notification generation.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import aiosqlite
import pytest

from noxai.jobs.schema import JobStep, StepKind
from noxai.jobs.steps import (
    step_git_preflight,
    step_git_ensure_repo,
    step_scan_secrets,
    step_scan_large_files,
    step_fs_scan_changes,
    step_git_stage,
    step_git_commit,
    step_git_push,
    step_git_verify_pushed,
    step_notify,
)


@pytest.fixture
def test_folders(tmp_path: Path):
    """Create a local bare remote repo and a working directory folder."""
    remote_dir = tmp_path / "remote.git"
    remote_dir.mkdir()
    subprocess.run(["git", "init", "--bare", str(remote_dir)], check=True, capture_output=True)

    work_dir = tmp_path / "work_folder"
    work_dir.mkdir()

    # Init working git repo
    subprocess.run(["git", "init", "-b", "main", str(work_dir)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=str(work_dir), check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=str(work_dir), check=True
    )
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote_dir)], cwd=str(work_dir), check=True
    )

    return {"remote": str(remote_dir), "work": str(work_dir)}


@pytest.mark.asyncio
async def test_step_git_preflight(test_folders):
    res = await step_git_preflight(folder=test_folders["work"], remote=test_folders["remote"])
    assert res.ok
    assert res.state == "ok"


@pytest.mark.asyncio
async def test_step_scan_secrets_clean_and_detection(test_folders):
    work = Path(test_folders["work"])
    clean_file = work / "notes.txt"
    clean_file.write_text("Hello, this is regular text with no API keys.")

    res = await step_scan_secrets(folder=str(work))
    assert res.ok
    assert len(res.skipped_files) == 0

    # Plant a fake secret
    bad_file = work / "key.txt"
    bad_file.write_text("fake_key = 'nvapi-1234567890abcdef1234567890abcdef'")

    res_bad = await step_scan_secrets(folder=str(work))
    assert res_bad.ok
    assert len(res_bad.skipped_files) > 0


@pytest.mark.asyncio
async def test_step_scan_large_files(test_folders):
    work = Path(test_folders["work"])
    res = await step_scan_large_files(folder=str(work))
    assert res.ok
    assert len(res.large_files) == 0


@pytest.mark.asyncio
async def test_step_git_workflow_lifecycle(test_folders):
    work = Path(test_folders["work"])
    file1 = work / "doc.md"
    file1.write_text("# Documentation")

    # 1. fs.scan_changes
    r_scan = await step_fs_scan_changes(folder=str(work))
    assert r_scan.ok
    assert r_scan.files_changed >= 1

    # 2. git.stage
    r_stage = await step_git_stage(folder=str(work))
    assert r_stage.ok

    # 3. git.commit
    r_commit = await step_git_commit(folder=str(work), folder_alias="work", files_count=1)
    assert r_commit.ok
    assert r_commit.commit_hash is not None

    # 4. git.push
    r_push = await step_git_push(folder=str(work))
    assert r_push.ok

    # 5. git.verify_pushed
    r_verify = await step_git_verify_pushed(folder=str(work))
    assert r_verify.ok

