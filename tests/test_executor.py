"""Tests for DAGExecutor, topological concurrency, SQLite persistence, and resumption
(Section 6.4, 6.6).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from noxai.agent.executor import DAGExecutor
from noxai.agent.planner import Plan, SubtaskNode
from noxai.agent.resume import mark_interrupted_runs, resume_run
from noxai.agent.worker import WorkerOutput, WorkerSource
from noxai.config import AgentConfig, ModelRoleConfig, ModelsConfig, Settings
from noxai.db import fetch_all, fetch_one
from noxai.events import EventBus
from noxai.models.fake import FakeProvider
from noxai.models.router import ModelRouter
from noxai.tools.registry import ToolRegistry


@pytest.fixture
def test_config() -> Settings:
    return Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="fake", model="fake-brain"),
                "heavy": ModelRoleConfig(provider="fake", model="fake-heavy"),
                "embed": ModelRoleConfig(provider="fake", model="fake-embed"),
            }
        ),
        agent=AgentConfig(
            worker_concurrency=2,
            max_subtasks=5,
            max_depth=3,
        ),
    )


@pytest.fixture
def fake_router(test_config: Settings) -> tuple[ModelRouter, FakeProvider]:
    fake = FakeProvider()
    router = ModelRouter(config=test_config, providers={"fake": fake})
    return router, fake


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
async def migrated_db(tmp_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    """Create a temporary SQLite DB with migrations applied."""
    db_file = tmp_path / "nox_test.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA foreign_keys = ON;")
    with open("src/noxai/migrations/001_initial.sql") as f:
        conn.executescript(f.read())
    with open("src/noxai/migrations/002_spec_alignment.sql") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()

    async_conn = await aiosqlite.connect(str(db_file))
    async_conn.row_factory = aiosqlite.Row
    yield async_conn
    await async_conn.close()


def _valid_worker_json(summary: str, conf: float = 0.9) -> str:
    return json.dumps(
        {
            "summary": summary,
            "key_points": [f"Key: {summary}"],
            "sources": [{"title": "Doc", "url": "https://example.com/doc"}],
            "confidence": conf,
            "gaps": [],
        }
    )


@pytest.mark.asyncio
async def test_executor_topological_concurrency(
    fake_router: tuple[ModelRouter, FakeProvider],
    registry: ToolRegistry,
    test_config: Settings,
    event_bus: EventBus,
    migrated_db: aiosqlite.Connection,
) -> None:
    """Independent nodes execute and downstream nodes execute after prerequisites finish."""
    router, fake = fake_router

    # Nodes: t1, t2 independent; t3 depends on [t1, t2]
    # Responses queued:
    # 1. worker t1
    # 2. worker t2
    # 3. worker t3
    # 4. synthesizer
    fake.queue_response(_valid_worker_json("Findings from task 1"))
    fake.queue_response(_valid_worker_json("Findings from task 2"))
    fake.queue_response(_valid_worker_json("Findings from task 3"))
    fake.queue_response("# Synthesized Final Report\n\nAll tasks completed.")

    plan = Plan(
        goal="Test full concurrency DAG",
        subtasks=[
            SubtaskNode(
                id="t1",
                title="Task 1",
                objective="Obj 1",
                kind="research",
                expected_output="Output 1",
                depends_on=[],
            ),
            SubtaskNode(
                id="t2",
                title="Task 2",
                objective="Obj 2",
                kind="research",
                expected_output="Output 2",
                depends_on=[],
            ),
            SubtaskNode(
                id="t3",
                title="Task 3",
                objective="Obj 3",
                kind="compute",
                expected_output="Output 3",
                depends_on=["t1", "t2"],
            ),
        ],
        synthesis_instructions="Merge all findings.",
    )

    executor = DAGExecutor(
        router=router, registry=registry, config=test_config, event_bus=event_bus
    )
    result = await executor.execute_plan(
        goal=plan.goal,
        existing_plan=plan,
        db=migrated_db,
    )

    assert result.status == "done"
    assert len(result.node_outputs) == 3
    assert "t1" in result.node_outputs
    assert "t2" in result.node_outputs
    assert "t3" in result.node_outputs
    assert "Synthesized Final Report" in result.final_text


@pytest.mark.asyncio
async def test_executor_dependency_gating(
    fake_router: tuple[ModelRouter, FakeProvider],
    registry: ToolRegistry,
    test_config: Settings,
    event_bus: EventBus,
    migrated_db: aiosqlite.Connection,
) -> None:
    """Downstream node receives prerequisite node's findings in prompt."""
    router, fake = fake_router

    fake.queue_response(_valid_worker_json("Alpha output"))
    fake.queue_response(_valid_worker_json("Beta output"))
    fake.queue_response("# Final Report")

    plan = Plan(
        goal="Verify dependency passing",
        subtasks=[
            SubtaskNode(
                id="node_a",
                title="Node Alpha",
                objective="Alpha objective",
                kind="research",
                expected_output="Alpha out",
                depends_on=[],
            ),
            SubtaskNode(
                id="node_b",
                title="Node Beta",
                objective="Beta objective",
                kind="compute",
                expected_output="Beta out",
                depends_on=["node_a"],
            ),
        ],
        synthesis_instructions="Merge",
    )

    executor = DAGExecutor(
        router=router, registry=registry, config=test_config, event_bus=event_bus
    )
    await executor.execute_plan(goal=plan.goal, existing_plan=plan, db=migrated_db)

    # Check calls to worker B
    chat_calls = [c for c in fake.calls if "messages" in c]
    assert len(chat_calls) >= 2
    node_b_prompt = chat_calls[1]["messages"][-1].get_text()
    assert "Findings from Prerequisite Tasks:" in node_b_prompt
    assert "[node_a]: Alpha output" in node_b_prompt


