"""Task Center: background task orchestration, priority queue, worker loop,
and approval parking (§1.4, §G3).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from noxai.agent.loop import AgentLoop
from noxai.config import Settings
from noxai.db import execute, fetch_all, fetch_one, get_db
from noxai.events import EventBus, global_event_bus, make_event
from noxai.models.router import ModelRouter
from noxai.tasks.models import Task, TaskLane, TaskPriority, TaskStatus
from noxai.tools.registry import ToolPendingApproval, ToolRegistry
from noxai.tools.registry import registry as default_registry

log = logging.getLogger(__name__)


class TaskCenter:
    """Manages asynchronous background tasks, priority queueing, execution, and approval parking."""

    def __init__(
        self,
        config: Settings,
        router: ModelRouter | None = None,
        registry: ToolRegistry | None = None,
        event_bus: EventBus | None = None,
        max_concurrent_local: int = 2,
        max_concurrent_cloud: int = 2,
    ) -> None:
        self.config = config
        self.router = router
        self.registry = registry or default_registry
        self.event_bus = event_bus or global_event_bus
        self.max_concurrent_local = max_concurrent_local
        self.max_concurrent_cloud = max_concurrent_cloud

        # Active tasks state
        self._running_jobs: dict[str, asyncio.Task[Any]] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._worker_loop_task: asyncio.Task[Any] | None = None
        self._stopped = False

    # ---------------------------------------------------------------------------
    # CRUD & Queue Operations
    # ---------------------------------------------------------------------------

    async def create_task(
        self,
        title: str,
        prompt: str,
        lane: str | TaskLane = TaskLane.LOCAL,
        priority: str | TaskPriority = TaskPriority.NORMAL,
        run_id: str | None = None,
        db: aiosqlite.Connection | None = None,
    ) -> Task:
        """Create and enqueue a new background task."""
        lane_val = TaskLane(lane) if isinstance(lane, str) else lane
        prio_val = TaskPriority(priority) if isinstance(priority, str) else priority
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        now_iso = datetime.now(UTC).isoformat()
        canc_token = uuid.uuid4().hex

        task = Task(
            id=task_id,
            title=title,
            prompt=prompt,
            lane=lane_val,
            priority=prio_val,
            status=TaskStatus.QUEUED,
            created_at=now_iso,
            cancellation_token=canc_token,
            run_id=run_id,
        )

        async def _insert(conn: aiosqlite.Connection) -> None:
            await execute(
                conn,
                """
                INSERT INTO tasks
                (id, title, prompt, lane, priority, status, created_at, cancellation_token, run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.title,
                    task.prompt,
                    task.lane.value,
                    task.priority.value,
                    task.status.value,
                    task.created_at,
                    task.cancellation_token,
                    task.run_id,
                ),
            )
            await conn.commit()

        if db is not None:
            await _insert(db)
        else:
            async with get_db() as conn:
                await _insert(conn)

        await self.event_bus.publish(
            make_event("task_created", task.to_dict(), run_id=run_id),
            db=db,
        )
        return task

    async def get_task(
        self,
        task_id: str,
        db: aiosqlite.Connection | None = None,
    ) -> Task | None:
        """Retrieve task by ID."""

        async def _query(conn: aiosqlite.Connection) -> Task | None:
            row = await fetch_one(conn, "SELECT * FROM tasks WHERE id = ?", (task_id,))
            if not row:
                return None
            return self._row_to_task(row)

        if db is not None:
            return await _query(db)
        async with get_db() as conn:
            return await _query(conn)

    async def list_tasks(
        self,
        status: str | TaskStatus | None = None,
        lane: str | TaskLane | None = None,
        limit: int = 50,
        db: aiosqlite.Connection | None = None,
    ) -> list[Task]:
        """List tasks with optional filtering by status and lane."""
        query = "SELECT * FROM tasks"
        clauses = []
        params: list[Any] = []

        if status:
            s_val = status.value if isinstance(status, TaskStatus) else status
            clauses.append("status = ?")
            params.append(s_val)

        if lane:
            l_val = lane.value if isinstance(lane, TaskLane) else lane
            clauses.append("lane = ?")
            params.append(l_val)

        if clauses:
            query += " WHERE " + " AND ".join(clauses)

        query += (
            " ORDER BY "
            "CASE priority WHEN 'urgent' THEN 4 WHEN 'high' THEN 3 "
            "WHEN 'normal' THEN 2 WHEN 'low' THEN 1 ELSE 0 END DESC, "
            "created_at ASC LIMIT ?"
        )
        params.append(limit)

        async def _query(conn: aiosqlite.Connection) -> list[Task]:
            rows = await fetch_all(conn, query, tuple(params))
            return [self._row_to_task(r) for r in rows]

        if db is not None:
            return await _query(db)
        async with get_db() as conn:
            return await _query(conn)

    async def cancel_task(
        self,
        task_id: str,
        db: aiosqlite.Connection | None = None,
    ) -> bool:
        """Cancel a queued or actively running task."""
        # 1. Trigger cancellation token if actively executing
        if task_id in self._cancel_events:
            self._cancel_events[task_id].set()

        if task_id in self._running_jobs:
            self._running_jobs[task_id].cancel()

        # 2. Update task state in database
        now_iso = datetime.now(UTC).isoformat()

        async def _update(conn: aiosqlite.Connection) -> bool:
            row = await fetch_one(conn, "SELECT status FROM tasks WHERE id = ?", (task_id,))
            if not row:
                return False
            curr_status = row["status"]
            terminal = (
                TaskStatus.DONE.value,
                TaskStatus.FAILED.value,
                TaskStatus.CANCELLED.value,
            )
            if curr_status in terminal:
                return False

            await execute(
                conn,
                "UPDATE tasks SET status = ?, finished_at = ? WHERE id = ?",
                (TaskStatus.CANCELLED.value, now_iso, task_id),
            )
            await conn.commit()
            return True

        if db is not None:
            success = await _update(db)
        else:
            async with get_db() as conn:
                success = await _update(conn)

        if success:
            await self.event_bus.publish(
                make_event("task_cancelled", {"task_id": task_id, "status": "cancelled"}),
                db=db,
            )
        return success

    async def pause_task(
        self,
        task_id: str,
        db: aiosqlite.Connection | None = None,
    ) -> bool:
        """Pause a queued task."""

        async def _do(conn: aiosqlite.Connection) -> bool:
            row = await fetch_one(conn, "SELECT status FROM tasks WHERE id = ?", (task_id,))
            if not row or row["status"] != TaskStatus.QUEUED.value:
                return False
            await execute(
                conn,
                "UPDATE tasks SET status = ? WHERE id = ?",
                (TaskStatus.PAUSED.value, task_id),
            )
            await conn.commit()
            return True

        if db is not None:
            return await _do(db)
        async with get_db() as conn:
            return await _do(conn)

    async def resume_task(
        self,
        task_id: str,
        db: aiosqlite.Connection | None = None,
    ) -> bool:
        """Resume a paused task."""

        async def _do(conn: aiosqlite.Connection) -> bool:
            row = await fetch_one(conn, "SELECT status FROM tasks WHERE id = ?", (task_id,))
            if not row or row["status"] != TaskStatus.PAUSED.value:
                return False
            await execute(
                conn,
                "UPDATE tasks SET status = ? WHERE id = ?",
                (TaskStatus.QUEUED.value, task_id),
            )
            await conn.commit()
            return True

        if db is not None:
            return await _do(db)
        async with get_db() as conn:
            return await _do(conn)

    # ---------------------------------------------------------------------------
    # Approval Parking
    # ---------------------------------------------------------------------------

    async def park_for_approval(
        self,
        task_id: str,
        approval_id: int,
        db: aiosqlite.Connection | None = None,
    ) -> None:
        """Park a running task into waiting_approval state linked to an approval row."""

        async def _do(conn: aiosqlite.Connection) -> None:
            await execute(
                conn,
                "UPDATE tasks SET status = ?, approval_id = ? WHERE id = ?",
                (TaskStatus.WAITING_APPROVAL.value, approval_id, task_id),
            )
            await conn.commit()

        if db is not None:
            await _do(db)
        else:
            async with get_db() as conn:
                await _do(conn)

        await self.event_bus.publish(
            make_event(
                "task_waiting_approval",
                {"task_id": task_id, "approval_id": approval_id},
            ),
            db=db,
        )

    async def handle_approval_resolved(
        self,
        approval_id: int,
        approved: bool,
        db: aiosqlite.Connection | None = None,
    ) -> None:
        """Handle outcome of an owner approval decision for parked tasks."""

        async def _do(conn: aiosqlite.Connection) -> None:
            tasks = await fetch_all(
                conn,
                "SELECT id FROM tasks WHERE approval_id = ? AND status = ?",
                (approval_id, TaskStatus.WAITING_APPROVAL.value),
            )
            for t_row in tasks:
                t_id = t_row["id"]
                if approved:
                    # Unpark task back to queued for execution
                    await execute(
                        conn,
                        "UPDATE tasks SET status = ? WHERE id = ?",
                        (TaskStatus.QUEUED.value, t_id),
                    )
                else:
                    # Denied: cancel task with failure notice
                    now_iso = datetime.now(UTC).isoformat()
                    await execute(
                        conn,
                        "UPDATE tasks SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                        (
                            TaskStatus.CANCELLED.value,
                            now_iso,
                            f"Required approval {approval_id} was denied by owner.",
                            t_id,
                        ),
                    )
            await conn.commit()

        if db is not None:
            await _do(db)
        else:
            async with get_db() as conn:
                await _do(conn)

    # ---------------------------------------------------------------------------
    # Worker Execution
    # ---------------------------------------------------------------------------

    async def process_next_task(self, db: aiosqlite.Connection | None = None) -> bool:
        """Claim and execute the highest-priority queued task."""

        async def _claim(conn: aiosqlite.Connection) -> Task | None:
            # Query top queued task respecting priority
            query = (
                "SELECT * FROM tasks WHERE status = 'queued' "
                "ORDER BY "
                "CASE priority WHEN 'urgent' THEN 4 WHEN 'high' THEN 3 "
                "WHEN 'normal' THEN 2 WHEN 'low' THEN 1 ELSE 0 END DESC, "
                "created_at ASC LIMIT 1"
            )
            row = await fetch_one(conn, query)
            if not row:
                return None

            t = self._row_to_task(row)
            # Update to running
            now_iso = datetime.now(UTC).isoformat()
            await execute(
                conn,
                "UPDATE tasks SET status = ?, started_at = ? WHERE id = ?",
                (TaskStatus.RUNNING.value, now_iso, t.id),
            )
            await conn.commit()
            t.status = TaskStatus.RUNNING
            t.started_at = now_iso
            return t

        if db is not None:
            task = await _claim(db)
        else:
            async with get_db() as conn:
                task = await _claim(conn)

        if not task:
            return False

        # Execute task
        await self._execute_task(task)
        return True

    async def _execute_task(self, task: Task) -> None:
        """Run the task logic with AgentLoop, cancellation tokens, and error handling."""
        cancel_evt = asyncio.Event()
        self._cancel_events[task.id] = cancel_evt

        await self.event_bus.publish(
            make_event("task_started", {"task_id": task.id, "lane": task.lane.value})
        )

        try:
            # If router not provided, execute lightweight mock or direct
            if self.router is None:
                # No LLM router attached (test / mock mode)
                await asyncio.sleep(0.01)
                final_output = f"Completed task: {task.title}"
            else:
                allow_cloud = (task.lane == TaskLane.CLOUD)
                loop = AgentLoop(
                    config=self.config,
                    router=self.router,
                    registry=self.registry,
                    event_bus=self.event_bus,
                )
                loop_res = await loop.run(
                    user_message=task.prompt,
                    allow_cloud=allow_cloud,
                    lane="front_desk" if allow_cloud else "chat",
                )
                if loop_res.status == "cancelled":
                    raise asyncio.CancelledError()
                if loop_res.status == "failed":
                    raise RuntimeError(loop_res.error or "Agent loop failed")
                final_output = loop_res.text

            # Mark task done
            now_iso = datetime.now(UTC).isoformat()
            res_blob = json.dumps({"output": final_output})
            async with get_db() as db:
                await execute(
                    db,
                    """
                    UPDATE tasks
                    SET status = ?, finished_at = ?, result_json = ?, progress = 1.0
                    WHERE id = ?
                    """,
                    (TaskStatus.DONE.value, now_iso, res_blob, task.id),
                )
                await db.commit()

            await self.event_bus.publish(
                make_event(
                    "task_done",
                    {"task_id": task.id, "title": task.title, "result": final_output},
                )
            )

        except asyncio.CancelledError:
            log.info("Task %s was cancelled.", task.id)
            now_iso = datetime.now(UTC).isoformat()
            async with get_db() as db:
                await execute(
                    db,
                    "UPDATE tasks SET status = ?, finished_at = ? WHERE id = ?",
                    (TaskStatus.CANCELLED.value, now_iso, task.id),
                )
                await db.commit()
            await self.event_bus.publish(
                make_event("task_cancelled", {"task_id": task.id, "status": "cancelled"})
            )

        except ToolPendingApproval as exc:
            log.info("Task %s parked waiting for approval (id=%s).", task.id, exc.approval_id)
            await self.park_for_approval(task.id, int(exc.approval_id))

        except Exception as exc:
            log.error("Task %s execution failed: %s", task.id, exc)
            now_iso = datetime.now(UTC).isoformat()
            async with get_db() as db:
                await execute(
                    db,
                    "UPDATE tasks SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                    (TaskStatus.FAILED.value, now_iso, str(exc), task.id),
                )
                await db.commit()
            await self.event_bus.publish(
                make_event("task_failed", {"task_id": task.id, "error": str(exc)})
            )

        finally:
            self._cancel_events.pop(task.id, None)
            self._running_jobs.pop(task.id, None)

    def start_worker_loop(self, poll_interval: float = 0.5) -> None:
        """Start the background task processing loop."""
        if self._worker_loop_task is not None and not self._worker_loop_task.done():
            return

        self._stopped = False

        async def _loop() -> None:
            while not self._stopped:
                try:
                    had_work = await self.process_next_task()
                    if not had_work:
                        await asyncio.sleep(poll_interval)
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    log.warning("Task worker loop iteration error: %s", exc)
                    await asyncio.sleep(poll_interval)

        self._worker_loop_task = asyncio.create_task(_loop())

    async def stop(self) -> None:
        """Stop worker loop and wait for active tasks."""
        self._stopped = True
        if self._worker_loop_task:
            self._worker_loop_task.cancel()
            try:
                await self._worker_loop_task
            except asyncio.CancelledError:
                pass
            self._worker_loop_task = None

        # Cancel remaining active jobs
        for tid in list(self._running_jobs.keys()):
            await self.cancel_task(tid)

    # ---------------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------------

    @staticmethod
    def _row_to_task(row: aiosqlite.Row) -> Task:
        return Task(
            id=str(row["id"]),
            title=str(row["title"]),
            prompt=str(row["prompt"]),
            lane=TaskLane(str(row["lane"])),
            priority=TaskPriority(str(row["priority"])),
            status=TaskStatus(str(row["status"])),
            created_at=str(row["created_at"]),
            started_at=str(row["started_at"]) if row["started_at"] else None,
            finished_at=str(row["finished_at"]) if row["finished_at"] else None,
            error=str(row["error"]) if row["error"] else None,
            result_json=str(row["result_json"]) if row["result_json"] else None,
            progress=float(row["progress"] or 0.0),
            approval_id=int(row["approval_id"]) if row["approval_id"] is not None else None,
            cancellation_token=(
                str(row["cancellation_token"]) if row["cancellation_token"] else None
            ),
            run_id=str(row["run_id"]) if row["run_id"] else None,
        )
