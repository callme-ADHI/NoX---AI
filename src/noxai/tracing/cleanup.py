# Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
# commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
# Modified for NoX: 90-day retention pruning and 100,000-row volume capping for SQLite traces.

"""Trace retention pruning and capacity capping (Workstream C).

Rules:
- Keep traces for 90 days by default.
- Delete older rows daily via the persistent scheduler.
- Cap total rows in the traces table at 100,000.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging

import aiosqlite

from noxai.db import execute, fetch_all, fetch_one, get_db

log = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 90
DEFAULT_MAX_ROWS = 100_000


async def cleanup_old_traces(
    db: aiosqlite.Connection | None = None,
    max_days: int = DEFAULT_RETENTION_DAYS,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> dict[str, int]:
    """Delete traces older than max_days and cap total rows at max_rows."""
    cutoff_dt = datetime.now(UTC) - timedelta(days=max_days)
    cutoff_iso = cutoff_dt.isoformat()

    deleted_expired = 0
    deleted_capped = 0

    async def _run(conn: aiosqlite.Connection) -> dict[str, int]:
        nonlocal deleted_expired, deleted_capped

        # 1. Delete rows older than cutoff
        res = await execute(
            conn,
            "DELETE FROM traces WHERE started_at < ?",
            (cutoff_iso,),
        )
        deleted_expired = res.rowcount if hasattr(res, "rowcount") else 0

        # 2. Check total count
        count_row = await fetch_one(conn, "SELECT COUNT(*) as cnt FROM traces")
        current_cnt = int(count_row["cnt"]) if count_row else 0

        # 3. If exceeding max_rows, prune oldest
        if current_cnt > max_rows:
            excess = current_cnt - max_rows
            del_res = await execute(
                conn,
                """
                DELETE FROM traces
                WHERE id IN (
                    SELECT id FROM traces
                    ORDER BY started_at ASC
                    LIMIT ?
                )
                """,
                (excess,),
            )
            deleted_capped = del_res.rowcount if hasattr(del_res, "rowcount") else excess

        await conn.commit()

        final_cnt_row = await fetch_one(conn, "SELECT COUNT(*) as cnt FROM traces")
        remaining = int(final_cnt_row["cnt"]) if final_cnt_row else 0

        log.info(
            "Trace cleanup complete: deleted_expired=%d, deleted_capped=%d, remaining=%d",
            deleted_expired,
            deleted_capped,
            remaining,
        )
        return {
            "deleted_expired": deleted_expired,
            "deleted_capped": deleted_capped,
            "remaining": remaining,
        }

    if db is not None:
        return await _run(db)
    else:
        async with get_db() as conn:
            return await _run(conn)
