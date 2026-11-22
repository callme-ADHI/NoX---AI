"""Static AST, shell script, JavaScript, and Markdown security scanner for skill packages.

Enforces:
- AST inspection of Python scripts (.py) for forbidden builtins (eval, exec,
  __import__), subprocess calls, and path escapes.
- JavaScript / TypeScript inspection (.js, .ts, .mjs) for child_process, exec, eval,
  and destructive fs operations.
- Shell script inspection (.sh, .bash) for destructive commands, curl | sh, and credential theft.
- Markdown file inspection (.md) for prompt injection, canary tokens, and embedded
  malicious code fences.
- Staged installation leaving newly imported skills disabled by default.
- Cryptographically chained audit logging for all skill installations and security rejections.
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
from noxai.security.audit import log_audit_event

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
    re.compile(r"base64\s+(?:-d|--decode)\s*\|\s*(?:ba)?sh"),
    re.compile(r"\.\./"),
]
SENSITIVE_PATHS = [
    "~/.ssh",
    "id_rsa",
    "~/.gnupg",
    "/etc/shadow",
    "/etc/passwd",
    "~/.local/share/nox/nox.db",
]

DANGEROUS_JS_PATTERNS = [
    (
        re.compile(r"\b(?:child_process|execSync|spawnSync|fork)\b"),
        "critical",
        "nodejs_subprocess_execution",
        "Node.js child_process or command execution",
    ),
    (
        re.compile(r"\b(?:exec|spawn)\s*\("),
        "critical",
        "nodejs_subprocess_execution",
        "Subprocess execution call",
    ),
    (
        re.compile(r"\beval\s*\("),
        "critical",
        "forbidden_dynamic_eval",
        "Dynamic JavaScript eval()",
    ),
    (
        re.compile(r"\bnew\s+Function\s*\("),
        "critical",
        "forbidden_dynamic_function",
        "Dynamic Function() constructor",
    ),
    (
        re.compile(r"Buffer\.from\([^)]*['\"]base64['\"]"),
        "warning",
        "obfuscated_base64_payload",
        "Base64 buffer decoding",
    ),
    (
        re.compile(r"\b(?:fs\.rmSync|fs\.unlinkSync|fs\.rmdirSync)\b"),
        "critical",
        "destructive_fs_operation",
        "Destructive filesystem operation",
    ),
    (
        re.compile(r"\.\./"),
        "warning",
        "path_traversal",
        "Path traversal escape attempt ('../')",
    ),
    (
        re.compile(r"~/\.ssh|\.ssh/id_rsa|/etc/shadow|\.gnupg"),
        "critical",
        "sensitive_path_reference",
        "Access to sensitive credentials",
    ),
]

DANGEROUS_MD_PATTERNS = [
    (
        re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.IGNORECASE),
        "critical",
        "prompt_injection",
        "Prompt injection pattern: ignore previous instructions",
    ),
    (
        re.compile(r"disregard\s+(?:all\s+)?prior\s+instructions", re.IGNORECASE),
        "critical",
        "prompt_injection",
        "Prompt injection pattern: disregard prior instructions",
    ),
    (
        re.compile(r"you\s+are\s+now\s+(?:DAN|unrestricted|jailbroken)", re.IGNORECASE),
        "critical",
        "prompt_injection",
        "Jailbreak attempt in skill instructions",
    ),
    (
        re.compile(r"<script\b", re.IGNORECASE),
        "critical",
        "script_injection",
        "HTML script tag in Markdown",
    ),
    (
        re.compile(r"curl\s+[^\n|`]+\|\s*(?:ba)?sh"),
        "critical",
        "destructive_shell_command",
        "Remote script pipe-to-shell in markdown snippet",
    ),
    (
        re.compile(r"\brm\s+-(?:r|f|rf|fr)\s+[/~]"),
        "critical",
        "destructive_shell_command",
        "Destructive rm command in markdown snippet",
    ),
    (
        re.compile(r"\.\./"),
        "warning",
        "path_traversal",
        "Path traversal escape attempt ('../')",
    ),
    (
        re.compile(r"~/\.ssh|\.ssh/id_rsa|/etc/shadow|\.gnupg"),
        "critical",
        "sensitive_path_reference",
        "Access to sensitive credentials",
    ),
    (
        re.compile(r"[A-Za-z0-9+/]{80,}={0,2}"),
        "warning",
        "obfuscated_base64_payload",
        "High-entropy base64 blob",
    ),
]


class SkillSecurityVisitor(ast.NodeVisitor):
    """AST visitor searching for dangerous constructs in Python skill scripts."""

    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.findings: list[dict[str, Any]] = []

    def visit_Call(self, node: ast.Call) -> None:
        func_name = ""
        full_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
            full_name = func_name
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
            if isinstance(node.func.value, ast.Name):
                full_name = f"{node.func.value.id}.{func_name}"
            else:
                full_name = func_name

        if func_name in FORBIDDEN_CALLS or full_name in FORBIDDEN_CALLS:
            self.findings.append(
                {
                    "file": self.filename,
                    "line": getattr(node, "lineno", 0),
                    "rule": "forbidden_builtin_call",
                    "severity": "critical",
                    "message": f"Forbidden dynamic execution call '{func_name}' detected.",
                }
            )

        is_sys = (
            func_name in ("system", "popen", "spawn")
            or full_name.startswith("os.system")
            or full_name.startswith("os.popen")
        )
        if is_sys:
            self.findings.append(
                {
                    "file": self.filename,
                    "line": getattr(node, "lineno", 0),
                    "rule": "os_command_execution",
                    "severity": "critical",
                    "message": f"OS command execution via '{full_name or func_name}'.",
                }
            )

        is_sub = (
            func_name in ("run", "Popen", "call", "check_output")
            or full_name.startswith("subprocess.")
        )
        if is_sub:
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

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            val = node.value
            for sp in SENSITIVE_PATHS:
                if sp in val:
                    is_crit = "id_rsa" in sp or "shadow" in sp
                    self.findings.append(
                        {
                            "file": self.filename,
                            "line": getattr(node, "lineno", 0),
                            "rule": "sensitive_path_reference",
                            "severity": "critical" if is_crit else "warning",
                            "message": f"Reference to sensitive path '{sp}'.",
                        }
                    )
            if "../" in val:
                self.findings.append(
                    {
                        "file": self.filename,
                        "line": getattr(node, "lineno", 0),
                        "rule": "path_traversal",
                        "severity": "warning",
                        "message": "Path traversal pattern ('../') found in string literal.",
                    }
                )
            if len(val) >= 80 and re.match(r"^[A-Za-z0-9+/]{80,}={0,2}$", val):
                self.findings.append(
                    {
                        "file": self.filename,
                        "line": getattr(node, "lineno", 0),
                        "rule": "obfuscated_base64_payload",
                        "severity": "warning",
                        "message": "Suspicious high-entropy base64 blob in string literal.",
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
    """Multi-language static security auditor for external skill packages."""

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
                        is_crit = "id_rsa" in sp or "shadow" in sp
                        findings.append(
                            {
                                "file": path.name,
                                "line": line_no,
                                "rule": "sensitive_path_reference",
                                "severity": "critical" if is_crit else "warning",
                                "message": f"Reference to sensitive path '{sp}'.",
                            }
                        )
                if re.search(r"[A-Za-z0-9+/]{80,}={0,2}", line):
                    findings.append(
                        {
                            "file": path.name,
                            "line": line_no,
                            "rule": "obfuscated_base64_payload",
                            "severity": "warning",
                            "message": "High-entropy base64 blob found in shell script.",
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
    def scan_javascript_file(cls, path: Path) -> list[dict[str, Any]]:
        findings = []
        try:
            content = path.read_text(encoding="utf-8")
            for line_no, line in enumerate(content.splitlines(), start=1):
                for pat, sev, rule, desc in DANGEROUS_JS_PATTERNS:
                    if pat.search(line):
                        findings.append(
                            {
                                "file": path.name,
                                "line": line_no,
                                "rule": rule,
                                "severity": sev,
                                "message": f"{desc} in '{line.strip()[:60]}'.",
                            }
                        )
        except Exception as exc:
            findings.append(
                {
                    "file": path.name,
                    "line": 0,
                    "rule": "read_failure",
                    "severity": "info",
                    "message": f"Could not read JavaScript file: {exc}",
                }
            )
        return findings

    @classmethod
    def scan_markdown_file(cls, path: Path) -> list[dict[str, Any]]:
        findings = []
        try:
            content = path.read_text(encoding="utf-8")
            for line_no, line in enumerate(content.splitlines(), start=1):
                for pat, sev, rule, desc in DANGEROUS_MD_PATTERNS:
                    if pat.search(line):
                        findings.append(
                            {
                                "file": path.name,
                                "line": line_no,
                                "rule": rule,
                                "severity": sev,
                                "message": f"{desc} in '{line.strip()[:60]}'.",
                            }
                        )
        except Exception as exc:
            findings.append(
                {
                    "file": path.name,
                    "line": 0,
                    "rule": "read_failure",
                    "severity": "info",
                    "message": f"Could not read Markdown file: {exc}",
                }
            )
        return findings

    @classmethod
    def scan_skill_directory(cls, folder: Path) -> list[dict[str, Any]]:
        """Scan all code and documentation files in skill directory."""
        findings = []
        for p in folder.rglob("*"):
            if p.is_file():
                if p.suffix == ".py":
                    findings.extend(cls.scan_python_file(p))
                elif p.suffix in (".sh", ".bash", ".zsh"):
                    findings.extend(cls.scan_shell_file(p))
                elif p.suffix in (".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx"):
                    findings.extend(cls.scan_javascript_file(p))
                elif p.suffix in (".md", ".markdown"):
                    findings.extend(cls.scan_markdown_file(p))
        return findings


async def install_untrusted_skill(
    source: str,
    target_name: str | None = None,
    allow_critical: bool = False,
) -> dict[str, Any]:
    """Stage, scan, and install an untrusted skill in disabled state with audit logging."""
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
        local_src = Path(source).expanduser().resolve()
        if not local_src.exists():
            raise FileNotFoundError(f"Skill path not found: {source}")
        shutil.copytree(local_src, stage_path)

    # 2. Multi-Language Security Scan
    findings = SkillSecurityScanner.scan_skill_directory(stage_path)
    critical_findings = [f for f in findings if f.get("severity") == "critical"]

    # Determine name early for audit logging
    skill_md = stage_path / "SKILL.md"
    name = target_name or (stage_path.name if not skill_md.exists() else "unnamed_skill")
    if skill_md.exists():
        for line in skill_md.read_text(encoding="utf-8").splitlines()[:10]:
            if line.startswith("name:"):
                name = line.replace("name:", "").strip()
                break

    if critical_findings and not allow_critical:
        lead_msg = critical_findings[0]["message"]
        await log_audit_event(
            actor="user",
            action="skill_install",
            target=name or source,
            decision="deny",
            summary_redacted=(
                f"Security scan rejected untrusted skill {name} with "
                f"{len(critical_findings)} critical findings: {lead_msg}"
            ),
        )
        raise PermissionError(
            f"Security scan rejected untrusted skill with {len(critical_findings)} "
            f"critical findings: {lead_msg}"
        )

    # 3. Install into user skills dir (starts disabled)
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

    await log_audit_event(
        actor="user",
        action="skill_install",
        target=name,
        decision="allow",
        summary_redacted=(
            f"Installed untrusted skill {name} in disabled state ({len(findings)} findings)"
        ),
    )

    return {
        "id": inst_id,
        "name": name,
        "path": str(dest_path),
        "status": "disabled",
        "findings_count": len(findings),
        "findings": findings,
    }
