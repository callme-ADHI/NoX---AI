"""Tests for TaskSupervisor crash recovery (SIGKILL / daemon restart) and TaskHandlerRegistry."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiosqlite
import pytest

from noxai.config import Settings, get_config
from noxai.db import execute, fetch_one, init_db
from noxai.tasks.models import Task
from noxai.tasks.supervisor import (
    TaskHandlerRegistry,
    TaskSupervisor,
)


@pytest.fixture
async def test_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[aiosqlite.Connection]:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "nox.db"

    import noxai.db as db_mod
    monkeypatch.setattr(db_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(db_mod, "DB_PATH", db_file)

    await init_db()

    conn = await aiosqlite.connect(str(db_file))
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON;")
    yield conn
    await conn.close()


@pytest.fixture
def test_config() -> Settings:
    return get_config()


@pytest.mark.asyncio
async def test_supervisor_recovers_interrupted_and_stale_tasks(
    test_db: aiosqlite.Connection, test_config: Settings
):
    """Test recover_interrupted_tasks catches both status='interrupted' and status='running' with stale heartbeat."""
    now_utc = datetime.now(UTC)
    stale_iso = (now_utc - timedelta(seconds=60)).isoformat()
    recent_iso = (now_utc - timedelta(seconds=1)).isoformat()

    # 1. Insert an 'interrupted' task
    await execute(
        test_db,
        """
        INSERT INTO tasks (id, title, prompt, lane, priority, status, kind, max_attempts, attempts, created_at)
        VALUES ('task_interrupted_1', 'Interrupted Task', 'Resume me', 'local', 'normal', 'interrupted', 'custom_kind', 3, 0, ?)
        """,
        (stale_iso,),
    )

    # 2. Insert a 'running' task with stale heartbeat (crashed daemon)
    await execute(
        test_db,
        """
        INSERT INTO tasks (id, title, prompt, lane, priority, status, kind, max_attempts, attempts, created_at, started_at, heartbeat_at)
        VALUES ('task_stale_running_1', 'Stale Running Task', 'Resume me too', 'local', 'high', 'running', 'custom_kind', 3, 1, ?, ?, ?)
        """,
        (stale_iso, stale_iso, stale_iso),
    )

    # 3. Insert an 'interrupted' task that has already exhausted max_attempts
    await execute(
        test_db,
        """
        INSERT INTO tasks (id, title, prompt, lane, priority, status, kind, max_attempts, attempts, created_at)
        VALUES ('task_exhausted_1', 'Exhausted Task', 'Do not resume', 'local', 'normal', 'interrupted', 'custom_kind', 3, 3, ?)
        """,
        (stale_iso,),
    )

    # 4. Insert an active running task with fresh heartbeat (should NOT be recovered)
    await execute(
        test_db,
        """
        INSERT INTO tasks (id, title, prompt, lane, priority, status, kind, max_attempts, attempts, created_at, started_at, heartbeat_at)
        VALUES ('task_fresh_running_1', 'Fresh Running Task', 'Leave me running', 'local', 'normal', 'running', 'custom_kind', 3, 1, ?, ?, ?)
        """,
        (recent_iso, recent_iso, recent_iso),
    )
    await test_db.commit()

    supervisor = TaskSupervisor(config=test_config, stale_threshold_sec=10.0)
    recovered_count = await supervisor.recover_interrupted_tasks(test_db)

    # Should recover 3 tasks: 2 to queued, 1 to failed
    assert recovered_count == 3

    r1 = await fetch_one(test_db, "SELECT status, error FROM tasks WHERE id = 'task_interrupted_1'")
    assert r1["status"] == "queued"
    assert "resumed after daemon restart" in r1["error"]

    r2 = await fetch_one(test_db, "SELECT status, error FROM tasks WHERE id = 'task_stale_running_1'")
    assert r2["status"] == "queued"

    r3 = await fetch_one(test_db, "SELECT status, error FROM tasks WHERE id = 'task_exhausted_1'")
    assert r3["status"] == "failed"
    assert "exceeded max retry attempts" in r3["error"]

    r4 = await fetch_one(test_db, "SELECT status FROM tasks WHERE id = 'task_fresh_running_1'")
    assert r4["status"] == "running"


@pytest.mark.asyncio
async def test_task_handler_registry_across_restart(
    test_db: aiosqlite.Connection, test_config: Settings
):
    """Test that a task handler registered by task kind executes successfully on recovered tasks."""
    executed_tasks = []

    async def custom_kind_handler(task: Task, cancel_evt: asyncio.Event) -> dict[str, Any]:
        executed_tasks.append(task.id)
        return {"result": f"processed_{task.id}"}

    registry = TaskHandlerRegistry()
    registry.register("data_pipeline", custom_kind_handler)

    # Simulate crashed task in DB
    now_iso = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    await execute(
        test_db,
        """
        INSERT INTO tasks (id, title, prompt, lane, priority, status, kind, max_attempts, attempts, created_at)
        VALUES ('task_pipe_99', 'Pipeline Job', 'run pipeline', 'local', 'high', 'interrupted', 'data_pipeline', 3, 0, ?)
        """,
        (now_iso,),
    )
    await test_db.commit()

    # Simulate daemon boot: create supervisor with registry
    sup = TaskSupervisor(config=test_config, task_registry=registry)
    await sup.recover_interrupted_tasks(test_db)

    # Verify task queued
    row = await fetch_one(test_db, "SELECT status FROM tasks WHERE id = 'task_pipe_99'")
    assert row["status"] == "queued"

    # Dispatch via supervisor
    dispatched = await sup._dispatch_next_task()
    assert dispatched is True

    # Wait for the async task to finish
    await asyncio.sleep(0.1)

    assert "task_pipe_99" in executed_tasks

    row_done = await fetch_one(test_db, "SELECT status, result_json FROM tasks WHERE id = 'task_pipe_99'")
    assert row_done["status"] == "done"
    assert "processed_task_pipe_99" in row_done["result_json"]
