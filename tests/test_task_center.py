"""Tests for Phase F5: Task Center (§1.4, §G3, M10).

Covers:
- Migration 006 (tasks table, indexes)
- TaskCenter CRUD, priority queueing, state transitions
- Cancellation tokens & background worker loop
- Approval parking and resolution
- REST API endpoints (/api/tasks, cancel, approval resolution)
- CLI commands (nx tasks list, show, cancel, create)
"""

from __future__ import annotations

import re
from collections.abc import Generator
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from noxai.api.app import create_app
from noxai.api.auth import generate_session_token, load_session_token
from noxai.cli import app as cli_app
from noxai.config import ModelRoleConfig, ModelsConfig, Settings
from noxai.db import fetch_all, get_db, init_db
from noxai.events import Event, EventBus
from noxai.models.base import ChatChunk
from noxai.models.fake import FakeProvider
from noxai.models.router import ModelRouter
from noxai.tasks.center import TaskCenter
from noxai.tasks.models import TaskLane, TaskPriority, TaskStatus
from noxai.tools.registry import ToolRegistry, record_pending_approval


@pytest.fixture(autouse=True)
def setup_isolated_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    data_dir = tmp_path / "share" / "nox"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "nox.db"
    token_file = data_dir / "session_token"

    import noxai.api.auth as auth_mod
    import noxai.db as db_mod

    monkeypatch.setattr(db_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(db_mod, "DB_PATH", db_file)
    monkeypatch.setattr(auth_mod, "DEFAULT_TOKEN_PATH", token_file)

    generate_session_token(token_file)
    yield


@pytest.fixture
def auth_headers() -> dict[str, str]:
    tok = load_session_token()
    assert tok is not None
    return {
        "Authorization": f"Bearer {tok}",
        "X-Nox-Client": "web",
    }


@pytest.fixture
def client(setup_isolated_env: None) -> Generator[TestClient, None, None]:
    app = create_app(start_scheduler=False)
    with TestClient(app) as tc:
        yield tc


@pytest.fixture
def sample_config() -> Settings:
    return Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="fake", model="fake-brain"),
                "embed": ModelRoleConfig(provider="fake", model="fake-embed"),
            }
        )
    )


@pytest.mark.asyncio
async def test_migration_006_schema() -> None:
    await init_db()
    async with get_db() as conn:
        cols = await fetch_all(conn, "PRAGMA table_info(tasks)")
        col_names = {r["name"] for r in cols}
        expected = {
            "id",
            "title",
            "prompt",
            "lane",
            "priority",
            "status",
            "created_at",
            "started_at",
            "finished_at",
            "error",
            "result_json",
            "progress",
            "approval_id",
            "cancellation_token",
            "run_id",
        }
        assert expected.issubset(col_names)


@pytest.mark.asyncio
async def test_task_crud_and_priority_ordering(sample_config: Settings) -> None:
    await init_db()
    bus = EventBus()
    tc = TaskCenter(config=sample_config, event_bus=bus)

    # Insert tasks out of priority order
    t_norm = await tc.create_task("Normal task", "do normal", priority=TaskPriority.NORMAL)
    t_low = await tc.create_task("Low task", "do low", priority=TaskPriority.LOW)
    t_urg = await tc.create_task("Urgent task", "do urgent", priority=TaskPriority.URGENT)
    t_high = await tc.create_task("High task", "do high", priority=TaskPriority.HIGH)

    # list_tasks should return ordered by priority: urgent, high, normal, low
    tasks = await tc.list_tasks()
    ids = [t.id for t in tasks]
    assert ids == [t_urg.id, t_high.id, t_norm.id, t_low.id]

    # Get single task
    fetched = await tc.get_task(t_urg.id)
    assert fetched is not None
    assert fetched.title == "Urgent task"
    assert fetched.priority == TaskPriority.URGENT
    assert fetched.status == TaskStatus.QUEUED


@pytest.mark.asyncio
async def test_task_filtering_status_and_lane(sample_config: Settings) -> None:
    await init_db()
    tc = TaskCenter(config=sample_config)

    t1 = await tc.create_task("Local 1", "p1", lane=TaskLane.LOCAL)
    t2 = await tc.create_task("Cloud 1", "p2", lane=TaskLane.CLOUD)

    local_tasks = await tc.list_tasks(lane=TaskLane.LOCAL)
    assert any(t.id == t1.id for t in local_tasks)
    assert not any(t.id == t2.id for t in local_tasks)

    cloud_tasks = await tc.list_tasks(lane=TaskLane.CLOUD)
    assert any(t.id == t2.id for t in cloud_tasks)
    assert not any(t.id == t1.id for t in cloud_tasks)


@pytest.mark.asyncio
async def test_task_execution_lifecycle_and_events(sample_config: Settings) -> None:
    await init_db()
    events_received: list[str] = []
    bus = EventBus()

    async def _on_event(evt: Event) -> None:
        events_received.append(evt.type)

    bus.subscribe(_on_event)

    fake = FakeProvider()
    fake.responses = [[ChatChunk(text="Processed successfully")]]
    router = ModelRouter(config=sample_config, providers={"fake": fake})
    reg = ToolRegistry()

    tc = TaskCenter(config=sample_config, router=router, registry=reg, event_bus=bus)

    task = await tc.create_task("Compute data", "Calculate metrics")
    assert task.status == TaskStatus.QUEUED
    assert "task_created" in events_received

    had_task = await tc.process_next_task()
    assert had_task is True

    done_task = await tc.get_task(task.id)
    assert done_task is not None
    assert done_task.status == TaskStatus.DONE
    assert done_task.progress == 1.0
    assert done_task.finished_at is not None
    assert done_task.result_json is not None
    assert "task_started" in events_received
    assert "task_done" in events_received


