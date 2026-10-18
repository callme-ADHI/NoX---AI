"""Database connection, migration runner and helpers for NoX.

Uses SQLite in WAL mode with foreign keys enabled. Migrations are numbered
SQL files under ``noxai/migrations/``. The migration runner is idempotent:
it skips already-applied migrations tracked in ``schema_migrations``.

Vector backend detection:
- Attempts to load ``sqlite-vec`` (via the ``sqlite_vec`` Python package).
- On failure, sets ``VECTOR_BACKEND = "numpy"`` and logs a warning.
  The numpy fallback stores float32 blobs and performs brute-force cosine
  similarity at query time (acceptable under ~50k vectors).

Usage::

    from noxai.db import get_db, run_migrations

    await run_migrations()

    async with get_db() as db:
        rows = await fetch_all(db, "SELECT * FROM jobs WHERE enabled = 1")
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_DIR = Path("~/.local/share/nox").expanduser()
DB_PATH = DATA_DIR / "nox.db"
MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# ---------------------------------------------------------------------------
# Vector backend detection
# ---------------------------------------------------------------------------

VECTOR_BACKEND: str = "numpy"  # "vec" | "numpy"


def _try_load_sqlite_vec() -> bool:
    """Attempt to load the sqlite-vec extension into a probe connection.

    Returns True if loading succeeded, False otherwise.
    """
    try:
        import sqlite_vec  # type: ignore[import-untyped]

        conn = sqlite3.connect(":memory:")
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.close()
        return True
    except Exception:
        return False


def _detect_vector_backend() -> str:
    if _try_load_sqlite_vec():
        log.info("vector_backend=vec sqlite-vec extension loaded successfully")
        return "vec"
    log.warning(
        "vector_backend=numpy sqlite-vec not available; "
        "using numpy brute-force cosine similarity (fine under ~50k vectors)"
    )
    return "numpy"


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


def _configure_sync_connection(conn: sqlite3.Connection) -> None:
    """Apply pragmas to a newly opened synchronous connection."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")  # 64 MB page cache
    conn.row_factory = sqlite3.Row


async def _configure_async_connection(db: aiosqlite.Connection) -> None:
    """Apply pragmas to a newly opened async connection."""
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute("PRAGMA cache_size=-64000")
    db.row_factory = aiosqlite.Row


@asynccontextmanager
async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """Async context manager that yields a configured aiosqlite connection.

    The connection is committed on clean exit and rolled back on exception.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await _configure_async_connection(db)
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


async def execute(
    db: aiosqlite.Connection,
    sql: str,
    params: tuple[Any, ...] | dict[str, Any] = (),
) -> aiosqlite.Cursor:
    """Execute a SQL statement and return the cursor."""
    return await db.execute(sql, params)


async def fetch_one(
    db: aiosqlite.Connection,
    sql: str,
    params: tuple[Any, ...] | dict[str, Any] = (),
) -> aiosqlite.Row | None:
    """Fetch a single row or None."""
    cursor = await db.execute(sql, params)
    return await cursor.fetchone()


async def fetch_all(
    db: aiosqlite.Connection,
    sql: str,
    params: tuple[Any, ...] | dict[str, Any] = (),
) -> list[aiosqlite.Row]:
    """Fetch all rows as a list."""
    cursor = await db.execute(sql, params)
    rows = await cursor.fetchall()
    return list(rows)


async def lastrowid(cursor: aiosqlite.Cursor) -> int | None:
    """Return the last inserted row id from a cursor."""
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------


async def run_migrations() -> None:
    """Apply all pending database migrations in order.

    Scans ``noxai/migrations/`` for files matching ``NNN_*.sql`` (where NNN
    is a zero-padded integer), sorts them numerically, and applies each one
    that has not already been recorded in ``schema_migrations``.

    This function is idempotent: running it twice on the same database is safe
    and produces no errors.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Use sync sqlite3 for migrations to keep them simple and atomic
    conn = sqlite3.connect(DB_PATH)
    _configure_sync_connection(conn)

    try:
        # Bootstrap the migrations tracking table if it doesn't exist
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id          INTEGER PRIMARY KEY,
                filename    TEXT    NOT NULL UNIQUE,
                applied_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.commit()

        # Discover migration files
        migration_files = sorted(
            MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"),
            key=lambda p: int(p.stem.split("_")[0]),
        )

        if not migration_files:
            log.warning("No migration files found in %s", MIGRATIONS_DIR)
            return

        for migration_file in migration_files:
            filename = migration_file.name

            # Check if already applied
            row = conn.execute(
                "SELECT id FROM schema_migrations WHERE filename = ?", (filename,)
            ).fetchone()
            if row is not None:
                log.debug("migration_skip filename=%s (already applied)", filename)
                continue

            log.info("migration_apply filename=%s", filename)
            sql = migration_file.read_text(encoding="utf-8")

            try:
                conn.executescript(sql)
                conn.execute("INSERT INTO schema_migrations (filename) VALUES (?)", (filename,))
                conn.commit()
                log.info("migration_done filename=%s", filename)
            except sqlite3.Error as exc:
                conn.rollback()
                raise RuntimeError(
                    f"Migration {filename} failed: {exc}\n"
                    "The database has been rolled back. "
                    "Fix the migration file and restart."
                ) from exc
    finally:
        conn.close()

    # After migrations, detect the vector backend
    global VECTOR_BACKEND
    VECTOR_BACKEND = _detect_vector_backend()
    log.info("startup vector_backend=%s db_path=%s", VECTOR_BACKEND, DB_PATH)


# ---------------------------------------------------------------------------
# Initialisation helper (called at daemon startup)
# ---------------------------------------------------------------------------


async def init_db() -> None:
    """Initialise the database: create directories, run migrations.

    This is the single entry point called from the daemon startup sequence.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    await run_migrations()
