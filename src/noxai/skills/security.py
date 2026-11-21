"""Static AST and shell script security scanner for untrusted skill packages.

Enforces:
- AST inspection of Python scripts (.py) for forbidden builtins (eval, exec, __import__).
- Detection of dangerous subprocess calls and sensitive path tampering.
- Shell script inspection for destructive commands and remote script pipe-to-shell patterns.
- Staged installation leaving newly imported skills disabled by default.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from noxai.db import get_db

log = logging.getLogger(__name__)

FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__"}
FORBIDDEN_MODULES = {"ctypes", "pty"}
DANGEROUS_SHELL_PATTERNS = [
    re.compile(r"\brm\s+-(?:r|f|rf|fr)\s+[/~]"),
    re.compile(r"curl\s+[^\n|]+\|\s*(?:ba)?sh"),
    re.compile(r"wget\s+[^\n|]+\|\s*(?:ba)?sh"),
    re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s+if="),
]
SENSITIVE_PATHS = [
    "~/.ssh",
    "~/.gnupg",
    "/etc/shadow",
    "/etc/passwd",
    "~/.local/share/nox/nox.db",
]


class SkillSecurityVisitor(ast.NodeVisitor):
    """AST visitor searching for dangerous constructs in Python skill scripts."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.findings: list[dict[str, Any]] = []

    def visit_Call(self, node: ast.Call) -> None:
        # Check call name
        func_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr

        if func_name in FORBIDDEN_CALLS:
            self.findings.append(
                {
                    "file": self.filename,
                    "line": getattr(node, "lineno", 0),
                    "rule": "forbidden_builtin_call",
                    "severity": "critical",
                    "message": f"Forbidden dynamic execution call '{func_name}' detected.",
                }
            )

        # Check subprocess calls for sensitive arguments
        if func_name in ("run", "Popen", "call", "check_output", "system"):
            # Check string arguments
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    for pat in DANGEROUS_SHELL_PATTERNS:
                        if pat.search(arg.value):
                            self.findings.append(
                                {
                                    "file": self.filename,
                                    "line": getattr(node, "lineno", 0),
                                    "rule": "destructive_shell_command",
                                    "severity": "critical",
                                    "message": f"Destructive command in '{arg.value[:45]}'.",
                                }
                            )

        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name in FORBIDDEN_MODULES:
                self.findings.append(
                    {
                        "file": self.filename,
                        "line": getattr(node, "lineno", 0),
                        "rule": "forbidden_module_import",
                        "severity": "warning",
                        "message": f"Import of high-risk module '{alias.name}'.",
                    }
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and node.module in FORBIDDEN_MODULES:
            self.findings.append(
                {
                    "file": self.filename,
                    "line": getattr(node, "lineno", 0),
                    "rule": "forbidden_module_import",
                    "severity": "warning",
                    "message": f"Import from high-risk module '{node.module}'.",
                }
            )
        self.generic_visit(node)


class SkillSecurityScanner:
    """Static security auditor for external skill packages."""

    @classmethod
    def scan_python_file(cls, path: Path) -> list[dict[str, Any]]:
        findings = []
        try:
            content = path.read_text(encoding="utf-8")
            tree = ast.parse(content, filename=str(path))
            visitor = SkillSecurityVisitor(filename=path.name)
            visitor.visit(tree)
            findings.extend(visitor.findings)
        except Exception as exc:
            findings.append(
                {
                    "file": path.name,
                    "line": 0,
                    "rule": "parse_failure",
                    "severity": "warning",
                    "message": f"Unable to parse Python AST: {exc}",
                }
            )
        return findings

    @classmethod
    def scan_shell_file(cls, path: Path) -> list[dict[str, Any]]:
        findings = []
        try:
            content = path.read_text(encoding="utf-8")
            for line_no, line in enumerate(content.splitlines(), start=1):
                for pat in DANGEROUS_SHELL_PATTERNS:
                    if pat.search(line):
                        findings.append(
                            {
                                "file": path.name,
                                "line": line_no,
                                "rule": "destructive_shell_command",
                                "severity": "critical",
                                "message": f"Destructive shell pattern in '{line.strip()[:60]}'.",
                            }
                        )
                for sp in SENSITIVE_PATHS:
                    if sp in line:
                        findings.append(
                            {
                                "file": path.name,
                                "line": line_no,
                                "rule": "sensitive_path_reference",
                                "severity": "warning",
                                "message": f"Reference to sensitive path '{sp}'.",
                            }
                        )
        except Exception as exc:
            findings.append(
                {
                    "file": path.name,
                    "line": 0,
                    "rule": "read_failure",
                    "severity": "info",
                    "message": f"Could not read shell file: {exc}",
                }
            )
        return findings

    @classmethod
    def scan_skill_directory(cls, folder: Path) -> list[dict[str, Any]]:
        """Scan all scripts in skill directory."""
        findings = []
        for p in folder.rglob("*"):
            if p.is_file():
                if p.suffix == ".py":
                    findings.extend(cls.scan_python_file(p))
                elif p.suffix in (".sh", ".bash"):
                    findings.extend(cls.scan_shell_file(p))
        return findings


async def install_untrusted_skill(
    source: str,
    target_name: str | None = None,
    allow_critical: bool = False,
) -> dict[str, Any]:
    """Stage, scan, and install an untrusted skill in disabled state."""
    from noxai.db import get_data_dir

    data_dir = get_data_dir()
    staging_dir = data_dir / "skills_staging"
    staging_dir.mkdir(parents=True, exist_ok=True)

    src_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
    stage_path = staging_dir / src_hash
    if stage_path.exists():
        shutil.rmtree(stage_path)

    # 1. Fetch / Copy to staging area
    if source.startswith(("http://", "https://", "git@")):
        # Clone git repository
        try:
            res = subprocess.run(
                ["git", "clone", "--depth", "1", source, str(stage_path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
            if res.returncode != 0:
                raise RuntimeError(f"Git clone failed: {res.stderr}")
        except Exception as exc:
            raise RuntimeError(f"Failed to clone external skill: {exc}") from exc
    else:
        # Local path copy
        local_src = Path(source).expanduser().resolve()
        if not local_src.exists():
            raise FileNotFoundError(f"Skill path not found: {source}")
        shutil.copytree(local_src, stage_path)

    # 2. Static AST Security Scan
    findings = SkillSecurityScanner.scan_skill_directory(stage_path)
    critical_findings = [f for f in findings if f.get("severity") == "critical"]

    if critical_findings and not allow_critical:
        lead_msg = critical_findings[0]["message"]
        raise PermissionError(
            f"Security scan rejected untrusted skill with {len(critical_findings)} "
            f"critical findings: {lead_msg}"
        )

    # 3. Determine name & install into user skills dir (starts disabled)
    skill_md = stage_path / "SKILL.md"
    name = target_name or (stage_path.name if not skill_md.exists() else "unnamed_skill")
    if skill_md.exists():
        for line in skill_md.read_text(encoding="utf-8").splitlines()[:10]:
            if line.startswith("name:"):
                name = line.replace("name:", "").strip()
                break

    user_skills_dir = data_dir / "skills"
    user_skills_dir.mkdir(parents=True, exist_ok=True)
    dest_path = user_skills_dir / name
    if dest_path.exists():
        shutil.rmtree(dest_path)
    shutil.copytree(stage_path, dest_path)

    # 4. Record installation in database (disabled by default)
    now = datetime.now(UTC).isoformat()
    inst_id = f"skinst_{src_hash}"

    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO skill_installations (
                id, name, source_url, install_path, integrity_hash,
                status, security_findings_json, created_at
            ) VALUES (?, ?, ?, ?, ?, 'disabled', ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                install_path = excluded.install_path,
                security_findings_json = excluded.security_findings_json,
                status = 'disabled'
            """,
            (
                inst_id,
                name,
                source,
                str(dest_path),
                src_hash,
                json.dumps(findings),
                now,
            ),
        )
        await db.commit()

    return {
        "id": inst_id,
        "name": name,
        "path": str(dest_path),
        "status": "disabled",
        "findings_count": len(findings),
        "findings": findings,
    }
