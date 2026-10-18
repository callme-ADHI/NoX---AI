"""Run interruption recovery and resumption per Section 6.4."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from noxai.agent.executor import DAGExecutor, PlanExecutionResult
from noxai.agent.planner import Plan, SubtaskNode
from noxai.db import execute, fetch_all, fetch_one

if TYPE_CHECKING:
    import aiosqlite

    from noxai.config import Settings
    from noxai.models.router import ModelRouter
    from noxai.tools.registry import ToolRegistry

log = logging.getLogger(__name__)


async def mark_interrupted_runs(db: aiosqlite.Connection) -> int:
    """Mark any orphaned runs in 'running' state as 'interrupted'.

    Called during daemon startup to recover from unclean exits.
    """
    # 1. Update task_nodes running in orphaned runs
    await execute(
        db,
        """
        UPDATE task_nodes
        SET status = 'failed', error = 'Daemon interrupted', finished_at = datetime('now')
        WHERE status = 'running'
        """,
    )

    # 2. Update runs in 'running' state
    cursor = await db.execute(
        """
        UPDATE runs
        SET status = 'interrupted', finished_at = datetime('now')
        WHERE status = 'running'
        """
    )
    affected = cursor.rowcount
    await db.commit()

    if affected > 0:
        log.warning("Marked %d orphaned active runs as 'interrupted'.", affected)
    return affected


async def resume_run(
    run_id: str,
    db: aiosqlite.Connection,
    router: ModelRouter,
    registry: ToolRegistry,
    config: Settings,
) -> PlanExecutionResult:
    """Resume an interrupted or failed run, reusing all previously completed task nodes."""
    run_row = await fetch_one(db, "SELECT * FROM runs WHERE id = ?", (run_id,))
    if run_row is None:
        raise ValueError(f"Run '{run_id}' not found in database.")

    node_rows = await fetch_all(
        db,
        "SELECT * FROM task_nodes WHERE run_id = ? ORDER BY node_index ASC",
        (run_id,),
    )
    if not node_rows:
        raise ValueError(f"Run '{run_id}' has no associated task nodes to resume.")

    # Reconstruct subtask nodes
    subtasks: list[SubtaskNode] = []
    for r in node_rows:
        deps = []
        if r["depends_on_json"]:
            try:
                deps = json.loads(r["depends_on_json"])
            except Exception:
                deps = []

        subtasks.append(
            SubtaskNode(
                id=str(r["id"]),
                title=str(r["title"]),
                objective=str(r["objective"]),
                kind=str(r["kind"]),  # type: ignore[arg-type]
                depends_on=deps,
                expected_output="Resumed subtask objective findings.",
            )
        )

    # Reset any nodes that were left in failed/running status so they can re-run
    await execute(
        db,
        """
        UPDATE task_nodes
        SET status = 'pending', error = NULL
        WHERE run_id = ? AND status != 'done'
        """,
        (run_id,),
    )

    allow_cloud = bool(run_row["allow_cloud"])
    goal = subtasks[0].title if subtasks else f"Resumed run {run_id}"

    plan = Plan(
        goal=goal,
        subtasks=subtasks,
        synthesis_instructions="Synthesize all findings into a structured final report.",
    )

    executor = DAGExecutor(
        router=router,
        registry=registry,
        config=config,
    )

    return await executor.execute_plan(
        goal=goal,
        run_id=run_id,
        conversation_id=run_row["conversation_id"],
        allow_cloud=allow_cloud,
        db=db,
        existing_plan=plan,
    )
