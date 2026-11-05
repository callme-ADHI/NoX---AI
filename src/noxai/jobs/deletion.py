"""Deletion controls, soft-delete, cooperative cancellation, and purge (Section 7)."""

from __future__ import annotations

import logging
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite

from noxai.config import Settings, get_config
from noxai.db import execute, fetch_all, fetch_one, get_db

log = logging.getLogger(__name__)


async def delete_job(
    job_id: str,
    purge: bool = False,
    delete_history: bool = True,
    db: aiosqlite.Connection | None = None,
) -> dict[str, Any]:
    """Delete a job with cooperative cancellation and soft-delete/purge semantics."""
    async with get_db() as local_db:
        conn = db or local_db

        row = await fetch_one(conn, "SELECT id, name FROM jobs WHERE id = ? OR name = ?", (job_id, job_id))
        if not row:
            raise ValueError(f"Job '{job_id}' not found.")
        actual_id = row[0]
        actual_name = row[1]

        # 1. Cancel running executions
        running_runs = await fetch_all(
            conn,
            "SELECT id FROM job_runs WHERE job_id = ? AND status = 'running'",
            (actual_id,),
        )
        for r in running_runs:
            await execute(
                conn,
                "UPDATE job_runs SET status = 'cancelled', finished_at = datetime('now'), error = 'Cancelled via job deletion' WHERE id = ?",
                (r[0],),
            )

        # 2. Purge or Soft-delete
        if purge:
            if delete_history:
                await execute(conn, "DELETE FROM job_runs WHERE job_id = ?", (actual_id,))
            await execute(conn, "DELETE FROM job_plans WHERE job_id = ?", (actual_id,))
            await execute(conn, "DELETE FROM jobs WHERE id = ?", (actual_id,))
            action = "purged"
        else:
            await execute(
                conn,
                "UPDATE jobs SET deleted_at = datetime('now'), enabled = 0, updated_at = datetime('now') WHERE id = ?",
                (actual_id,),
            )
            action = "soft_deleted"

        await conn.commit()
        return {
            "ok": True,
            "id": actual_id,
            "name": actual_name,
            "action": action,
            "running_runs_cancelled": len(running_runs),
        }


async def restore_job(job_id: str, db: aiosqlite.Connection | None = None) -> dict[str, Any]:
    """Restore a soft-deleted job."""
    async with get_db() as local_db:
        conn = db or local_db
        row = await fetch_one(conn, "SELECT id, name FROM jobs WHERE id = ? OR name = ?", (job_id, job_id))
        if not row:
            raise ValueError(f"Job '{job_id}' not found.")
        actual_id = row[0]

        await execute(conn, "UPDATE jobs SET deleted_at = NULL, enabled = 1, updated_at = datetime('now') WHERE id = ?", (actual_id,))
        await conn.commit()
        return {"ok": True, "id": actual_id, "name": row[1], "action": "restored"}


async def delete_task(
    task_id: str,
    purge: bool = False,
    db: aiosqlite.Connection | None = None,
) -> dict[str, Any]:
    """Delete a task, cancelling running task first."""
    async with get_db() as local_db:
        conn = db or local_db
        row = await fetch_one(conn, "SELECT id, title, status FROM tasks WHERE id = ?", (task_id,))
        if not row:
            raise ValueError(f"Task '{task_id}' not found.")
        actual_id = row[0]
        title = row[1]
        cur_status = row[2]

        # Cooperative cancellation if running
        if cur_status in ("running", "queued", "waiting_approval"):
            from noxai.tasks.supervisor import get_task_supervisor
            sup = get_task_supervisor()
            if sup:
                await sup.cancel_task(actual_id)
            await execute(
                conn,
                "UPDATE tasks SET status = 'cancelled', finished_at = datetime('now'), error = 'Cancelled via task deletion' WHERE id = ?",
                (actual_id,),
            )

        if purge:
            await execute(conn, "DELETE FROM tasks WHERE id = ?", (actual_id,))
            action = "purged"
        else:
            await execute(conn, "UPDATE tasks SET deleted_at = datetime('now') WHERE id = ?", (actual_id,))
            action = "soft_deleted"

        await conn.commit()
        return {"ok": True, "id": actual_id, "title": title, "action": action}


async def clear_finished_tasks(db: aiosqlite.Connection | None = None) -> dict[str, Any]:
    """Soft-delete all finished tasks (done, failed, cancelled, interrupted)."""
    async with get_db() as local_db:
        conn = db or local_db
        cursor = await execute(
            conn,
            """
            UPDATE tasks
            SET deleted_at = datetime('now')
            WHERE status IN ('done', 'failed', 'cancelled', 'interrupted')
              AND deleted_at IS NULL
            """,
        )
        count = cursor.rowcount
        await conn.commit()
        return {"ok": True, "cleared": count}



