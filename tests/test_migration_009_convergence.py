"""Tests verifying migration 009 convergence across pre-edit, post-edit,
and real DB copy paths (C-7).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import aiosqlite
import pytest

from noxai.db import _configure_async_connection, fetch_all, fetch_one, run_migrations

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "src" / "noxai" / "migrations"


async def apply_sql_file(db: aiosqlite.Connection, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    await db.executescript(sql)
    await db.commit()


@pytest.mark.asyncio
async def test_migration_009_path_a_pre_edit_convergence(tmp_path: Path):
    """Path A: Run 001..008, then 009 WITHOUT cloud.enabled insert, then 010..023.
    Verifies that migration 010 inserts cloud.enabled='true' and subsequent migrations converge.
    """
    db_file = tmp_path / "path_a.db"
    db = await aiosqlite.connect(str(db_file))
    await _configure_async_connection(db)

    # 1. Run migrations 001 through 008
    for i in range(1, 9):
        p = sorted(MIGRATIONS_DIR.glob(f"{i:03d}_*.sql"))[0]
        await apply_sql_file(db, p)

    # 2. Run pre-edit 009 (without cloud.enabled insert)
    p_009 = sorted(MIGRATIONS_DIR.glob("009_*.sql"))[0]
    sql_009 = p_009.read_text(encoding="utf-8")
    assert "cloud.enabled" not in sql_009
    await db.executescript(sql_009)
    await db.commit()

    # Verify settings table exists but cloud.enabled is not yet inserted
    row_before = await fetch_one(db, "SELECT value FROM settings WHERE key = 'cloud.enabled'")
    assert row_before is None

    # 3. Run migration 010 through current (023)
    all_mig = sorted(
        MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"),
        key=lambda x: int(x.stem.split("_")[0]),
    )
    for p in all_mig:
        num = int(p.stem.split("_")[0])
        if num >= 10:
            await apply_sql_file(db, p)

    # Verify settings has cloud.enabled='true'
    row_after = await fetch_one(db, "SELECT value FROM settings WHERE key = 'cloud.enabled'")
    assert row_after is not None
    assert row_after["value"] == "true"

    # Verify key tables exist
    raw_tables = await fetch_all(db, "SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r["name"] for r in raw_tables}
    assert "settings" in tables
    assert "briefing_runs" in tables
    assert "briefing_topic_runs" in tables
    assert "opportunity_registry" in tables
    assert "documents" in tables
    assert "chunks" in tables

    await db.close()


@pytest.mark.asyncio
async def test_migration_009_path_b_post_edit_convergence(tmp_path: Path):
    """Path B: Run 001..008, then edited 009 WITH cloud.enabled insert, then 010..023.
    Verifies that migration 010 gracefully ignores the duplicate insert and converges.
    """
    db_file = tmp_path / "path_b.db"
    db = await aiosqlite.connect(str(db_file))
    await _configure_async_connection(db)

    # 1. Run migrations 001 through 008
    for i in range(1, 9):
        p = sorted(MIGRATIONS_DIR.glob(f"{i:03d}_*.sql"))[0]
        await apply_sql_file(db, p)

    # 2. Run simulated edited 009 (which had INSERT OR IGNORE INTO settings ... cloud.enabled)
    p_009 = sorted(MIGRATIONS_DIR.glob("009_*.sql"))[0]
    sql_009_edited = (
        p_009.read_text(encoding="utf-8")
        + "\nINSERT OR IGNORE INTO settings (key, value) VALUES ('cloud.enabled', 'true');\n"
    )
    await db.executescript(sql_009_edited)
    await db.commit()

    row_before = await fetch_one(db, "SELECT value FROM settings WHERE key = 'cloud.enabled'")
    assert row_before is not None
    assert row_before["value"] == "true"

    # 3. Run migration 010 through current (023)
    all_mig = sorted(
        MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"),
        key=lambda x: int(x.stem.split("_")[0]),
    )
    for p in all_mig:
        num = int(p.stem.split("_")[0])
        if num >= 10:
            await apply_sql_file(db, p)

    # Verify settings still has cloud.enabled='true'
    row_after = await fetch_one(db, "SELECT value FROM settings WHERE key = 'cloud.enabled'")
    assert row_after is not None
    assert row_after["value"] == "true"

    raw_tables = await fetch_all(db, "SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r["name"] for r in raw_tables}
    assert "settings" in tables
    assert "briefing_runs" in tables
    assert "briefing_topic_runs" in tables
    assert "opportunity_registry" in tables
    assert "documents" in tables
    assert "chunks" in tables

    await db.close()


@pytest.mark.asyncio
async def test_migration_009_real_db_copy_convergence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Path C: Run full migrations on an isolated copy of ~/.local/share/nox/nox.db."""
    from tests.conftest import REAL_DATA_NOX

    real_db = REAL_DATA_NOX / "nox.db"
    if not real_db.exists():
        real_db = Path("/home/boss/.local/share/nox/nox.db")
    if not real_db.exists():
        pytest.skip(f"Real DB does not exist at {real_db}")

    copy_db = tmp_path / "real_copy.db"
    shutil.copy2(real_db, copy_db)

    monkeypatch.setattr("noxai.db.DB_PATH", copy_db)
    monkeypatch.setenv("NOX_DATABASE_PATH", str(copy_db))

    # Apply all migrations via the standard migrator
    await run_migrations()

    db = await aiosqlite.connect(str(copy_db))
    await _configure_async_connection(db)

    row = await fetch_one(db, "SELECT value FROM settings WHERE key = 'cloud.enabled'")
    assert row is not None
    assert row["value"] in ("true", "false")

    raw_tables = await fetch_all(db, "SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r["name"] for r in raw_tables}
    assert "settings" in tables
    assert "briefing_runs" in tables
    assert "briefing_topic_runs" in tables
    assert "opportunity_registry" in tables

    await db.close()
