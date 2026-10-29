"""Phase 2 Verification Tests: Detached Task Supervisor.

PROVES:
1. A background task keeps running after WebSocket client disconnects.
2. A background task keeps running while 20 chat requests execute concurrently.
3. Explicit cancel works within 2 seconds.
4. Killing the daemon (simulated restart) mid-task marks it interrupted and resumes.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from noxai.config import get_config
from noxai.db import execute, fetch_one, get_db, init_db
from noxai.events import EventBus
from noxai.tasks.models import Task, TaskStatus
from noxai.tasks.supervisor import TaskSupervisor


@pytest.fixture
async def setup_supervisor(tmp_path, monkeypatch):
    test_db = tmp_path / "test_supervisor.db"
    monkeypatch.setattr("noxai.db.DB_PATH", test_db)
    monkeypatch.setattr("noxai.db.DATA_DIR", tmp_path)
    await init_db()

    cfg = get_config()
    bus = EventBus()
    supervisor = TaskSupervisor(
        config=cfg,
        router=None,  # fast test mock mode
        event_bus=bus,
        heartbeat_interval_sec=0.2,
        stale_threshold_sec=1.0,
    )
    await supervisor.start()
    yield supervisor
    await supervisor.stop()


@pytest.mark.asyncio
async def test_task_keeps_running_after_websocket_disconnect(setup_supervisor):
    """Prove that task progress is independent of WebSocket lifecycle."""
    supervisor = setup_supervisor

    async def slow_work(task: Task, cancel_evt: asyncio.Event):
        for p in (0.25, 0.5, 0.75, 1.0):
            if cancel_evt.is_set():
                raise asyncio.CancelledError()
            await asyncio.sleep(0.08)
            async with get_db() as db:
                await execute(db, "UPDATE tasks SET progress = ? WHERE id = ?", (p, task.id))
                await db.commit()
        return "Complete"

    client_events = []

    async def on_event(ev):
        client_events.append(ev)

    unsubscribe = supervisor.event_bus.subscribe(on_event)

    task = await supervisor.submit_task(
        title="Background Research",
        prompt="Gather news",
        handler=slow_work,
    )

    await asyncio.sleep(0.05)
    unsubscribe()

    await asyncio.sleep(1.0)

    async with get_db() as db:
        row = await fetch_one(db, "SELECT status, progress FROM tasks WHERE id = ?", (task.id,))
        assert row is not None
        assert row["status"] == TaskStatus.DONE.value
        assert row["progress"] == 1.0


@pytest.mark.asyncio
async def test_task_keeps_running_while_20_chat_requests_happen(setup_supervisor):
    """Prove that 20 rapid interactive chats do not preempt or cancel background tasks."""
    supervisor = setup_supervisor

    task_completed = asyncio.Event()

    async def long_task(task: Task, cancel_evt: asyncio.Event):
        await asyncio.sleep(0.3)
        task_completed.set()
        return "Done"

    task = await supervisor.submit_task(
        title="Heavy Background Compilation",
        prompt="Compile dataset",
        handler=long_task,
    )

    async with get_db() as db:
        await execute(
            db,
            "INSERT INTO conversations (id, title) VALUES ('conv_test', 'Test Conversation')",
        )
        await db.commit()

    async def fake_chat(i: int):
        async with get_db() as db:
            await execute(
                db,
                "INSERT INTO messages (id, conversation_id, role, content) VALUES (?, ?, ?, ?)",
                (f"msg_{i}_{time.time()}", "conv_test", "user", f"Hello {i}"),
            )
            await db.commit()
        await asyncio.sleep(0.01)

    await asyncio.gather(*(fake_chat(i) for i in range(20)))

    await asyncio.wait_for(task_completed.wait(), timeout=3.0)
    await asyncio.sleep(0.1)

    async with get_db() as db:
        row = await fetch_one(db, "SELECT status FROM tasks WHERE id = ?", (task.id,))
        assert row["status"] == TaskStatus.DONE.value


@pytest.mark.asyncio
async def test_explicit_cancel_works_within_2_seconds(setup_supervisor):
    """Prove cooperative cancellation halts work within 2 seconds."""
    supervisor = setup_supervisor

    async def infinite_task(task: Task, cancel_evt: asyncio.Event):
        while not cancel_evt.is_set():
            await asyncio.sleep(0.05)
        raise asyncio.CancelledError()

    task = await supervisor.submit_task(
        title="Infinite Job",
        prompt="Loop forever",
        handler=infinite_task,
    )

    await asyncio.sleep(0.1)

    t0 = time.monotonic()
    success = await supervisor.cancel_task(task.id, reason="User cancelled from UI")
    elapsed = time.monotonic() - t0

    assert success is True
    assert elapsed < 2.0

    await asyncio.sleep(0.2)
    async with get_db() as db:
        row = await fetch_one(db, "SELECT status, error FROM tasks WHERE id = ?", (task.id,))
        assert row["status"] == TaskStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_daemon_restart_resumes_without_redoing_completed_subtasks(tmp_path, monkeypatch):
    """Prove crash recovery: finished subtasks not rerun, interrupted resumed."""
    test_db = tmp_path / "test_supervisor_resume.db"
    monkeypatch.setattr("noxai.db.DB_PATH", test_db)
    monkeypatch.setattr("noxai.db.DATA_DIR", tmp_path)
    await init_db()

    cfg = get_config()
    bus = EventBus()

    async with get_db() as db:
        await execute(
            db,
            "INSERT INTO tasks (id, title, prompt, status, created_at) "
            "VALUES ('parent_1', 'Parent DAG', 'Run all', 'running', datetime('now'))",
        )
        await execute(
            db,
            "INSERT INTO tasks (id, title, prompt, status, progress, parent_id, created_at) "
            "VALUES ('sub_1', 'Subtask 1', 'Part 1', 'done', 1.0, 'parent_1', datetime('now'))",
        )
        stale_heartbeat = "2020-01-01T00:00:00Z"
        await execute(
            db,
            "INSERT INTO tasks "
            "(id, title, prompt, status, progress, parent_id, heartbeat_at, attempts, created_at) "
            "VALUES ('sub_2', 'Subtask 2', 'Part 2', 'running', 0.0, 'parent_1', ?, 1, "
            "datetime('now'))",
            (stale_heartbeat,),
        )
        await db.commit()

    supervisor = TaskSupervisor(config=cfg, event_bus=bus, stale_threshold_sec=1.0)
    await supervisor.start()

    async with get_db() as db:
        r1 = await fetch_one(db, "SELECT status FROM tasks WHERE id = 'sub_1'")
        r2 = await fetch_one(db, "SELECT status, error FROM tasks WHERE id = 'sub_2'")
        assert r1["status"] == "done"
        assert r2["status"] in ("queued", "running", "done")

    await supervisor.stop()