async def delete_briefing(
    briefing_id: str,
    purge: bool = False,
    db: aiosqlite.Connection | None = None,
    config: Settings | None = None,
) -> dict[str, Any]:
    """Delete a briefing run and safely manage associated vault notes."""
    cfg = config or get_config()
    async with get_db() as local_db:
        conn = db or local_db
        row = await fetch_one(
            conn,
            "SELECT id, date, slot, title, markdown_path FROM briefing_runs WHERE id = ?",
            (briefing_id,),
        )
        if not row:
            raise ValueError(f"Briefing '{briefing_id}' not found.")
        actual_id = row[0]
        b_date = row[1]
        b_slot = row[2]
        title = row[3]
        md_path = row[4]

        # 1. Handle markdown report
        if md_path and os.path.exists(md_path):
            try:
                os.remove(md_path)
            except OSError:
                pass

        # 2. Vault notes handling: check if user edited
        vault_root = Path(os.path.expanduser(cfg.notes.vault_path))
        notes_kept: list[str] = []
        notes_trashed: list[str] = []

        if vault_root.exists():
            trash_dir = vault_root / ".trash"
            trash_dir.mkdir(parents=True, exist_ok=True)
            # Find notes tagged with this briefing date/slot
            for note_file in vault_root.glob("*.md"):
                if note_file.name.startswith("."):
                    continue
                try:
                    content = note_file.read_text(encoding="utf-8")
                    if f"date: {b_date}" in content and f"slot: {b_slot}" in content:
                        # Check user modifications outside nox markers
                        # Unedited note has only nox frontmatter/body
                        has_user_edits = False
                        lines = content.splitlines()
                        for line in lines:
                            if line.startswith("# Notes:") or line.startswith("## User Notes"):
                                has_user_edits = True
                                break

                        if has_user_edits:
                            notes_kept.append(note_file.name)
                        else:
                            shutil.move(str(note_file), str(trash_dir / note_file.name))
                            notes_trashed.append(note_file.name)
                except Exception:
                    pass

        # 3. Purge or Soft-delete
        if purge:
            await execute(conn, "DELETE FROM briefing_items WHERE briefing_run_id = ?", (actual_id,))
            await execute(conn, "DELETE FROM briefing_runs WHERE id = ?", (actual_id,))
            action = "purged"
        else:
            await execute(conn, "UPDATE briefing_runs SET deleted_at = datetime('now') WHERE id = ?", (actual_id,))
            action = "soft_deleted"

        await conn.commit()
        return {
            "ok": True,
            "id": actual_id,
            "title": title,
            "action": action,
            "notes_trashed": notes_trashed,
            "notes_kept": notes_kept,
        }


async def restore_task(task_id: str, db: aiosqlite.Connection | None = None) -> dict[str, Any]:
    """Restore a soft-deleted task."""
    async with get_db() as local_db:
        conn = db or local_db
        row = await fetch_one(conn, "SELECT id, title FROM tasks WHERE id = ?", (task_id,))
        if not row:
            raise ValueError(f"Task '{task_id}' not found.")
        actual_id = row[0]

        await execute(conn, "UPDATE tasks SET deleted_at = NULL WHERE id = ?", (actual_id,))
        await conn.commit()
        return {"ok": True, "id": actual_id, "title": row[1], "action": "restored"}


async def restore_briefing(briefing_id: str, db: aiosqlite.Connection | None = None) -> dict[str, Any]:
    """Restore a soft-deleted briefing."""
    async with get_db() as local_db:
        conn = db or local_db
        row = await fetch_one(conn, "SELECT id, date, slot FROM briefing_runs WHERE id = ? OR (date || '-' || slot) = ?", (briefing_id, briefing_id))
        if not row:
            raise ValueError(f"Briefing '{briefing_id}' not found.")
        actual_id = row[0]

        await execute(conn, "UPDATE briefing_runs SET deleted_at = NULL WHERE id = ?", (actual_id,))
        await conn.commit()
        return {"ok": True, "id": actual_id, "action": "restored"}


async def purge_expired(
    max_age_days: int = 30,
    force_all: bool = False,
    db: aiosqlite.Connection | None = None,
) -> dict[str, int]:
    """Purge soft-deleted items older than max_age_days (or all if force_all=True)."""
    async with get_db() as local_db:
        conn = db or local_db
        if force_all:
            cutoff_sql = "deleted_at IS NOT NULL"
            params: tuple[Any, ...] = ()
        else:
            cutoff = (datetime.now(UTC) - timedelta(days=max_age_days)).strftime("%Y-%m-%d %H:%M:%S")
            cutoff_sql = "deleted_at IS NOT NULL AND deleted_at <= ?"
            params = (cutoff,)

        c1 = await execute(conn, f"DELETE FROM jobs WHERE {cutoff_sql}", params)  # noqa: S608
        purged_jobs = c1.rowcount

        c2 = await execute(conn, f"DELETE FROM tasks WHERE {cutoff_sql}", params)  # noqa: S608
        purged_tasks = c2.rowcount

        c3 = await execute(conn, f"DELETE FROM briefing_runs WHERE {cutoff_sql}", params)  # noqa: S608
        purged_briefings = c3.rowcount

        await conn.commit()
        return {
            "purged_jobs": purged_jobs,
            "purged_tasks": purged_tasks,
            "purged_briefings": purged_briefings,
        }

