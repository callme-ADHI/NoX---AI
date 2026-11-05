"""Unit tests for Planned Jobs Deletion Engine (Section 6).

Verifies soft delete, restore, purge, clear-finished, and notes trashing behavior.
"""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from noxai.db import execute, fetch_all, fetch_one
from noxai.jobs.deletion import (
    clear_finished_tasks,
    delete_job,
    delete_task,
    purge_expired,
    restore_job,
    restore_task,
)


@pytest.fixture
async def mem_db(tmp_path: Path):
    db_file = tmp_path / "test_deletion.db"
    async with aiosqlite.connect(str(db_file)) as conn:
        conn.row_factory = aiosqlite.Row
        # Run migration 012 schema
        mig_sql = Path("src/noxai/migrations/012_planned_jobs_and_delete_controls.sql").read_text()
        # Also need base tables if not present in migration 012
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                instruction TEXT NOT NULL,
                schedule_type TEXT NOT NULL DEFAULT 'daily',
                schedule_expr TEXT NOT NULL DEFAULT '20:00',
                timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata',
                enabled INTEGER NOT NULL DEFAULT 1,
                config_json TEXT,
                deleted_at TEXT,
                last_run_at TEXT,
                next_run_at TEXT,
                version INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                prompt TEXT NOT NULL,
                lane TEXT NOT NULL DEFAULT 'local',
                priority TEXT NOT NULL DEFAULT 'normal',
                status TEXT NOT NULL DEFAULT 'queued',
                progress REAL NOT NULL DEFAULT 0.0,
                created_at TEXT DEFAULT (datetime('now')),
                started_at TEXT,
                finished_at TEXT,
                approval_id TEXT,
                error TEXT,
                result_json TEXT,
                cancellation_token TEXT,
                run_id TEXT,
                parent_task_id TEXT,
                subtask_ids_json TEXT,
                retry_count INTEGER DEFAULT 0,
                max_retries INTEGER DEFAULT 0,
                heartbeat_at TEXT,
                deleted_at TEXT
            );
            CREATE TABLE IF NOT EXISTS briefing_runs (
                id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                slot TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'queued',
                trigger TEXT NOT NULL DEFAULT 'scheduled',
                rev INTEGER NOT NULL DEFAULT 1,
                title TEXT,
                summary TEXT,
                markdown_path TEXT,
                generation_seconds REAL,
                sources_count INTEGER,
                task_id TEXT,
                error TEXT,
                failure_reason TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                finished_at TEXT,
                deleted_at TEXT
            );
            CREATE TABLE IF NOT EXISTS briefing_items (
                id TEXT PRIMARY KEY,
                briefing_run_id TEXT NOT NULL REFERENCES briefing_runs(id),
                title TEXT,
                topic_id TEXT,
                category TEXT
            );
            CREATE TABLE IF NOT EXISTS job_runs (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(id),
                started_at TEXT NOT NULL DEFAULT (datetime('now')),
                finished_at TEXT,
                status TEXT NOT NULL,
                output_markdown TEXT,
                error TEXT,
                heartbeat_at TEXT,
                current_step_index INTEGER,
                total_steps INTEGER,
                due_slot TEXT,
                plan_version INTEGER
            );
            CREATE TABLE IF NOT EXISTS job_plans (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(id),
                version INTEGER NOT NULL,
                steps_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'draft',
                source TEXT NOT NULL,
                approved_at TEXT,
                approved_by TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(job_id, version)
            );
        """)
        await conn.commit()

    import os

    orig_env = os.environ.get("NOX_TEST_DB")
    os.environ["NOX_TEST_DB"] = str(db_file)
    try:
        yield str(db_file)
    finally:
        if orig_env:
            os.environ["NOX_TEST_DB"] = orig_env
        else:
            os.environ.pop("NOX_TEST_DB", None)


@pytest.mark.asyncio
async def test_job_soft_delete_and_restore(mem_db):
    async with aiosqlite.connect(mem_db) as conn:
        conn.row_factory = aiosqlite.Row
        await execute(
            conn,
            "INSERT INTO jobs (id, name, instruction, enabled) VALUES ('job_1', 'TestJob', 'echo test', 1)",
        )
        await execute(
            conn,
            "INSERT INTO job_runs (id, job_id, status) VALUES ('jrun_1', 'job_1', 'running')",
        )
        await conn.commit()

        # 1. Soft delete
        res = await delete_job("job_1", purge=False, db=conn)
        assert res["action"] == "soft_deleted"
        assert res["running_runs_cancelled"] == 1

        row = await fetch_one(conn, "SELECT deleted_at, enabled FROM jobs WHERE id = 'job_1'")
        assert row["deleted_at"] is not None
        assert row["enabled"] == 0

        # Run was cancelled
        run_row = await fetch_one(conn, "SELECT status FROM job_runs WHERE id = 'jrun_1'")
        assert run_row["status"] == "cancelled"

        # 2. Restore
        rest_res = await restore_job("job_1", db=conn)
        assert rest_res["action"] == "restored"
        rest_row = await fetch_one(conn, "SELECT deleted_at, enabled FROM jobs WHERE id = 'job_1'")
        assert rest_row["deleted_at"] is None
        assert rest_row["enabled"] == 1


@pytest.mark.asyncio
async def test_job_purge(mem_db):
    async with aiosqlite.connect(mem_db) as conn:
        conn.row_factory = aiosqlite.Row
        await execute(
            conn,
            "INSERT INTO jobs (id, name, instruction) VALUES ('job_2', 'PurgeJob', 'do something')",
        )
        await execute(
            conn,
            "INSERT INTO job_plans (id, job_id, version, steps_json, source) VALUES ('p_1', 'job_2', 1, '[]', 'template')",
        )
        await conn.commit()

        res = await delete_job("job_2", purge=True, db=conn)
        assert res["action"] == "purged"

        row = await fetch_one(conn, "SELECT id FROM jobs WHERE id = 'job_2'")
        assert row is None
        plan_row = await fetch_one(conn, "SELECT id FROM job_plans WHERE job_id = 'job_2'")
        assert plan_row is None


@pytest.mark.asyncio
async def test_task_soft_delete_and_clear_finished(mem_db):
    async with aiosqlite.connect(mem_db) as conn:
        conn.row_factory = aiosqlite.Row
        await execute(
            conn,
            "INSERT INTO tasks (id, title, prompt, status) VALUES ('task_1', 'T1', 'p1', 'done')",
        )
        await execute(
            conn,
            "INSERT INTO tasks (id, title, prompt, status) VALUES ('task_2', 'T2', 'p2', 'running')",
        )
        await execute(
            conn,
            "INSERT INTO tasks (id, title, prompt, status) VALUES ('task_3', 'T3', 'p3', 'failed')",
        )
        await conn.commit()

        # Soft delete task_2
        del_res = await delete_task("task_2", purge=False, db=conn)
        assert del_res["action"] == "soft_deleted"
        row = await fetch_one(conn, "SELECT deleted_at, status FROM tasks WHERE id = 'task_2'")
        assert row["deleted_at"] is not None
        assert row["status"] == "cancelled"

        # Restore task_2
        res_res = await restore_task("task_2", db=conn)
        assert res_res["action"] == "restored"
        row = await fetch_one(conn, "SELECT deleted_at FROM tasks WHERE id = 'task_2'")
        assert row["deleted_at"] is None

        # Insert a running task_4
        await execute(
            conn,
            "INSERT INTO tasks (id, title, prompt, status) VALUES ('task_4', 'T4', 'p4', 'running')",
        )
        await conn.commit()

        # Clear finished tasks (task_1, task_2 [now cancelled], task_3)
        clear_res = await clear_finished_tasks(db=conn)
        assert clear_res["cleared"] == 3  # task_1, task_2, task_3

        remaining = await fetch_all(conn, "SELECT id FROM tasks WHERE deleted_at IS NULL")
        assert len(remaining) == 1
        assert remaining[0]["id"] == "task_4"


@pytest.mark.asyncio
async def test_purge_expired_records(mem_db):
    async with aiosqlite.connect(mem_db) as conn:
        conn.row_factory = aiosqlite.Row
        # Insert soft-deleted records: one old (35 days ago), one fresh (1 day ago)
        await execute(
            conn,
            "INSERT INTO jobs (id, name, instruction, deleted_at) VALUES ('j_old', 'Old', 'i', datetime('now', '-35 days'))",
        )
        await execute(
            conn,
            "INSERT INTO jobs (id, name, instruction, deleted_at) VALUES ('j_fresh', 'Fresh', 'i', datetime('now', '-1 day'))",
        )
        await execute(
            conn,
            "INSERT INTO tasks (id, title, prompt, deleted_at) VALUES ('t_old', 'Old', 'p', datetime('now', '-40 days'))",
        )
        await conn.commit()

        # Default purge (30 days)
        res = await purge_expired(max_age_days=30, force_all=False, db=conn)
        assert res["purged_jobs"] == 1
        assert res["purged_tasks"] == 1

        # Fresh job still exists
        fresh = await fetch_one(conn, "SELECT id FROM jobs WHERE id = 'j_fresh'")
        assert fresh is not None

        # Force all purge
        res_all = await purge_expired(force_all=True, db=conn)
        assert res_all["purged_jobs"] == 1
        fresh_after = await fetch_one(conn, "SELECT id FROM jobs WHERE id = 'j_fresh'")
        assert fresh_after is None
