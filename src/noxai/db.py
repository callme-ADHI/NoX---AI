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
import os
import shutil
import sqlite3
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def get_data_dir() -> Path:
    """Return the active data directory, honoring NOX_DATA_DIR override."""
    if "NOX_DATA_DIR" in os.environ:
        return Path(os.environ["NOX_DATA_DIR"]).expanduser().resolve()
    return DATA_DIR


DATA_DIR = Path("~/.local/share/nox").expanduser().resolve()


def get_db_path() -> Path:
    """Return the active database path, allowing NOX_DATABASE_PATH override."""
    if "NOX_DATABASE_PATH" in os.environ:
        return Path(os.environ["NOX_DATABASE_PATH"]).expanduser().resolve()
    return DB_PATH


DB_PATH = DATA_DIR / "nox.db"


def get_backup_dir() -> Path:
    """Return the active database backups directory (~/.local/share/nox/backups)."""
    return get_data_dir() / "backups"


def backup_db(db_path: Path | None = None, keep: int = 10) -> Path | None:
    """Create an atomic, timestamped backup of the database and prune old ones.

    Uses the SQLite online backup API to ensure 100% transactional consistency
    even when running under WAL mode. Keeps the last `keep` backups.
    """
    path = db_path or get_db_path()
    if not path.exists() or path.stat().st_size == 0:
        return None

    b_dir = get_backup_dir()
    b_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    rand_suffix = uuid.uuid4().hex[:6]
    backup_file = b_dir / f"nox_{ts}_{rand_suffix}.db"

    # Use SQLite online backup API
    src = sqlite3.connect(path)
    try:
        dest = sqlite3.connect(backup_file)
        try:
            src.backup(dest)
        finally:
            dest.close()
    finally:
        src.close()

    log.info("database_backup_created path=%s", backup_file)

    # Prune older backups keeping last `keep`
    prune_old_backups(keep=keep)
    return backup_file


def list_backups(backup_dir: Path | None = None) -> list[Path]:
    """Return sorted list of existing database backups (newest first)."""
    b_dir = backup_dir or get_backup_dir()
    if not b_dir.exists():
        return []
    files = list(b_dir.glob("nox_*.db"))
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def prune_old_backups(keep: int = 10) -> None:
    """Prune database backups keeping only the most recent `keep` files."""
    backups = list_backups()
    if len(backups) > keep:
        for old in backups[keep:]:
            try:
                old.unlink()
                log.info("database_backup_pruned path=%s", old)
            except OSError as err:
                log.warning("database_backup_prune_failed path=%s err=%s", old, err)


def restore_db(backup_file: Path, target_path: Path | None = None) -> Path:
    """Restore target database from a backup file after creating a safety backup."""
    target = target_path or get_db_path()
    if not backup_file.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_file}")

    # Verify backup is a valid SQLite DB
    test_conn = sqlite3.connect(backup_file)
    try:
        test_conn.execute("PRAGMA schema_version").fetchone()
    finally:
        test_conn.close()

    # Create safety backup of current target before replacing
    if target.exists() and target.stat().st_size > 0:
        backup_db(target, keep=10)

    # Copy backup to target
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_file, target)

    # Remove stale -wal and -shm files if present
    for suffix in ("-wal", "-shm"):
        extra = target.with_name(target.name + suffix)
        if extra.exists():
            try:
                extra.unlink()
            except OSError:
                pass

    log.info("database_restored from=%s to=%s", backup_file, target)
    return target


DB_PATH = get_db_path()
MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# ---------------------------------------------------------------------------
# Vector backend detection
# ---------------------------------------------------------------------------

VECTOR_BACKEND: str = "numpy"  # "vec" | "numpy"
_CACHED_VECTOR_BACKEND: str | None = None


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
    global _CACHED_VECTOR_BACKEND
    if _CACHED_VECTOR_BACKEND is not None:
        return _CACHED_VECTOR_BACKEND

    import os

    if os.getenv("NOX_FORCE_NUMPY_VECTORS", "0") == "1":
        log.info("vector_backend=numpy forced via NOX_FORCE_NUMPY_VECTORS=1")
        _CACHED_VECTOR_BACKEND = "numpy"
        return "numpy"
    if _try_load_sqlite_vec():
        log.info("vector_backend=vec sqlite-vec extension loaded successfully")
        _CACHED_VECTOR_BACKEND = "vec"
        return "vec"
    log.warning(
        "vector_backend=numpy sqlite-vec not available; "
        "using numpy brute-force cosine similarity (fine under ~50k vectors)"
    )
    _CACHED_VECTOR_BACKEND = "numpy"
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
    conn.execute("PRAGMA busy_timeout=30000")
    conn.row_factory = sqlite3.Row


async def _configure_async_connection(db: aiosqlite.Connection) -> None:
    """Apply pragmas to a newly opened async connection."""
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute("PRAGMA cache_size=-64000")
    await db.execute("PRAGMA busy_timeout=30000")
    db.row_factory = aiosqlite.Row


@asynccontextmanager
async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """Async context manager that yields a configured aiosqlite connection.

    The connection is committed on clean exit and rolled back on exception.
    """
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path, timeout=30.0) as db:
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
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Use sync sqlite3 for migrations to keep them simple and atomic
    conn = sqlite3.connect(path)
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

        applied_rows = conn.execute("SELECT filename FROM schema_migrations").fetchall()
        applied_set = {r[0] for r in applied_rows}
        pending = [m for m in migration_files if m.name not in applied_set]

        # Automatic timestamped backup before running any pending migrations
        if pending and path.exists() and path.stat().st_size > 0:
            log.info(
                "Creating automatic pre-migration backup for %d pending migrations",
                len(pending),
            )
            backup_db(path, keep=10)

        for migration_file in pending:
            filename = migration_file.name

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
    already_detected = _CACHED_VECTOR_BACKEND is not None
    VECTOR_BACKEND = _detect_vector_backend()
    if not already_detected:
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
