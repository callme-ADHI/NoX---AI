"""Tests for noxai.db — migration runner and database helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Return a temporary database path and redirect DB_PATH in the module."""
    return tmp_path / "test_nox.db"


@pytest.fixture(autouse=True)
def patch_db_path(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect all db module paths to a temporary location."""
    import noxai.db as db_mod
    monkeypatch.setattr(db_mod, "DB_PATH", db_path)
    monkeypatch.setattr(db_mod, "DATA_DIR", db_path.parent)


class TestMigrations:
    async def test_migrations_apply_on_fresh_db(self, db_path: Path) -> None:
        """Running migrations on a fresh database creates all expected tables."""
        from noxai import db as db_mod
        await db_mod.run_migrations()

        conn = sqlite3.connect(str(db_path))
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()

        required_tables = {
            "schema_migrations", "conversations", "messages", "runs",
            "task_nodes", "facts", "facts_history", "episodes",
            "vec_facts", "vec_episodes",
            "jobs", "job_versions", "job_runs", "outputs",
            "sources", "seen_items", "preferences",
            "skills_index", "skill_versions",
            "approvals", "audit_log", "safety_whitelist",
            "settings", "seed_state",
        }
        missing = required_tables - tables
        assert not missing, f"Tables missing after migration: {sorted(missing)}"

    async def test_migrations_idempotent(self, db_path: Path) -> None:
        """Running migrations twice on the same database produces no errors."""
        from noxai import db as db_mod
        await db_mod.run_migrations()
        # Second run must not raise
        await db_mod.run_migrations()

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT filename FROM schema_migrations").fetchall()
        conn.close()
        # Should still have exactly one row per migration file
        filenames = [r[0] for r in rows]
        assert len(filenames) == len(set(filenames)), "Duplicate migration rows found"

    async def test_wal_mode_enabled(self, db_path: Path) -> None:
        """After migrations, the database must be in WAL journal mode."""
        from noxai import db as db_mod
        await db_mod.run_migrations()

        conn = sqlite3.connect(str(db_path))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal", f"Expected WAL mode, got: {mode!r}"

    async def test_foreign_keys_enabled(self) -> None:
        """The async get_db context manager must have foreign keys enabled."""
        from noxai import db as db_mod
        await db_mod.run_migrations()

        async with db_mod.get_db() as db:
            cursor = await db.execute("PRAGMA foreign_keys")
            row = await cursor.fetchone()
            assert row[0] == 1, "Foreign keys should be ON"

    async def test_schema_migrations_table_has_entries(self, db_path: Path) -> None:
        """schema_migrations has one row per applied migration file."""
        from noxai import db as db_mod
        await db_mod.run_migrations()

        async with db_mod.get_db() as db:
            rows = await db_mod.fetch_all(
                db, "SELECT filename FROM schema_migrations ORDER BY id"
            )
        assert len(rows) >= 1
        assert rows[0]["filename"] == "001_initial.sql"

    async def test_settings_default_rows_inserted(self) -> None:
        """The migration inserts default settings rows."""
        from noxai import db as db_mod
        await db_mod.run_migrations()

        async with db_mod.get_db() as db:
            rows = await db_mod.fetch_all(db, "SELECT key FROM settings")
        keys = {r["key"] for r in rows}
        assert "theme" in keys
        assert "accent_color" in keys


class TestQueryHelpers:
    async def test_fetch_one_returns_none_when_empty(self) -> None:
        """fetch_one returns None for an empty result set."""
        from noxai import db as db_mod
        await db_mod.run_migrations()

        async with db_mod.get_db() as db:
            row = await db_mod.fetch_one(
                db, "SELECT * FROM conversations WHERE id = 'nonexistent'"
            )
        assert row is None

    async def test_execute_and_fetch_all(self) -> None:
        """execute + fetch_all round-trip works for a basic insert."""
        from noxai import db as db_mod
        await db_mod.run_migrations()

        async with db_mod.get_db() as db:
            await db_mod.execute(
                db,
                "INSERT INTO preferences (key, value) VALUES (?, ?)",
                ("test_key", "test_value"),
            )

        async with db_mod.get_db() as db:
            rows = await db_mod.fetch_all(
                db, "SELECT key, value FROM preferences WHERE key = 'test_key'"
            )
        assert len(rows) == 1
        assert rows[0]["key"] == "test_key"
        assert rows[0]["value"] == "test_value"

    async def test_rollback_on_exception(self) -> None:
        """A failed write inside get_db does not persist."""
        from noxai import db as db_mod
        await db_mod.run_migrations()

        with pytest.raises(RuntimeError, match="simulated error"):
            async with db_mod.get_db() as db:
                await db_mod.execute(
                    db,
                    "INSERT INTO preferences (key, value) VALUES (?, ?)",
                    ("rollback_key", "should_not_persist"),
                )
                raise RuntimeError("simulated error")

        async with db_mod.get_db() as db:
            row = await db_mod.fetch_one(
                db, "SELECT * FROM preferences WHERE key = 'rollback_key'"
            )
        assert row is None, "Rolled-back row should not be visible"
