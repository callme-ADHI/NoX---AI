# Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
# commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
# Modified for NoX: Safe agentskills.io SKILL.md frontmatter parser and validation rules (Workstream E).

"""Safe parser and validator for agentskills.io SKILL.md packages.

Validation rules:
- Name in frontmatter must match parent folder name.
- Description must be present and <= 1,024 characters.
- SKILL.md file size must be <= 100 KB.
- No symlinks pointing outside the skill folder directory.
- YAML frontmatter parsed with safe loader only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import re
from typing import Any
import yaml

log = logging.getLogger(__name__)

MAX_SKILL_MD_SIZE_BYTES = 100 * 1024  # 100 KB
MAX_DESCRIPTION_LENGTH = 1024


class SkillValidationError(ValueError):
    """Raised when a skill directory or SKILL.md violates validation rules."""
    pass


@dataclass
class SkillMeta:
    name: str
    description: str
    version: str = "1.0.0"
    author: str = ""
    license: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    raw_frontmatter: dict[str, Any] = field(default_factory=dict)


@dataclass
class Skill:
    meta: SkillMeta
    body: str
    folder_path: Path
    skill_md_path: Path
    scripts: list[str] = field(default_factory=list)
    enabled: bool = True
    allow_scripts: bool = False

    @property
    def name(self) -> str:
        return self.meta.name

    @property
    def description(self) -> str:
        return self.meta.description


def parse_frontmatter_block(content: str) -> tuple[dict[str, Any], str]:
    """Safely parse YAML frontmatter between leading '---' fences and return (meta, body)."""
    content = content.lstrip()
    if not content.startswith("---"):
        raise SkillValidationError("malformed_frontmatter: SKILL.md must begin with '---' YAML frontmatter fence.")

    # Match frontmatter block
    pattern = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
    match = pattern.match(content)
    if not match:
        raise SkillValidationError("malformed_frontmatter: Closing '---' fence not found for YAML frontmatter.")

    fm_yaml, body = match.group(1), match.group(2)

    try:
        data = yaml.safe_load(fm_yaml)
    except Exception as exc:
        raise SkillValidationError(f"malformed_frontmatter: Invalid YAML syntax: {exc}") from exc

    if not isinstance(data, dict):
        raise SkillValidationError("malformed_frontmatter: YAML frontmatter must evaluate to a dictionary.")

    return data, body.strip()


def validate_skill_folder(folder_path: Path) -> Skill:
    """Validate a skill directory and return a structured Skill object."""
    folder_path = folder_path.resolve()
    if not folder_path.is_dir():
        raise SkillValidationError(f"Skill directory does not exist or is not a directory: {folder_path}")

    # Check symlinks pointing outside the skill folder
    for root, dirs, files in os.walk(folder_path, followlinks=False):
        for name in dirs + files:
            p = Path(root) / name
            if p.is_symlink():
                target = p.resolve()
                try:
                    target.relative_to(folder_path)
                except ValueError:
                    raise SkillValidationError(
                        f"escaping_symlink: Symlink '{p.name}' targets '{target}' outside skill folder '{folder_path}'"
                    )

    skill_md_path = folder_path / "SKILL.md"
    if not skill_md_path.exists():
        raise SkillValidationError(f"Missing required SKILL.md in {folder_path}")

    # File size validation
    size = skill_md_path.stat().st_size
    if size > MAX_SKILL_MD_SIZE_BYTES:
        raise SkillValidationError(
            f"oversized: SKILL.md size {size} bytes exceeds maximum allowed {MAX_SKILL_MD_SIZE_BYTES} bytes (100 KB)"
        )

    try:
        content = skill_md_path.read_text(encoding="utf-8")
    except Exception as exc:
        raise SkillValidationError(f"Could not read SKILL.md: {exc}") from exc

    data, body = parse_frontmatter_block(content)

    # Validate name
    name = data.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        raise SkillValidationError("missing_name: SKILL.md frontmatter must contain a non-empty 'name' field.")

    name = name.strip()
    if name != folder_path.name:
        raise SkillValidationError(
            f"name_mismatch: Frontmatter name '{name}' does not match folder name '{folder_path.name}'"
        )

    # Validate description
    desc = data.get("description")
    if not desc or not isinstance(desc, str) or not desc.strip():
        raise SkillValidationError("missing_description: SKILL.md frontmatter must contain a non-empty 'description' field.")

    desc = desc.strip()
    if len(desc) > MAX_DESCRIPTION_LENGTH:
        raise SkillValidationError(
            f"description_too_long: Description length {len(desc)} exceeds {MAX_DESCRIPTION_LENGTH} characters."
        )

    # Detect scripts
    scripts: list[str] = []
    scripts_dir = folder_path / "scripts"
    if scripts_dir.is_dir():
        for sp in scripts_dir.iterdir():
            if sp.is_file() and not sp.name.startswith("."):
                scripts.append(sp.name)

    meta = SkillMeta(
        name=name,
        description=desc,
        version=str(data.get("version", "1.0.0")),
        author=str(data.get("author", "")),
        license=str(data.get("license", "")),
        allowed_tools=list(data.get("allowed_tools", [])),
        raw_frontmatter=data,
    )

    return Skill(
        meta=meta,
        body=body,
        folder_path=folder_path,
        skill_md_path=skill_md_path,
        scripts=scripts,
    )