@pytest.mark.asyncio
async def test_executor_sqlite_status_persistence(
    fake_router: tuple[ModelRouter, FakeProvider],
    registry: ToolRegistry,
    test_config: Settings,
    event_bus: EventBus,
    migrated_db: aiosqlite.Connection,
) -> None:
    """Runs and task_nodes table transitions are recorded in SQLite."""
    router, fake = fake_router

    fake.queue_response(_valid_worker_json("Worker 1 completed successfully"))
    fake.queue_response("# Synthesis complete")

    plan = Plan(
        goal="DB Persistence Verification",
        subtasks=[
            SubtaskNode(
                id="n1",
                title="Single Subtask",
                objective="Inspect status",
                kind="research",
                expected_output="Result",
                depends_on=[],
            )
        ],
        synthesis_instructions="Combine",
    )

    executor = DAGExecutor(
        router=router, registry=registry, config=test_config, event_bus=event_bus
    )
    res = await executor.execute_plan(
        goal=plan.goal,
        existing_plan=plan,
        db=migrated_db,
        run_id="run_test_persist_123",
    )
    assert res.status == "done"

    # Verify run row
    run_row = await fetch_one(
        migrated_db, "SELECT * FROM runs WHERE id = ?", ("run_test_persist_123",)
    )
    assert run_row is not None
    assert run_row["status"] == "done"
    assert run_row["finished_at"] is not None

    # Verify task_nodes row
    node_row = await fetch_one(
        migrated_db,
        "SELECT * FROM task_nodes WHERE run_id = ? AND id = ?",
        ("run_test_persist_123", "n1"),
    )
    assert node_row is not None
    assert node_row["status"] == "done"
    assert node_row["finished_at"] is not None
    assert node_row["output_json"] is not None
    assert "Worker 1 completed" in node_row["output_json"]


@pytest.mark.asyncio
async def test_executor_failure_cascading(
    fake_router: tuple[ModelRouter, FakeProvider],
    registry: ToolRegistry,
    test_config: Settings,
    event_bus: EventBus,
    migrated_db: aiosqlite.Connection,
) -> None:
    """When a parent task fails, downstream dependent tasks fail without running."""
    router, fake = fake_router

    # t1 raises an exception
    fake.queue_response(RuntimeError("Network failure on t1"))
    # Synthesizer runs on remaining
    fake.queue_response("# Report noting failures")

    plan = Plan(
        goal="Test failure cascade",
        subtasks=[
            SubtaskNode(
                id="t1",
                title="Failing Task",
                objective="Do doomed task",
                kind="research",
                expected_output="Failure",
                depends_on=[],
            ),
            SubtaskNode(
                id="t2",
                title="Dependent Task",
                objective="Should never run",
                kind="compute",
                expected_output="None",
                depends_on=["t1"],
            ),
        ],
        synthesis_instructions="Summarize",
    )

    executor = DAGExecutor(
        router=router, registry=registry, config=test_config, event_bus=event_bus
    )
    res = await executor.execute_plan(goal=plan.goal, existing_plan=plan, db=migrated_db)

    # t1 should be in node_errors
    assert "t1" in res.node_errors
    assert "Network failure on t1" in res.node_errors["t1"]

    # t2 should be cascaded as failed
    assert "t2" in res.node_errors
    assert "Prerequisite dependency failed" in res.node_errors["t2"]

    # Check task_nodes table
    rows = await fetch_all(
        migrated_db, "SELECT id, status, error FROM task_nodes WHERE run_id = ?", (res.run_id,)
    )
    status_map = {r["id"]: (r["status"], r["error"]) for r in rows}
    assert status_map["t1"][0] == "failed"
    assert status_map["t2"][0] == "failed"
    assert "Prerequisite dependency failed" in status_map["t2"][1]


