"""Tests for Skills loading, frontmatter parsing, indexing, and meta-tools per Section 6.8."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch

import aiosqlite
import pytest

from noxai.config import ModelRoleConfig, ModelsConfig, Settings
from noxai.db import fetch_all
from noxai.models.fake import FakeProvider
from noxai.models.router import ModelRouter
from noxai.skills.loader import (
    ensure_builtin_skills,
    find_relevant_skills,
    index_skills,
    parse_frontmatter,
)
from noxai.tools.builtin.scheduler_tools import (
    ChangeHistoryParams,
    SkillListParams,
    SkillReadParams,
    SkillWriteParams,
    UndoLastChangeParams,
    change_history,
    skill_list,
    skill_read,
    skill_write,
    undo_last_change,
)
from noxai.tools.registry import ToolContext


@pytest.fixture
def test_config() -> Settings:
    return Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="fake", model="fake-brain"),
                "heavy": ModelRoleConfig(provider="fake", model="fake-heavy"),
                "embed": ModelRoleConfig(provider="fake", model="fake-embed"),
            }
        )
    )


@pytest.fixture
def fake_router(test_config: Settings) -> tuple[ModelRouter, FakeProvider]:
    fake = FakeProvider()
    router = ModelRouter(config=test_config, providers={"fake": fake})
    return router, fake


@pytest.fixture
async def skill_db(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    db_file = tmp_path / "nox_skills_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA foreign_keys = ON;")
    with open("src/noxai/migrations/001_initial.sql") as f:
        conn.executescript(f.read())
    with open("src/noxai/migrations/002_spec_alignment.sql") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()

    async_conn = await aiosqlite.connect(str(db_file))
    async_conn.row_factory = aiosqlite.Row
    yield async_conn
    await async_conn.close()


def test_parse_frontmatter_valid() -> None:
    content = (
        "---\n"
        "name: reverse-engineering\n"
        "description: Guidelines for Ghidra and binary disassembly\n"
        "---\n\n"
        "# Reverse Engineering Steps\n1. Disassemble binary.\n2. Trace entry points."
    )
    meta, body = parse_frontmatter(content)
    assert meta["name"] == "reverse-engineering"
    assert meta["description"] == "Guidelines for Ghidra and binary disassembly"
    assert "Disassemble binary" in body


def test_ensure_builtin_skills(tmp_path: Path) -> None:
    skills_dir = tmp_path / "skills"
    ensure_builtin_skills(skills_dir)

    briefing_file = skills_dir / "briefing-style.md"
    research_file = skills_dir / "research-method.md"

    assert briefing_file.exists()
    assert research_file.exists()

    b_meta, b_body = parse_frontmatter(briefing_file.read_text(encoding="utf-8"))
    assert b_meta["name"] == "briefing-style"
    assert "Briefing Style Guidelines" in b_body

    r_meta, r_body = parse_frontmatter(research_file.read_text(encoding="utf-8"))
    assert r_meta["name"] == "research-method"
    assert "Research Methodology Guidelines" in r_body


@pytest.mark.asyncio
async def test_index_and_find_relevant_skills(
    tmp_path: Path,
    skill_db: aiosqlite.Connection,
    fake_router: tuple[ModelRouter, FakeProvider],
) -> None:
    router, _ = fake_router
    skills_dir = tmp_path / "skills"
    ensure_builtin_skills(skills_dir)

    # Index skills
    count = await index_skills(skills_dir=skills_dir, db=skill_db, router=router)
    assert count == 2

    # Verify rows in skills_index table
    rows = await fetch_all(skill_db, "SELECT name, filename FROM skills_index ORDER BY name ASC")
    assert len(rows) == 2
    names = [r["name"] for r in rows]
    assert "briefing-style" in names
    assert "research-method" in names

    # Query for briefing
    matches = await find_relevant_skills(
        "format morning briefing", skill_db, skills_dir=skills_dir, top_k=1
    )
    assert len(matches) == 1
    assert matches[0]["name"] == "briefing-style"
    assert "Briefing Style Guidelines" in matches[0]["body"]


@pytest.mark.asyncio
async def test_skill_meta_tools_crud_and_undo(
    tmp_path: Path,
    skill_db: aiosqlite.Connection,
    fake_router: tuple[ModelRouter, FakeProvider],
    test_config: Settings,
) -> None:
    """skill_write creates skill, skill_read reads it, update creates version,
    undo restores previous.
    """
    router, _ = fake_router
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    ctx = ToolContext(
        run_id="test_skill_meta",
        config=test_config,
        db=skill_db,
        router=router,
    )

    with patch("noxai.tools.builtin.scheduler_tools.DEFAULT_SKILLS_DIR", skills_dir):
        # 1. Create skill: ctf-pwn
        w_res = await skill_write(
            SkillWriteParams(
                name="ctf-pwn",
                description="Exploitation methodology for CTF binary challenges",
                body="# Pwn Methodology\nCheck protections with checksec.",
            ),
            ctx,
        )
        assert w_res.ok

        # 2. Read skill
        r_res = await skill_read(SkillReadParams(name="ctf-pwn"), ctx)
        assert r_res.ok
        assert "Check protections with checksec." in r_res.output

        # 3. List skills
        l_res = await skill_list(SkillListParams(), ctx)
        assert l_res.ok
        assert "ctf-pwn" in l_res.output

        # 4. Update skill with new version
        upd_res = await skill_write(
            SkillWriteParams(
                name="ctf-pwn",
                description="Updated CTF pwn guide",
                body="# Pwn Methodology v2\nUse ROPgadget and pwntools.",
            ),
            ctx,
        )
        assert upd_res.ok

        # Verify version snapshot in DB
        v_rows = await fetch_all(
            skill_db, "SELECT * FROM skill_versions WHERE skill_name = 'ctf-pwn'"
        )
        assert len(v_rows) == 1
        assert "Check protections with checksec." in v_rows[0]["body"]

        # 5. Check history
        hist_res = await change_history(ChangeHistoryParams(entity="skill", name="ctf-pwn"), ctx)
        assert hist_res.ok
        assert "1 versions" in hist_res.summary

        # 6. Undo change
        undo_res = await undo_last_change(UndoLastChangeParams(entity="skill", name="ctf-pwn"), ctx)
        assert undo_res.ok
        assert "restored skill 'ctf-pwn'" in undo_res.output

        # Read after undo: should have restored v1
        restored_res = await skill_read(SkillReadParams(name="ctf-pwn"), ctx)
        assert restored_res.ok
        assert "Check protections with checksec." in restored_res.output