@pytest.mark.asyncio
async def test_task_cancellation_token(sample_config: Settings) -> None:
    await init_db()
    tc = TaskCenter(config=sample_config)

    t = await tc.create_task("Long task", "run long operation")
    ok = await tc.cancel_task(t.id)
    assert ok is True

    cancelled = await tc.get_task(t.id)
    assert cancelled is not None
    assert cancelled.status == TaskStatus.CANCELLED
    assert cancelled.finished_at is not None

    # Cannot cancel already cancelled task
    ok_second = await tc.cancel_task(t.id)
    assert ok_second is False


@pytest.mark.asyncio
async def test_task_approval_parking_and_resumption(sample_config: Settings) -> None:
    await init_db()
    tc = TaskCenter(config=sample_config)

    async with get_db() as db:
        appr1 = await record_pending_approval(
            db,
            tool_name="dispatch_task",
            args={"title": "Action 1"},
            reason="Requires owner approval",
        )
        appr2 = await record_pending_approval(
            db,
            tool_name="dispatch_task",
            args={"title": "Action 2"},
            reason="Requires owner approval",
        )

    t1 = await tc.create_task("Action with approval 1", "execute privileged op")
    t2 = await tc.create_task("Action with approval 2", "execute another privileged op")

    # Park both tasks waiting on approval
    await tc.park_for_approval(t1.id, approval_id=int(appr1))
    await tc.park_for_approval(t2.id, approval_id=int(appr2))

    parked1 = await tc.get_task(t1.id)
    assert parked1 is not None
    assert parked1.status == TaskStatus.WAITING_APPROVAL
    assert parked1.approval_id == int(appr1)

    # Case A: Approval approved -> resumes tasks to queued
    await tc.handle_approval_resolved(approval_id=int(appr1), approved=True)
    resumed1 = await tc.get_task(t1.id)
    assert resumed1 is not None
    assert resumed1.status == TaskStatus.QUEUED

    # Case B: Denied approval -> cancels parked tasks
    await tc.handle_approval_resolved(approval_id=int(appr2), approved=False)
    denied2 = await tc.get_task(t2.id)
    assert denied2 is not None
    assert denied2.status == TaskStatus.CANCELLED
    assert denied2.error is not None
    assert "denied" in denied2.error


def test_api_task_crud_and_cancel(client: TestClient, auth_headers: dict[str, str]) -> None:
    # 1. Create task
    resp = client.post(
        "/api/tasks",
        json={
            "title": "API Task",
            "prompt": "Run via API",
            "lane": "local",
            "priority": "high",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    task_id = data["id"]
    assert data["title"] == "API Task"
    assert data["priority"] == "high"
    assert data["status"] == "queued"

    # 2. Get task
    get_resp = client.get(f"/api/tasks/{task_id}", headers=auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == task_id

    # 3. List tasks
    list_resp = client.get("/api/tasks", headers=auth_headers)
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert any(item["id"] == task_id for item in items)

    # 4. Cancel task
    cancel_resp = client.post(f"/api/tasks/{task_id}/cancel", headers=auth_headers)
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["ok"] is True
    assert cancel_resp.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_api_approval_resolution_unparks_task(
    client: TestClient, auth_headers: dict[str, str], sample_config: Settings
) -> None:
    await init_db()
    tc = TaskCenter(config=sample_config)

    # Setup approval
    async with get_db() as db:
        appr_id = await record_pending_approval(
            db,
            tool_name="dispatch_task",
            args={"title": "Important privileged task"},
            reason="Requires manual review",
        )

    task = await tc.create_task("Privileged task", "perform action requiring approval")
    await tc.park_for_approval(task.id, approval_id=int(appr_id))

    parked = await tc.get_task(task.id)
    assert parked is not None
    assert parked.status == TaskStatus.WAITING_APPROVAL

    # Approve via REST API
    resp = client.post(
        f"/api/approvals/{appr_id}",
        json={"decision": "approve"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Verify task unparked to queued
    resumed = await tc.get_task(task.id)
    assert resumed is not None
    assert resumed.status == TaskStatus.QUEUED


def test_cli_tasks_commands() -> None:
    runner = CliRunner()

    # Create task via CLI
    create_res = runner.invoke(
        cli_app,
        ["tasks", "create", "CLI Task", "Work hard", "--priority", "urgent"],
    )
    assert create_res.exit_code == 0
    assert "Created task" in create_res.stdout
    m = re.search(r"(task_[a-f0-9]+)", create_res.stdout)
    assert m is not None
    task_id = m.group(1)

    # List tasks via CLI
    list_res = runner.invoke(cli_app, ["tasks", "list"])
    assert list_res.exit_code == 0
    assert "CLI Task" in list_res.stdout

    # Show task details
    show_res = runner.invoke(cli_app, ["tasks", "show", task_id])
    assert show_res.exit_code == 0
    assert "CLI Task" in show_res.stdout

    # Cancel task
    cancel_res = runner.invoke(cli_app, ["tasks", "cancel", task_id])
    assert cancel_res.exit_code == 0
    assert "cancelled" in cancel_res.stdout