@pytest.mark.asyncio
async def test_executor_restart_resume(
    fake_router: tuple[ModelRouter, FakeProvider],
    registry: ToolRegistry,
    test_config: Settings,
    migrated_db: aiosqlite.Connection,
) -> None:
    """resume_run reuses completed task nodes from SQLite and executes remaining pending nodes."""
    router, fake = fake_router
    run_id = "run_resume_test_456"

    # Seed run into DB
    await migrated_db.execute(
        """
        INSERT INTO runs (id, kind, status, started_at)
        VALUES (?, 'chat', 'interrupted', datetime('now'))
        """,
        (run_id,),
    )

    t1_output = WorkerOutput(
        summary="Cached result for t1",
        key_points=["Cached pt"],
        sources=[WorkerSource(title="Cached Doc", url="https://example.com/cached")],
        confidence=0.9,
    )

    # Insert t1 as done, t2 as pending
    await migrated_db.execute(
        """
        INSERT INTO task_nodes
        (id, run_id, node_index, title, objective, kind, depends_on_json, status, output_json)
        VALUES (?, ?, 0, 'Task 1', 'Obj 1', 'research', '[]', 'done', ?)
        """,
        ("t1", run_id, t1_output.model_dump_json()),
    )
    await migrated_db.execute(
        """
        INSERT INTO task_nodes
        (id, run_id, node_index, title, objective, kind, depends_on_json, status)
        VALUES (?, ?, 1, 'Task 2', 'Obj 2', 'compute', '["t1"]', 'pending')
        """,
        ("t2", run_id),
    )
    await migrated_db.commit()

    # Queue response for t2 and synthesizer
    fake.queue_response(_valid_worker_json("Fresh result for t2"))
    fake.queue_response("# Resumed synthesis report")

    # Run resume
    result = await resume_run(
        run_id=run_id,
        db=migrated_db,
        router=router,
        registry=registry,
        config=test_config,
    )

    assert result.status == "done"
    assert result.node_outputs["t1"].summary == "Cached result for t1"
    assert result.node_outputs["t2"].summary == "Fresh result for t2"
    assert "Resumed synthesis report" in result.final_text

    # Exactly 2 LLM chat calls: worker for t2, and synthesizer (t1 was cached)
    chat_calls = [c for c in fake.calls if "messages" in c]
    assert len(chat_calls) == 2


@pytest.mark.asyncio
async def test_startup_mark_interrupted_runs(
    migrated_db: aiosqlite.Connection,
) -> None:
    """mark_interrupted_runs transitions orphaned 'running' runs to 'interrupted'."""
    # Seed active runs and task_nodes
    await migrated_db.execute(
        "INSERT INTO runs (id, kind, status, started_at) "
        "VALUES ('r_running_1', 'chat', 'running', datetime('now'))"
    )
    await migrated_db.execute(
        "INSERT INTO runs (id, kind, status, started_at) "
        "VALUES ('r_done_1', 'chat', 'done', datetime('now'))"
    )
    await migrated_db.execute(
        """
        INSERT INTO task_nodes (id, run_id, node_index, title, objective, kind, status)
        VALUES ('n_active', 'r_running_1', 0, 'Active Node', 'Obj', 'research', 'running')
        """
    )
    await migrated_db.commit()

    affected = await mark_interrupted_runs(migrated_db)
    assert affected == 1

    r1 = await fetch_one(
        migrated_db, "SELECT status, finished_at FROM runs WHERE id = 'r_running_1'"
    )
    assert r1 is not None
    assert r1["status"] == "interrupted"
    assert r1["finished_at"] is not None

    r_done = await fetch_one(migrated_db, "SELECT status FROM runs WHERE id = 'r_done_1'")
    assert r_done is not None
    assert r_done["status"] == "done"

    node = await fetch_one(
        migrated_db, "SELECT status, error FROM task_nodes WHERE id = 'n_active'"
    )
    assert node is not None
    assert node["status"] == "failed"
    assert "Daemon interrupted" in node["error"]
