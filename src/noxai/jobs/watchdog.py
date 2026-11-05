"""Watchdog & Orphaned Run Reconciliation for Planned Jobs (Section 4.3)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite

from noxai.config import Settings, get_config
from noxai.db import execute, fetch_all, get_db

log = logging.getLogger(__name__)


async def repair_orphaned_runs(
    db: aiosqlite.Connection | None = None,
    dry_run: bool = False,
    config: Settings | None = None,
) -> list[dict[str, Any]]:
    """Reconcile orphaned or stalled job runs (nx jobs repair and startup watchdog).

    Finds job_runs with status='running' that have no recent heartbeat (> 3 min)
    and transitions them to 'stalled'. If dry_run=True, returns candidates without modifying DB.
    """
    cfg = config or get_config()
    stale_sec = cfg.jobs.stale_heartbeat_seconds
    now_utc = datetime.now(UTC)
    cutoff = now_utc - timedelta(seconds=stale_sec)

    reconciled: list[dict[str, Any]] = []

    if db is not None:
        return await _do_repair(db, cutoff, dry_run, reconciled)

    async with get_db() as local_db:
        return await _do_repair(local_db, cutoff, dry_run, reconciled)


async def _do_repair(
    db: aiosqlite.Connection,
    cutoff: datetime,
    dry_run: bool,
    reconciled: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    # Find all runs stuck in 'running' state
    rows = await fetch_all(
        db,
        """
        SELECT id, job_id, started_at, heartbeat_at, status
        FROM job_runs
        WHERE status = 'running'
        ORDER BY started_at ASC
        """,
    )

    for r in rows:
        run_id = r[0]
        job_id = r[1]
        started_at = r[2]
        heartbeat_at = r[3]

        is_stale = False
        if not heartbeat_at:
            is_stale = True
        else:
            try:
                # Clean up iso or space formats
                clean_hb = heartbeat_at.replace("T", " ").split(".")[0].split("+")[0]
                dt_hb = datetime.strptime(clean_hb, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
                if dt_hb < cutoff:
                    is_stale = True
            except Exception:
                is_stale = True

        if is_stale:
            item = {
                "run_id": run_id,
                "job_id": job_id,
                "started_at": started_at,
                "heartbeat_at": heartbeat_at,
                "previous_status": "running",
                "action": "transition_to_stalled",
            }
            reconciled.append(item)

            if not dry_run:
                await execute(
                    db,
                    """
                    UPDATE job_runs
                    SET status = 'stalled',
                        finished_at = datetime('now'),
                        error = 'Stalled: heartbeat timed out after 3 minutes with no activity'
                    WHERE id = ?
                    """,
                    (run_id,),
                )
                log.warning(
                    "Watchdog reconciled orphaned run %s (job %s) to 'stalled'", run_id, job_id
                )

    if not dry_run and reconciled:
        await db.commit()

    return reconciled
