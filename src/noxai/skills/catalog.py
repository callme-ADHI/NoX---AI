# Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
# commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
# Modified for NoX: Skills catalog, progressive prompt loading, script safety gates, and CLI management (Workstream E).

"""Skills Catalog and Lifecycle Management.

Supports:
- Multi-root discovery: repo `skills/` and user `~/.local/share/nox/skills/`
- Progressive prompt loading: compact catalog in prompt, on-demand full body retrieval
- Strict script execution safety: scripts disabled by default, requiring explicit permission
- Staged installation with validation and typed user confirmation
- Persistence of enabled / disabled and script execution states in SQLite
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

import aiosqlite

from noxai.db import execute, fetch_all, fetch_one, get_data_dir, get_db
from noxai.skills.parser import Skill, SkillValidationError, validate_skill_folder

log = logging.getLogger(__name__)


def get_repo_skills_dir() -> Path:
    """Return repo-level shipped skills directory."""
    # Find project root relative to this file
    root = Path(__file__).resolve().parent.parent.parent.parent
    repo_skills = root / "skills"
    return repo_skills


def get_user_skills_dir() -> Path:
    """Return active user skills directory (~/.local/share/nox/skills)."""
    return get_data_dir() / "skills"


async def ensure_skills_schema(db: aiosqlite.Connection) -> None:
    """Ensure skills_state table exists."""
    await execute(
        db,
        """
        CREATE TABLE IF NOT EXISTS skills_state (
            name            TEXT PRIMARY KEY,
            enabled         INTEGER NOT NULL DEFAULT 1,
            allow_scripts   INTEGER NOT NULL DEFAULT 0,
            source_path     TEXT NOT NULL,
            installed_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
        """,
    )
    await db.commit()


class SkillsCatalog:
    """In-memory catalog and lifecycle manager for NoX skills."""

    def __init__(self, user_dir: Path | None = None, repo_dir: Path | None = None) -> None:
        self.user_dir = user_dir or get_user_skills_dir()
        self.repo_dir = repo_dir or get_repo_skills_dir()
        self._skills: dict[str, Skill] = {}

    def discover_skills(self) -> dict[str, Skill]:
        """Scan repo and user skills folders, parse, and validate each skill package."""
        skills: dict[str, Skill] = {}

        # 1. Shipped repo skills (base)
        if self.repo_dir.is_dir():
            for folder in self.repo_dir.iterdir():
                if folder.is_dir() and not folder.name.startswith("."):
                    try:
                        s = validate_skill_folder(folder)
                        skills[s.name] = s
                    except Exception as exc:
                        log.warning("Could not load repo skill '%s': %s", folder.name, exc)

        # 2. User installed skills (can override or add)
        if self.user_dir.is_dir():
            for folder in self.user_dir.iterdir():
                if folder.is_dir() and not folder.name.startswith("."):
                    try:
                        s = validate_skill_folder(folder)
                        skills[s.name] = s
                    except Exception as exc:
                        log.warning("Could not load user skill '%s': %s", folder.name, exc)

        self._skills = skills
        return skills

    async def sync_state(self, db: aiosqlite.Connection | None = None) -> None:
        """Synchronize in-memory skills with database state table."""
        self.discover_skills()

        async def _sync(conn: aiosqlite.Connection) -> None:
            await ensure_skills_schema(conn)
            rows = await fetch_all(conn, "SELECT name, enabled, allow_scripts FROM skills_state")
            state_map = {}
            for r in rows:
                if hasattr(r, "keys"):
                    state_map[r["name"]] = {"name": r["name"], "enabled": r["enabled"], "allow_scripts": r["allow_scripts"]}
                else:
                    state_map[r[0]] = {"name": r[0], "enabled": r[1], "allow_scripts": r[2]}

            for name, skill in self._skills.items():
                if name in state_map:
                    skill.enabled = bool(state_map[name]["enabled"])
                    skill.allow_scripts = bool(state_map[name]["allow_scripts"])
                else:
                    # Shipped repo skills default to enabled=1; user skills default to enabled=0
                    is_user = str(self.user_dir) in str(skill.folder_path)
                    default_enabled = 0 if is_user else 1
                    skill.enabled = bool(default_enabled)
                    skill.allow_scripts = False
                    await execute(
                        conn,
                        """
                        INSERT OR IGNORE INTO skills_state (name, enabled, allow_scripts, source_path)
                        VALUES (?, ?, 0, ?)
                        """,
                        (name, default_enabled, str(skill.folder_path)),
                    )
            await conn.commit()

        if db is not None:
            await _sync(db)
        else:
            async with get_db() as conn:
                await _sync(conn)

    def get_skill(self, name: str) -> Skill | None:
        """Retrieve a skill by name."""
        if not self._skills:
            self.discover_skills()
        return self._skills.get(name)

    def list_skills(self) -> list[Skill]:
        """List all discovered skills."""
        if not self._skills:
            self.discover_skills()
        return list(self._skills.values())

    async def enable_skill(self, name: str, db: aiosqlite.Connection | None = None) -> bool:
        """Enable a skill."""
        await self.sync_state(db)
        skill = self._skills.get(name)
        if not skill:
            return False
        skill.enabled = True

        async def _update(conn: aiosqlite.Connection) -> None:
            await execute(
                conn,
                "UPDATE skills_state SET enabled = 1 WHERE name = ?",
                (name,),
            )
            await conn.commit()

        if db is not None:
            await _update(db)
        else:
            async with get_db() as conn:
                await _update(conn)
        return True

    async def disable_skill(self, name: str, db: aiosqlite.Connection | None = None) -> bool:
        """Disable a skill."""
        await self.sync_state(db)
        skill = self._skills.get(name)
        if not skill:
            return False
        skill.enabled = False

        async def _update(conn: aiosqlite.Connection) -> None:
            await execute(
                conn,
                "UPDATE skills_state SET enabled = 0 WHERE name = ?",
                (name,),
            )
            await conn.commit()

        if db is not None:
            await _update(db)
        else:
            async with get_db() as conn:
                await _update(conn)
        return True

    async def set_allow_scripts(self, name: str, allow: bool, db: aiosqlite.Connection | None = None) -> bool:
        """Set allow_scripts permission for a skill."""
        await self.sync_state(db)
        skill = self._skills.get(name)
        if not skill:
            return False
        skill.allow_scripts = allow

        async def _update(conn: aiosqlite.Connection) -> None:
            await execute(
                conn,
                "UPDATE skills_state SET allow_scripts = ? WHERE name = ?",
                (1 if allow else 0, name),
            )
            await conn.commit()

        if db is not None:
            await _update(db)
        else:
            async with get_db() as conn:
                await _update(conn)
        return True

    async def remove_skill(self, name: str, db: aiosqlite.Connection | None = None) -> bool:
        """Remove a user installed skill."""
        await self.sync_state(db)
        skill = self._skills.get(name)
        if not skill:
            return False

        # Only allow removing skills from user_dir
        if str(self.user_dir) not in str(skill.folder_path):
            raise PermissionError(f"Cannot remove shipped system skill '{name}'. You can disable it instead.")

        shutil.rmtree(skill.folder_path, ignore_errors=True)
        del self._skills[name]

        async def _del(conn: aiosqlite.Connection) -> None:
            await execute(conn, "DELETE FROM skills_state WHERE name = ?", (name,))
            await conn.commit()

        if db is not None:
            await _del(db)
        else:
            async with get_db() as conn:
                await _del(conn)
        return True

    def install_skill_from_path(
        self,
        source_path: Path,
        confirm: bool = True,
    ) -> Skill:
        """Stage, validate, and install a skill folder into user skills directory."""
        source_path = source_path.resolve()
        if not source_path.is_dir():
            raise FileNotFoundError(f"Source path is not a directory: {source_path}")

        with tempfile.TemporaryDirectory(prefix="nox_skill_stage_") as stage_tmp:
            stage_dir = Path(stage_tmp) / source_path.name
            shutil.copytree(source_path, stage_dir, symlinks=True)

            # Validate in staging
            skill = validate_skill_folder(stage_dir)

            self.user_dir.mkdir(parents=True, exist_ok=True)
            target_dir = self.user_dir / skill.name
            if target_dir.exists():
                shutil.rmtree(target_dir)

            shutil.copytree(stage_dir, target_dir)

        # Newly installed skills start disabled per Session M spec
        installed = validate_skill_folder(target_dir)
        installed.enabled = False
        installed.allow_scripts = False
        self._skills[installed.name] = installed
        return installed

    def get_prompt_catalog(self) -> str:
        """Generate compact list of enabled skills for agent system prompt."""
        enabled = [s for s in self.list_skills() if s.enabled]
        if not enabled:
            return ""
        lines = ["Available Reusable Skills:"]
        for s in enabled:
            lines.append(f"- **{s.name}**: {s.description}")
        return "\n".join(lines)

    def load_skill_body(self, name: str) -> str | None:
        """Progressive loading: retrieve full instruction body for an enabled skill."""
        skill = self.get_skill(name)
        if skill and skill.enabled:
            return skill.body
        return None

    def execute_script(
        self,
        skill_name: str,
        script_name: str,
        args: list[str] | None = None,
        timeout: float = 30.0,
    ) -> subprocess.CompletedProcess[str]:
        """Execute a script inside a skill folder subject to strict safety gates."""
        skill = self.get_skill(skill_name)
        if not skill:
            raise ValueError(f"Skill '{skill_name}' not found.")

        # Safety gate check
        if not skill.allow_scripts:
            raise PermissionError(
                f"Refusing script execution: Skill '{skill_name}' has scripts disabled by default. "
                f"Enable execution with 'nx skill allow-scripts {skill_name}' or in configuration."
            )

        script_path = skill.folder_path / "scripts" / script_name
        if not script_path.exists():
            raise FileNotFoundError(f"Script '{script_name}' not found in skill '{skill_name}'.")

        cmd = [str(script_path)] + (args or [])
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )


_default_catalog: SkillsCatalog | None = None


def get_skills_catalog() -> SkillsCatalog:
    """Return singleton SkillsCatalog instance."""
    global _default_catalog
    if _default_catalog is None:
        _default_catalog = SkillsCatalog()
    return _default_catalog
