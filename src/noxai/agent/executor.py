"""Executor engine for running the Planner DAG with concurrency bounds and persistence."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from noxai.agent.planner import Plan, SubtaskNode, generate_plan, validate_plan_dag
from noxai.agent.synthesizer import SynthesizerResult, synthesize_report
from noxai.agent.worker import WorkerOutput, execute_worker_subtask
from noxai.db import execute, fetch_all
from noxai.events import EventBus, global_event_bus, make_event
from noxai.memory.recall import recall_memories

if TYPE_CHECKING:
    import aiosqlite

    from noxai.config import Settings
    from noxai.models.router import ModelRouter
    from noxai.tools.registry import ToolRegistry

log = logging.getLogger(__name__)


@dataclass
class PlanExecutionResult:
    """Outcome of full DAG execution and synthesis."""

    run_id: str
    status: str  # "done" | "failed" | "interrupted"
    plan: Plan
    node_outputs: dict[str, WorkerOutput]
    node_errors: dict[str, str]
    final_text: str
    citations: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    error: str | None = None


class DAGExecutor:
    """Orchestrates plan generation, parallel worker DAG execution, and synthesis."""

    def __init__(
        self,
        router: ModelRouter,
        registry: ToolRegistry,
        config: Settings,
        event_bus: EventBus | None = None,
    ) -> None:
        self.router = router
        self.registry = registry
        self.config = config
        self.event_bus = event_bus or global_event_bus

    async def execute_plan(
        self,
        goal: str,
        *,
        conversation_id: str | None = None,
        allow_cloud: bool = False,
        db: aiosqlite.Connection | None = None,
        run_id: str | None = None,
        existing_plan: Plan | None = None,
    ) -> PlanExecutionResult:
        """Run the full Planner -> Worker DAG -> Synthesizer pipeline."""
        start_time = time.perf_counter()
        actual_run_id = run_id or uuid.uuid4().hex

        # 1. Memory recall for local runs
        memory_ctx = ""
        if not allow_cloud and db is not None:
            try:
                rec_res = await recall_memories(
                    goal,
                    db=db,
                    router=self.router,
                    config=self.config,
                    top_k=4,
                )
                memory_ctx = rec_res.formatted_block
            except Exception as exc:
                log.warning("Memory recall failed in DAG executor: %s", exc)

        # 2. Record run in DB
        if db is not None:
            await execute(
                db,
                """
                INSERT INTO runs (id, kind, conversation_id, status, allow_cloud, started_at)
                VALUES (?, 'chat', ?, 'running', ?, datetime('now'))
                ON CONFLICT(id) DO UPDATE SET status = 'running'
                """,
                (actual_run_id, conversation_id, 1 if allow_cloud else 0),
            )
            await db.commit()

        # 3. Plan generation
        plan = existing_plan
        if plan is None:
            plan = await generate_plan(
                goal,
                router=self.router,
                config=self.config,
                allow_cloud=allow_cloud,
                memory_context=memory_ctx or None,
            )

        # Validate DAG
        try:
            validate_plan_dag(
                plan,
                max_subtasks=self.config.agent.max_subtasks,
                max_depth=self.config.agent.max_depth,
            )
        except Exception as exc:
            log.warning("Plan DAG validation failed: %s; using fallback", exc)
            from noxai.agent.planner import make_fallback_plan

            plan = make_fallback_plan(goal)

        # 4. Emit plan_created event
        await self.event_bus.publish(
            make_event(
                "plan_created",
                {
                    "goal": plan.goal,
                    "subtasks": [n.model_dump() for n in plan.subtasks],
                    "synthesis_instructions": plan.synthesis_instructions,
                },
                run_id=actual_run_id,
            ),
            db=db,
        )

        # 5. Persist task_nodes in DB
        if db is not None:
            for idx, node in enumerate(plan.subtasks):
                await execute(
                    db,
                    """
                    INSERT INTO task_nodes
                    (id, run_id, node_index, title, objective, kind, depends_on_json, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
                    ON CONFLICT(id) DO UPDATE SET
                        run_id = excluded.run_id,
                        node_index = excluded.node_index,
                        title = excluded.title,
                        objective = excluded.objective,
                        kind = excluded.kind,
                        depends_on_json = excluded.depends_on_json,
                        status = 'pending',
                        output_json = NULL,
                        error = NULL,
                        started_at = NULL,
                        finished_at = NULL
                    WHERE task_nodes.run_id != excluded.run_id
                    """,
                    (
                        node.id,
                        actual_run_id,
                        idx,
                        node.title,
                        node.objective,
                        node.kind,
                        json.dumps(node.depends_on),
                    ),
                )
            await db.commit()

        # 6. Run Worker DAG with bounded concurrency
        concurrency = max(1, self.config.agent.worker_concurrency)
        sem = asyncio.Semaphore(concurrency)

        nodes_by_id: dict[str, SubtaskNode] = {n.id: n for n in plan.subtasks}
        node_outputs: dict[str, WorkerOutput] = {}
        node_errors: dict[str, str] = {}
        node_statuses: dict[str, str] = {n.id: "pending" for n in plan.subtasks}

        # Check if resuming existing outputs from DB
        if db is not None:
            existing_rows = await fetch_all(
                db,
                "SELECT id, status, output_json, error FROM task_nodes WHERE run_id = ?",
                (actual_run_id,),
            )
            for r in existing_rows:
                nid = str(r["id"])
                st = str(r["status"])
                if st == "done" and r["output_json"]:
                    try:
                        node_outputs[nid] = WorkerOutput.model_validate_json(r["output_json"])
                        node_statuses[nid] = "done"
                    except Exception as err:
                        log.debug("Failed parsing cached node output for %s: %s", nid, err)
                elif st == "failed":
                    node_errors[nid] = str(r["error"] or "Previously failed")
                    node_statuses[nid] = "failed"

        running_tasks: dict[str, asyncio.Task[WorkerOutput]] = {}
        bg_tasks: set[asyncio.Task[Any]] = set()

        async def _run_single_node(target_node: SubtaskNode) -> WorkerOutput:
            async with sem:
                node_statuses[target_node.id] = "running"
                if db is not None:
                    await execute(
                        db,
                        "UPDATE task_nodes SET status = 'running', "
                        "started_at = datetime('now') WHERE id = ? AND run_id = ?",
                        (target_node.id, actual_run_id),
                    )
                    await db.commit()

                await self.event_bus.publish(
                    make_event(
                        "node_started",
                        {"node_id": target_node.id, "title": target_node.title},
                        run_id=actual_run_id,
                    ),
                    db=db,
                )

                def _progress_cb(msg: str) -> None:
                    t = asyncio.create_task(
                        self.event_bus.publish(
                            make_event(
                                "node_progress",
                                {"node_id": target_node.id, "message": msg},
                                run_id=actual_run_id,
                            ),
                            db=db,
                        )
                    )
                    bg_tasks.add(t)
                    t.add_done_callback(bg_tasks.discard)

                dep_findings = {
                    dep_id: node_outputs[dep_id]
                    for dep_id in target_node.depends_on
                    if dep_id in node_outputs
                }

                out = await execute_worker_subtask(
                    node=target_node,
                    dependency_outputs=dep_findings,
                    router=self.router,
                    registry=self.registry,
                    config=self.config,
                    allow_cloud=allow_cloud,
                    db=db,
                    emit_progress=_progress_cb,
                    run_id=actual_run_id,
                )
                return out

        # Execution loop
        while any(st in ("pending", "running") for st in node_statuses.values()):
            # Find newly ready pending nodes
            for nid, node in nodes_by_id.items():
                if node_statuses[nid] != "pending":
                    continue

                # Check dependencies
                deps = node.depends_on
                if any(dep in node_errors for dep in deps):
                    failed_deps = [d for d in deps if d in node_errors]
                    node_statuses[nid] = "failed"
                    err_msg = f"Prerequisite dependency failed: {failed_deps}"
                    node_errors[nid] = err_msg
                    if db is not None:
                        await execute(
                            db,
                            "UPDATE task_nodes SET status = 'failed', error = ?, "
                            "finished_at = datetime('now') WHERE id = ? AND run_id = ?",
                            (err_msg, nid, actual_run_id),
                        )
                        await db.commit()
                    await self.event_bus.publish(
                        make_event(
                            "node_failed",
                            {"node_id": nid, "error": err_msg},
                            run_id=actual_run_id,
                        ),
                        db=db,
                    )
                    continue

                if all(dep in node_outputs for dep in deps):
                    # Ready to run!
                    t = asyncio.create_task(_run_single_node(node))
                    running_tasks[nid] = t

            if not running_tasks:
                # No tasks running and no new tasks can be scheduled
                break

            # Wait for at least one task to finish
            done, _ = await asyncio.wait(
                running_tasks.values(),
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Process completed tasks
            for t in done:
                done_nid = next(k for k, v in running_tasks.items() if v == t)
                del running_tasks[done_nid]

                try:
                    res_out = await t
                    node_outputs[done_nid] = res_out
                    node_statuses[done_nid] = "done"

                    if db is not None:
                        await execute(
                            db,
                            """
                            UPDATE task_nodes
                            SET status = 'done', output_json = ?, finished_at = datetime('now')
                            WHERE id = ? AND run_id = ?
                            """,
                            (res_out.model_dump_json(), done_nid, actual_run_id),
                        )
                        await db.commit()

                    await self.event_bus.publish(
                        make_event(
                            "node_done",
                            {
                                "node_id": done_nid,
                                "summary": res_out.summary,
                                "sources": [s.model_dump() for s in res_out.sources],
                                "confidence": res_out.confidence,
                            },
                            run_id=actual_run_id,
                        ),
                        db=db,
                    )
                except Exception as exc:
                    log.error("Worker node %s failed with exception: %s", done_nid, exc)
                    node_errors[done_nid] = str(exc)
                    node_statuses[done_nid] = "failed"

                    if db is not None:
                        await execute(
                            db,
                            "UPDATE task_nodes SET status = 'failed', error = ?, "
                            "finished_at = datetime('now') WHERE id = ? AND run_id = ?",
                            (str(exc), done_nid, actual_run_id),
                        )
                        await db.commit()

                    await self.event_bus.publish(
                        make_event(
                            "node_failed",
                            {"node_id": done_nid, "error": str(exc)},
                            run_id=actual_run_id,
                        ),
                        db=db,
                    )

        # 7. Synthesizer phase
        await self.event_bus.publish(
            make_event("synthesis_started", {"goal": plan.goal}, run_id=actual_run_id),
            db=db,
        )

        all_node_tuples = [
            (
                node,
                node_outputs.get(node.id),
                node_errors.get(node.id),
            )
            for node in plan.subtasks
        ]

        synth_result: SynthesizerResult = await synthesize_report(
            goal=plan.goal,
            synthesis_instructions=plan.synthesis_instructions,
            node_results=all_node_tuples,
            router=self.router,
            allow_cloud=allow_cloud,
        )

        elapsed = time.perf_counter() - start_time
        overall_status = "done" if node_outputs else "failed"

        if db is not None:
            await execute(
                db,
                "UPDATE runs SET status = ?, finished_at = datetime('now') WHERE id = ?",
                (overall_status, actual_run_id),
            )
            await db.commit()

        await self.event_bus.publish(
            make_event(
                "run_completed",
                {
                    "status": overall_status,
                    "duration_seconds": elapsed,
                    "citations_count": len(synth_result.citations),
                },
                run_id=actual_run_id,
            ),
            db=db,
        )

        return PlanExecutionResult(
            run_id=actual_run_id,
            status=overall_status,
            plan=plan,
            node_outputs=node_outputs,
            node_errors=node_errors,
            final_text=synth_result.text,
            citations=synth_result.citations,
            duration_seconds=elapsed,
        )
