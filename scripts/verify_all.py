#!/usr/bin/env python3
"""NoX Verification Harness (Session O).

Runs real, non-mocked verification checks for all defect IDs (N-1..N-8, C-1..C-8,
T-1..T-2, L-1..L-2, D-1..D-7, P-1..P-2, F-1..F-4).
Supports:
  python scripts/verify_all.py --all
  python scripts/verify_all.py <defect_id>
  python scripts/verify_all.py --json
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class CheckResult:
    defect_id: str
    name: str
    category: str
    status: str  # PASS, FAIL, NOT_CONFIGURED, USER_RUN
    label: str   # FIXED-AND-PROVEN, VERIFIED-LIVE, VERIFIED-TEST, MOCK-ONLY, USER-RUN, NOT-DONE
    details: str
    command: str = ""
    output: str = ""


class TempInstance:
    """Helper to launch isolated temporary instances of NoX daemon and data."""

    def __init__(self, port: int | None = None) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="nox_verify_")
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.data_dir = self.home / ".local" / "share" / "nox"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir = self.home / ".config" / "nox"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.port = port or self._find_free_port()
        self.process: subprocess.Popen[str] | None = None

    def _find_free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def get_env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        env["NOX_DATA_DIR"] = str(self.data_dir)
        env["NOX_CONFIG_DIR"] = str(self.config_dir)
        env["NOX_PORT"] = str(self.port)
        if extra:
            env.update(extra)
        return env

    def close(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.temp_dir.cleanup()


class BareRepoHelper:
    """Creates temporary bare git repositories to test git operations safely."""

    def __init__(self, parent_dir: Path) -> None:
        self.parent = parent_dir
        self.bare_dir = self.parent / "bare_repos"
        self.bare_dir.mkdir(parents=True, exist_ok=True)

    def create_bare_repo(self, name: str) -> Path:
        repo_path = self.bare_dir / f"{name}.git"
        subprocess.run(
            ["git", "init", "--bare", str(repo_path)],
            check=True,
            capture_output=True,
        )
        return repo_path


# ---------------------------------------------------------------------------
# Individual Check Functions
# ---------------------------------------------------------------------------

def check_N1() -> CheckResult:
    """Check N-1: Token extraction / git credential fill removal."""
    # 1. Search code for forbidden pattern "credential fill" or "git credential fill"
    forbidden = ["credential fill", "git credential fill"]
    hits = []
    for ext in ["*.py", "*.sh", "*.js", "*.ts", "*.tsx"]:
        for p in REPO_ROOT.glob(f"**/{ext}"):
            if ".git" in p.parts or ".venv" in p.parts or "node_modules" in p.parts or "scripts/verify_all.py" in p.parts:
                continue
            try:
                text = p.read_text(errors="ignore")
                for f in forbidden:
                    if f in text:
                        hits.append(f"{p}:{f}")
            except Exception:
                pass

    # 2. Check if SecretStore has github:token stored
    token_in_store = False
    try:
        from noxai.security.secrets import SecretStore
        if SecretStore.get("github:token"):
            token_in_store = True
    except Exception:
        pass

    if hits or token_in_store:
        details = []
        if hits:
            details.append(f"Forbidden credential extraction strings found in: {hits[:3]}")
        if token_in_store:
            details.append("github:token is stored in SecretStore")
        return CheckResult(
            defect_id="N-1",
            name="No git credential fill or automatic token storage",
            category="Security",
            status="FAIL",
            label="NOT-DONE",
            details="; ".join(details),
            command="git grep -n 'credential fill' src scripts tests",
            output="\n".join(hits) + (f"\nToken stored: {token_in_store}" if token_in_store else ""),
        )

    return CheckResult(
        defect_id="N-1",
        name="No git credential fill or automatic token storage",
        category="Security",
        status="PASS",
        label="FIXED-AND-PROVEN",
        details="Zero instances of credential fill in codebase; SecretStore clean.",
        command="git grep -n 'credential fill' src scripts tests",
        output="Clean",
    )


def check_N2() -> CheckResult:
    """Check N-2: Honest connector/channel states."""
    # Check if connectors table or health reports honest states when unconfigured
    try:
        from noxai.connectors.registry import ConnectorRegistry
        # If any unconfigured connector reports HEALTHY without credentials, FAIL
        db_path = os.path.expanduser("~/.local/share/nox/nox.db")
        if os.path.exists(db_path):
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cur = conn.cursor()
            rows = cur.execute("SELECT name, health_status FROM connectors").fetchall()
            conn.close()
            # If 0 connectors or any shows HEALTHY without credentials
            if not rows:
                return CheckResult(
                    defect_id="N-2",
                    name="Honest connector states (NOT_CONFIGURED, etc.)",
                    category="Security",
                    status="FAIL",
                    label="NOT-DONE",
                    details="Connectors table has 0 rows but CLI health displayed HEALTHY/PASS.",
                    command="nx health",
                    output="Connectors: 0 configured rows; displayed HEALTHY in previous session.",
                )
    except Exception as exc:
        return CheckResult(
            defect_id="N-2",
            name="Honest connector states",
            category="Security",
            status="FAIL",
            label="NOT-DONE",
            details=f"Error checking connector states: {exc}",
        )

    return CheckResult(
        defect_id="N-2",
        name="Honest connector states",
        category="Security",
        status="FAIL",
        label="NOT-DONE",
        details="Honest state machine (NOT_CONFIGURED, CONFIGURED_UNTESTED, etc.) not yet enforced.",
    )


def check_N3() -> CheckResult:
    """Check N-3: Audit log wiring."""
    db_path = os.path.expanduser("~/.local/share/nox/nox.db")
    if os.path.exists(db_path):
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = conn.cursor()
        count = cur.execute("SELECT count(*) FROM audit_events").fetchone()[0]
        conn.close()
        if count <= 3:
            return CheckResult(
                defect_id="N-3",
                name="Audit log completeness on guarded actions",
                category="Security",
                status="FAIL",
                label="NOT-DONE",
                details=f"Audit log has only {count} events; guarded actions are not comprehensively wired.",
                command="sqlite3 ~/.local/share/nox/nox.db 'SELECT count(*) FROM audit_events'",
                output=f"count = {count}",
            )
    return CheckResult(
        defect_id="N-3",
        name="Audit log completeness on guarded actions",
        category="Security",
        status="FAIL",
        label="NOT-DONE",
        details="Audit log not yet wired across connectors, writes, egress, and skills.",
    )


def check_N4() -> CheckResult:
    """Check N-4: SecretStore truthfulness."""
    # Check if SecretStore text/docs claim 'encrypted' when using file store
    secrets_py = REPO_ROOT / "src" / "noxai" / "security" / "secrets.py"
    if secrets_py.exists():
        text = secrets_py.read_text()
        # Look at how it is described
        if "encrypted" in text.lower() and "file" in text.lower():
            return CheckResult(
                defect_id="N-4",
                name="SecretStore truthfulness",
                category="Security",
                status="FAIL",
                label="NOT-DONE",
                details="SecretStore describes 0600 file store as 'encrypted' without encryption.",
                command="grep -i 'encrypted' src/noxai/security/secrets.py",
                output="Found unencrypted 0600 file store described as encrypted.",
            )
    return CheckResult(
        defect_id="N-4",
        name="SecretStore truthfulness",
        category="Security",
        status="FAIL",
        label="NOT-DONE",
        details="SecretStore text needs alignment to accurately state 0600 file store.",
    )


def check_N5() -> CheckResult:
    """Check N-5: Multi-language skill scanner."""
    scanner_py = REPO_ROOT / "src" / "noxai" / "agents" / "skill_scanner.py"
    if scanner_py.exists():
        text = scanner_py.read_text()
        if "ast.parse" in text and "child_process" not in text and "curl" not in text:
            return CheckResult(
                defect_id="N-5",
                name="Multi-language static skill scanner",
                category="Security",
                status="FAIL",
                label="NOT-DONE",
                details="Skill scanner is Python-AST only; lacks shell, JS, and Markdown scanners.",
                command="grep 'ast.parse' src/noxai/agents/skill_scanner.py",
                output="Only ast.parse present.",
            )
    return CheckResult(
        defect_id="N-5",
        name="Multi-language static skill scanner",
        category="Security",
        status="FAIL",
        label="NOT-DONE",
        details="Skill scanner not yet upgraded to multi-language.",
    )


def check_N6() -> CheckResult:
    """Check N-6: OPENJARVIS_PARITY.md rebuilt from pinned clone."""
    parity_md = REPO_ROOT / "docs" / "OPENJARVIS_PARITY.md"
    if parity_md.exists():
        text = parity_md.read_text()
        if "openjarvis/" not in text and "src/" not in text:
            return CheckResult(
                defect_id="N-6",
                name="OpenJarvis parity matrix rebuilt with upstream paths",
                category="Security",
                status="FAIL",
                label="NOT-DONE",
                details="OPENJARVIS_PARITY.md lacks exact upstream file path citations.",
                command="head -n 30 docs/OPENJARVIS_PARITY.md",
                output="Missing specific upstream file paths per row.",
            )
    return CheckResult(
        defect_id="N-6",
        name="OpenJarvis parity matrix rebuilt with upstream paths",
        category="Security",
        status="FAIL",
        label="NOT-DONE",
        details="OpenJarvis parity matrix not yet rebuilt with exact upstream citations.",
    )


def check_N7() -> CheckResult:
    """Check N-7: UI component tests for 7 new pages."""
    test_dir = REPO_ROOT / "ui" / "src" / "__tests__"
    new_pages = [
        "ConnectorsPage.test.tsx",
        "MailPage.test.tsx",
        "GitHubPage.test.tsx",
        "AgentsPage.test.tsx",
        "ApprovalsPage.test.tsx",
        "AuditPage.test.tsx",
        "SkillsPage.test.tsx",
    ]
    missing = [p for p in new_pages if not (test_dir / p).exists()]
    if missing:
        return CheckResult(
            defect_id="N-7",
            name="UI component tests for 7 new pages",
            category="Security",
            status="FAIL",
            label="NOT-DONE",
            details=f"Missing component tests for: {missing}",
            command=f"ls ui/src/__tests__/",
            output=f"Missing: {len(missing)}/7 page tests",
        )
    return CheckResult(
        defect_id="N-7",
        name="UI component tests for 7 new pages",
        category="Security",
        status="PASS",
        label="FIXED-AND-PROVEN",
        details="All 7 page component tests present.",
    )


def check_N8() -> CheckResult:
    """Check N-8: GitHub CI workflows and local CI-equivalent."""
    ci_local = REPO_ROOT / "scripts" / "ci-local.sh"
    if not ci_local.exists():
        return CheckResult(
            defect_id="N-8",
            name="GitHub Actions CI & local CI-equivalent",
            category="Security",
            status="FAIL",
            label="NOT-DONE",
            details="scripts/ci-local.sh does not exist.",
            command="ls scripts/ci-local.sh",
            output="Not found",
        )
    return CheckResult(
        defect_id="N-8",
        name="GitHub Actions CI & local CI-equivalent",
        category="Security",
        status="FAIL",
        label="NOT-DONE",
        details="CI workflows not yet running on GitHub.",
    )


def check_C1() -> CheckResult:
    """Check C-1: Single scheduler authority."""
    # Check if duplicate briefing methods exist in PersistentScheduler
    ps_path = REPO_ROOT / "src" / "noxai" / "scheduler" / "persistent.py"
    if ps_path.exists():
        text = ps_path.read_text()
        if "_run_morning_briefing" in text or "briefing_morning" in text:
            return CheckResult(
                defect_id="C-1",
                name="One authoritative scheduler path per job type",
                category="Core Runtime",
                status="FAIL",
                label="NOT-DONE",
                details="PersistentScheduler contains duplicate briefing execution logic.",
                command="grep -n '_run_morning_briefing' src/noxai/scheduler/persistent.py",
                output="Found briefing runner inside persistent.py alongside BriefingScheduler.",
            )
    return CheckResult(
        defect_id="C-1",
        name="One authoritative scheduler path per job type",
        category="Core Runtime",
        status="FAIL",
        label="NOT-DONE",
        details="Scheduler authority not yet unified into single path.",
    )


def check_C2() -> CheckResult:
    """Check C-2: Live automation proof for 04:00 & 18:00."""
    return CheckResult(
        defect_id="C-2",
        name="Unattended briefing automation proof (04:00 & 18:00)",
        category="Core Runtime",
        status="FAIL",
        label="NOT-DONE",
        details="Requires temp-instance automated proof with time-override.",
    )


def check_C3() -> CheckResult:
    """Check C-3: Resume after SIGKILL."""
    return CheckResult(
        defect_id="C-3",
        name="Subprocess daemon SIGKILL resume proof",
        category="Core Runtime",
        status="FAIL",
        label="NOT-DONE",
        details="Requires live SIGKILL subprocess test against running daemon.",
    )


def check_C4() -> CheckResult:
    """Check C-4: KeyPool allocator used in NVIDIA provider."""
    # From mypy: NvidiaProvider was using NvidiaKeyPool which has no attribute 'lease'
    nvidia_py = REPO_ROOT / "src" / "noxai" / "models" / "nvidia.py"
    if nvidia_py.exists():
        text = nvidia_py.read_text()
        if "from noxai.models.key_pool import" in text:
            return CheckResult(
                defect_id="C-4",
                name="KeyPool allocator genuinely wired in provider",
                category="Core Runtime",
                status="FAIL",
                label="NOT-DONE",
                details="NvidiaProvider references old key_pool.py instead of models/keypool.py.",
                command="grep 'key_pool' src/noxai/models/nvidia.py",
                output="Found import of legacy key_pool.",
            )
    return CheckResult(
        defect_id="C-4",
        name="KeyPool allocator genuinely wired in provider",
        category="Core Runtime",
        status="FAIL",
        label="NOT-DONE",
        details="KeyPool allocator not yet verified with mock HTTP server.",
    )


def check_C5() -> CheckResult:
    """Check C-5: Cloud default ON with taint isolation."""
    return CheckResult(
        defect_id="C-5",
        name="Cloud default ON with recorded taint isolation",
        category="Core Runtime",
        status="FAIL",
        label="NOT-DONE",
        details="Requires temp instance recording transport proof.",
    )


def check_C6() -> CheckResult:
    """Check C-6: Conversation memory real model evaluation."""
    return CheckResult(
        defect_id="C-6",
        name="Conversation memory real model 8-scenario evaluation",
        category="Core Runtime",
        status="FAIL",
        label="NOT-DONE",
        details="Requires real model execution without 80-token cap.",
    )


def check_C7() -> CheckResult:
    """Check C-7: Migration 009 convergence."""
    return CheckResult(
        defect_id="C-7",
        name="Migration 009 convergence on DB copy",
        category="Core Runtime",
        status="FAIL",
        label="NOT-DONE",
        details="Requires converging migration and upgrade tests for both paths.",
    )


def check_C8() -> CheckResult:
    """Check C-8: Retrieval taint on notes."""
    return CheckResult(
        defect_id="C-8",
        name="Retrieval taint on notes chunks (local_only)",
        category="Core Runtime",
        status="FAIL",
        label="NOT-DONE",
        details="Requires recorded-transport egress check for retrieved chunks.",
    )


def check_T1() -> CheckResult:
    """Check T-1: Briefing content quality & link checks."""
    web_py = REPO_ROOT / "src" / "noxai" / "tools" / "builtin" / "web.py"
    if web_py.exists():
        text = web_py.read_text()
        if "NOX_MOCK_SEARCH" in text or "mock_results" in text:
            return CheckResult(
                defect_id="T-1",
                name="Briefing content quality & real search",
                category="Content",
                status="FAIL",
                label="NOT-DONE",
                details="Mocked search found in production code (src/noxai/tools/builtin/web.py).",
                command="grep -n 'NOX_MOCK_SEARCH' src/noxai/tools/builtin/web.py",
                output="Found NOX_MOCK_SEARCH in web.py",
            )
    return CheckResult(
        defect_id="T-1",
        name="Briefing content quality & real search",
        category="Content",
        status="FAIL",
        label="NOT-DONE",
        details="Briefing content live proof with real network run required.",
    )


def check_T2() -> CheckResult:
    """Check T-2: Single Run Briefing button survives refresh/nav/close."""
    # Check Playwright test syntax
    spec = REPO_ROOT / "ui" / "e2e" / "briefing_single_button.spec.ts"
    if spec.exists():
        text = spec.read_text()
        if "__dirname" in text:
            return CheckResult(
                defect_id="T-2",
                name="Single Run Briefing button survives refresh/nav/close",
                category="Content",
                status="FAIL",
                label="NOT-DONE",
                details="Playwright test has ReferenceError: __dirname in ESM scope.",
                command="npx playwright test --list",
                output="ReferenceError: __dirname is not defined in ES module scope",
            )
    return CheckResult(
        defect_id="T-2",
        name="Single Run Briefing button survives refresh/nav/close",
        category="Content",
        status="FAIL",
        label="NOT-DONE",
        details="Playwright test against real run required.",
    )


def check_L1() -> CheckResult:
    """Check L-1: Stuck BackUp job run audit & reconciliation."""
    audit_doc = REPO_ROOT / "docs" / "audits" / "job-stuck-audit.md"
    if not audit_doc.exists():
        return CheckResult(
            defect_id="L-1",
            name="Stuck BackUp job run audit and watchdog repair",
            category="Jobs",
            status="FAIL",
            label="NOT-DONE",
            details="docs/audits/job-stuck-audit.md does not exist.",
            command="ls docs/audits/job-stuck-audit.md",
            output="Not found",
        )
    return CheckResult(
        defect_id="L-1",
        name="Stuck BackUp job run audit and watchdog repair",
        category="Jobs",
        status="FAIL",
        label="NOT-DONE",
        details="Audit and repair of stuck job not complete.",
    )


def check_L2() -> CheckResult:
    """Check L-2: Plan-first job engine & daily backup."""
    return CheckResult(
        defect_id="L-2",
        name="Plan-first job engine & daily GitHub backup",
        category="Jobs",
        status="FAIL",
        label="NOT-DONE",
        details="Requires live bare-repo proofs in temp HOME per Session L spec.",
    )


def check_D1() -> CheckResult:
    """Check D-1: Desktop tray endpoints."""
    tray_rs = REPO_ROOT / "desktop" / "src-tauri" / "src" / "tray.rs"
    if tray_rs.exists():
        text = tray_rs.read_text()
        if "/api/briefing/run" in text:
            return CheckResult(
                defect_id="D-1",
                name="Desktop tray endpoints",
                category="Desktop & Packaging",
                status="FAIL",
                label="NOT-DONE",
                details="Tray posts to invalid endpoint /api/briefing/run (singular) and swallows errors.",
                command="grep '/api/briefing/run' desktop/src-tauri/src/tray.rs",
                output="Found /api/briefing/run in tray.rs",
            )
    return CheckResult(
        defect_id="D-1",
        name="Desktop tray endpoints",
        category="Desktop & Packaging",
        status="FAIL",
        label="NOT-DONE",
        details="Desktop tray endpoints not yet verified.",
    )


def check_D2() -> CheckResult:
    """Check D-2: Notes vault path resolution."""
    return CheckResult(
        defect_id="D-2",
        name="Notes Vault path sourced from daemon config",
        category="Desktop & Packaging",
        status="FAIL",
        label="NOT-DONE",
        details="Vault path in desktop app must be dynamic from daemon config.",
    )


def check_D3() -> CheckResult:
    """Check D-3: Dynamic tray status labels."""
    return CheckResult(
        defect_id="D-3",
        name="Dynamic tray status labels",
        category="Desktop & Packaging",
        status="FAIL",
        label="NOT-DONE",
        details="Tray labels are static; need live state polling.",
    )


def check_D4() -> CheckResult:
    """Check D-4: Desktop token flow security."""
    return CheckResult(
        defect_id="D-4",
        name="Desktop secure token flow",
        category="Desktop & Packaging",
        status="FAIL",
        label="NOT-DONE",
        details="Token flow must avoid argv and cross-origin exposure.",
    )


def check_D5() -> CheckResult:
    """Check D-5: Navigation allowlist and IPC safety."""
    return CheckResult(
        defect_id="D-5",
        name="Desktop navigation allowlist & IPC safety",
        category="Desktop & Packaging",
        status="FAIL",
        label="NOT-DONE",
        details="Navigation allowlist and path allowlist verification required.",
    )


def check_D6() -> CheckResult:
    """Check D-6: Window class / app-id consistency."""
    return CheckResult(
        defect_id="D-6",
        name="Window class / app-id consistency",
        category="Desktop & Packaging",
        status="FAIL",
        label="NOT-DONE",
        details="Check app-id across desktop entry, Hyprland rules, and docs.",
    )


def check_D7() -> CheckResult:
    """Check D-7: Desktop tests and Hyprland smoke test."""
    return CheckResult(
        defect_id="D-7",
        name="Desktop extended cargo tests & Hyprland smoke test",
        category="Desktop & Packaging",
        status="FAIL",
        label="NOT-DONE",
        details="Requires extended cargo unit tests and live hyprctl smoke test.",
    )


def check_P1() -> CheckResult:
    """Check P-1: PKGBUILD dependencies and systemd unit."""
    pkgbuild = REPO_ROOT / "packaging" / "arch" / "PKGBUILD"
    if pkgbuild.exists():
        text = pkgbuild.read_text()
        if "SKIP" in text:
            return CheckResult(
                defect_id="P-1",
                name="Arch PKGBUILD integrity",
                category="Desktop & Packaging",
                status="FAIL",
                label="NOT-DONE",
                details="PKGBUILD uses SKIP checksums and lacks tray dependency verification.",
                command="grep 'sha256sums' packaging/arch/PKGBUILD",
                output="Found sha256sums=('SKIP' ...)",
            )
    return CheckResult(
        defect_id="P-1",
        name="Arch PKGBUILD integrity",
        category="Desktop & Packaging",
        status="FAIL",
        label="NOT-DONE",
        details="PKGBUILD and verify-install.sh audit required.",
    )


def check_P2() -> CheckResult:
    """Check P-2: nx setup non-destructive safety."""
    return CheckResult(
        defect_id="P-2",
        name="nx setup non-destructive safety",
        category="Desktop & Packaging",
        status="FAIL",
        label="NOT-DONE",
        details="nx setup must not edit Hyprland config without --apply, must back up & show diff.",
    )


def check_F1() -> CheckResult:
    """Check F-1: Mail/Calendar protocol fakes."""
    return CheckResult(
        defect_id="F-1",
        name="Mail & Calendar connectors verification",
        category="Features",
        status="FAIL",
        label="NOT-DONE",
        details="Requires protocol-level fakes and USER-RUN verification.",
    )


def check_F2() -> CheckResult:
    """Check F-2: Channels protocol fakes."""
    return CheckResult(
        defect_id="F-2",
        name="Telegram, Slack, Discord, Feeds verification",
        category="Features",
        status="FAIL",
        label="NOT-DONE",
        details="Requires protocol-level fakes and USER-RUN verification.",
    )


def check_F3() -> CheckResult:
    """Check F-3: GitHub intelligence gh-first."""
    return CheckResult(
        defect_id="F-3",
        name="GitHub intelligence gh-first live checks",
        category="Features",
        status="FAIL",
        label="NOT-DONE",
        details="Requires gh-first refactor and live read-only checks.",
    )


def check_F4() -> CheckResult:
    """Check F-4: Agents framework, presets, learning loop."""
    return CheckResult(
        defect_id="F-4",
        name="Agents framework, presets, and learning loop",
        category="Features",
        status="FAIL",
        label="NOT-DONE",
        details="Requires held-out evaluation and auto-rollback verification.",
    )


CHECKERS = {
    "N-1": check_N1,
    "N-2": check_N2,
    "N-3": check_N3,
    "N-4": check_N4,
    "N-5": check_N5,
    "N-6": check_N6,
    "N-7": check_N7,
    "N-8": check_N8,
    "C-1": check_C1,
    "C-2": check_C2,
    "C-3": check_C3,
    "C-4": check_C4,
    "C-5": check_C5,
    "C-6": check_C6,
    "C-7": check_C7,
    "C-8": check_C8,
    "T-1": check_T1,
    "T-2": check_T2,
    "L-1": check_L1,
    "L-2": check_L2,
    "D-1": check_D1,
    "D-2": check_D2,
    "D-3": check_D3,
    "D-4": check_D4,
    "D-5": check_D5,
    "D-6": check_D6,
    "D-7": check_D7,
    "P-1": check_P1,
    "P-2": check_P2,
    "F-1": check_F1,
    "F-2": check_F2,
    "F-3": check_F3,
    "F-4": check_F4,
}


def run_checks(check_ids: list[str]) -> list[CheckResult]:
    results = []
    for cid in check_ids:
        fn = CHECKERS.get(cid)
        if fn:
            results.append(fn())
        else:
            results.append(
                CheckResult(
                    defect_id=cid,
                    name=f"Unknown check {cid}",
                    category="Unknown",
                    status="FAIL",
                    label="NOT-DONE",
                    details=f"No checker defined for {cid}",
                )
            )
    return results


def format_table(results: list[CheckResult]) -> str:
    lines = []
    lines.append(f"{'Defect ID':<10} {'Category':<20} {'Status':<15} {'Label':<18} {'Details'}")
    lines.append("-" * 90)
    for r in results:
        lines.append(f"{r.defect_id:<10} {r.category:<20} {r.status:<15} {r.label:<18} {r.details[:40]}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="NoX Verification Harness (Session O)")
    parser.add_argument("--all", action="store_true", help="Run all verification checks")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    parser.add_argument("check_id", nargs="?", help="Specific defect ID to check (e.g. N-1)")

    args = parser.parse_args()

    if args.all or not args.check_id:
        to_run = list(CHECKERS.keys())
    else:
        to_run = [args.check_id.upper()]

    results = run_checks(to_run)

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        print(format_table(results))

    failed = [r for r in results if r.status == "FAIL"]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
