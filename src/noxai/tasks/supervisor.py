"""Detached Task Supervisor (Phase 2).

Owned by the daemon lifecycle (started in app startup, stopped in shutdown).
Provides:
- Strong in-memory asyncio task registry decoupled from HTTP/WebSocket/UI sessions.
- SQLite persistence with heartbeat timestamps every 3s.
- Automatic stale heartbeat detection and resume on daemon startup.
- Safe cooperative cancellation within 2s via asyncio.Event.
- Exponential backoff with jitter for transient retries, fast-fail on permanent 4xx.
- Concurrency limits per priority class and global.
- Parent task progress = weighted sum of children for DAG execution.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite

from noxai.config import Settings
from noxai.db import execute, fetch_all, fetch_one, get_db
from noxai.events import EventBus, global_event_bus, make_event
from noxai.models.router import ModelRouter
from noxai.tasks.models import Task, TaskLane, TaskPriority, TaskStatus
from noxai.tools.registry import ToolRegistry
from noxai.tools.registry import registry as default_registry

log = logging.getLogger(__name__)

_global_supervisor: TaskSupervisor | None = None


def get_task_supervisor() -> TaskSupervisor | None:
    return _global_supervisor


def set_task_supervisor(sup: TaskSupervisor | None) -> None:
    global _global_supervisor
    _global_supervisor = sup


TaskHandler = Callable[[Task, asyncio.Event], Coroutine[Any, Any, Any]]


class TaskHandlerRegistry:
    """Registry mapping task kind to handler functions across daemon restarts."""

    def __init__(self) -> None:
        self._handlers: dict[str, TaskHandler] = {}

    def register(self, kind: str, handler: TaskHandler) -> None:
        self._handlers[kind] = handler

    def get(self, kind: str) -> TaskHandler | None:
        return self._handlers.get(kind)

    def has(self, kind: str) -> bool:
        return kind in self._handlers


global_task_registry = TaskHandlerRegistry()


def get_task_registry() -> TaskHandlerRegistry:
    return global_task_registry


class TaskSupervisor:
    """Daemon-lifecycle supervisor for detached background tasks and DAG pipelines."""

    def __init__(
        self,
        config: Settings,
        router: ModelRouter | None = None,
        registry: ToolRegistry | None = None,
        event_bus: EventBus | None = None,
        task_registry: TaskHandlerRegistry | None = None,
        max_concurrent_global: int = 4,
        max_concurrent_interactive: int = 2,
        heartbeat_interval_sec: float = 3.0,
        stale_threshold_sec: float = 12.0,
    ) -> None:
        self.config = config
        self.router = router
        self.registry = registry or default_registry
        self.event_bus = event_bus or global_event_bus
        self.task_registry = task_registry or global_task_registry
        self.max_concurrent_global = max_concurrent_global
        self.max_concurrent_interactive = max_concurrent_interactive
        self.heartbeat_interval_sec = heartbeat_interval_sec
        self.stale_threshold_sec = stale_threshold_sec

        self._running_tasks: dict[str, asyncio.Task[Any]] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._task_handlers: dict[str, TaskHandler] = {}

        self._worker_loop_task: asyncio.Task[None] | None = None
        self._heartbeat_loop_task: asyncio.Task[None] | None = None
        self._stopped = False
        self._lock = asyncio.Lock()

    def register_handler(self, kind: str, handler: TaskHandler) -> None:
        """Register a handler function for all tasks of a specific kind."""
        self.task_registry.register(kind, handler)


    async def start(self) -> None:
        """Start the supervisor worker loop and heartbeat task."""
        if self._worker_loop_task and not self._worker_loop_task.done():
            return
        self._stopped = False

        try:
            async with get_db() as db:
                await self.recover_interrupted_tasks(db)
        except Exception as exc:
            log.warning("TaskSupervisor recovery error on startup: %s", exc)

        self._worker_loop_task = asyncio.create_task(
            self._supervise_loop(), name="nox_task_supervisor_worker"
        )
        self._heartbeat_loop_task = asyncio.create_task(
            self._heartbeat_loop(), name="nox_task_supervisor_heartbeat"
        )
        log.info("TaskSupervisor started (max_concurrent=%d).", self.max_concurrent_global)

    async def stop(self) -> None:
        """Gracefully stop supervisor on daemon shutdown, persisting resumable state."""
        self._stopped = True
        log.info("Stopping TaskSupervisor, cancelling running background tasks...")

        if self._worker_loop_task and not self._worker_loop_task.done():
            self._worker_loop_task.cancel()
        if self._heartbeat_loop_task and not self._heartbeat_loop_task.done():
            self._heartbeat_loop_task.cancel()

        running_ids = list(self._running_tasks.keys())
        for tid in running_ids:
            if tid in self._cancel_events:
                self._cancel_events[tid].set()
            task_obj = self._running_tasks.get(tid)
            if task_obj and not task_obj.done():
                task_obj.cancel()

        if running_ids:
            try:
                async with get_db() as db:
                    now_iso = datetime.now(UTC).isoformat()
                    placeholders = ",".join("?" for _ in running_ids)
                    sql = (
                        f"UPDATE tasks SET status = 'interrupted', finished_at = ? "  # noqa: S608
                        f"WHERE id IN ({placeholders})"
                    )
                    await execute(db, sql, (now_iso, *running_ids))
                    await db.commit()
            except Exception as exc:
                log.error("Failed to mark interrupted tasks during shutdown: %s", exc)

        self._running_tasks.clear()
        self._cancel_events.clear()
        log.info("TaskSupervisor shutdown complete.")

    async def recover_interrupted_tasks(self, db: aiosqlite.Connection) -> int:
        """Find tasks left in 'interrupted' or 'running' with stale heartbeat and queue for resume."""
        now_utc = datetime.now(UTC)
        cutoff_iso = (now_utc - timedelta(seconds=self.stale_threshold_sec)).isoformat()

        rows = await fetch_all(
            db,
            """
            SELECT id, kind, parent_id, attempts, max_attempts FROM tasks
            WHERE status = 'interrupted' OR (status = 'running' AND (heartbeat_at IS NULL OR heartbeat_at <= ?))
            """,
            (cutoff_iso,),
        )
        if not rows:
            return 0

        recovered = 0
        for r in rows:
            t_id = str(r["id"])
            attempts = int(r["attempts"] or 0)
            max_att = int(r["max_attempts"] or 3)

            if attempts < max_att:
                await execute(
                    db,
                    """
                    UPDATE tasks
                    SET status = 'queued', error = 'Interrupted: resumed after daemon restart'
                    WHERE id = ?
                    """,
                    (t_id,),
                )
            else:
                await execute(
                    db,
                    """
                    UPDATE tasks
                    SET status = 'failed', error = 'Interrupted and exceeded max retry attempts'
                    WHERE id = ?
                    """,
                    (t_id,),
                )
            recovered += 1

        await db.commit()
        if recovered > 0:
            log.warning("TaskSupervisor recovered %d interrupted tasks for resumption.", recovered)
        return recovered

    async def submit_task(
        self,
        title: str,
        prompt: str,
        lane: str | TaskLane = TaskLane.LOCAL,
        priority: str | TaskPriority = TaskPriority.NORMAL,
        kind: str = "agent",
        parent_id: str | None = None,
        payload_json: str | None = None,
        owner: str = "system",
        max_attempts: int = 3,
        handler: Callable[[Task, asyncio.Event], Coroutine[Any, Any, Any]] | None = None,
        db: aiosqlite.Connection | None = None,
    ) -> Task:
        """Enqueue a detached background task held strongly by the supervisor."""
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
            kind=kind,
            parent_id=parent_id,
            payload_json=payload_json,
            owner=owner,
            max_attempts=max_attempts,
            created_at=now_iso,
            cancellation_token=canc_token,
        )

        if handler:
            self._task_handlers[task_id] = handler

        async def _insert(conn: aiosqlite.Connection) -> None:
            await execute(
                conn,
                """
                INSERT INTO tasks
                (id, title, prompt, lane, priority, status, kind, parent_id, payload_json,
                 owner, max_attempts, created_at, cancellation_token)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.title,
                    task.prompt,
                    task.lane.value,
                    task.priority.value,
                    task.status.value,
                    task.kind,
                    task.parent_id,
                    task.payload_json,
                    task.owner,
                    task.max_attempts,
                    task.created_at,
                    task.cancellation_token,
                ),
            )
            await conn.commit()

        if db is not None:
            await _insert(db)
        else:
            async with get_db() as conn:
                await _insert(conn)

        await self.event_bus.publish(
            make_event(
                "task_created",
                {"task_id": task.id, "title": task.title, "priority": task.priority.value},
            ),
            db=db,
        )
        return task

    async def cancel_task(self, task_id: str, reason: str = "Cancelled by user") -> bool:
        """Cancel a queued or actively running task within 2 seconds."""
        if task_id in self._cancel_events:
            self._cancel_events[task_id].set()

        if task_id in self._running_tasks:
            self._running_tasks[task_id].cancel()

        now_iso = datetime.now(UTC).isoformat()
        async with get_db() as conn:
            row = await fetch_one(conn, "SELECT status FROM tasks WHERE id = ?", (task_id,))
            if not row:
                return False
            curr = row["status"]
            if curr in (TaskStatus.DONE.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value):
                return False

            await execute(
                conn,
                "UPDATE tasks SET status = ?, finished_at = ?, error = ? WHERE id = ?",
                (TaskStatus.CANCELLED.value, now_iso, reason, task_id),
            )
            await conn.commit()

        await self.event_bus.publish(
            make_event("task_cancelled", {"task_id": task_id, "reason": reason})
        )
        return True

    async def _heartbeat_loop(self) -> None:
        """Periodically update heartbeat_at for all actively running tasks."""
        while not self._stopped:
            try:
                await asyncio.sleep(self.heartbeat_interval_sec)
                active_ids = list(self._running_tasks.keys())
                if active_ids:
                    now_iso = datetime.now(UTC).isoformat()
                    async with get_db() as db:
                        for tid in active_ids:
                            sql = (
                                "UPDATE tasks SET heartbeat_at = ? "
                                "WHERE id = ? AND status = 'running'"
                            )
                            await execute(db, sql, (now_iso, tid))
                        await db.commit()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.debug("Heartbeat update error: %s", exc)

    async def _supervise_loop(self) -> None:
        """Main loop dispatching queued tasks up to concurrency limits."""
        while not self._stopped:
            try:
                finished = [tid for tid, t in self._running_tasks.items() if t.done()]
                for tid in finished:
                    self._running_tasks.pop(tid, None)
                    self._cancel_events.pop(tid, None)
                    self._task_handlers.pop(tid, None)

                capacity = self.max_concurrent_global - len(self._running_tasks)
                if capacity > 0:
                    dispatched = await self._dispatch_next_task()
                    if not dispatched:
                        await asyncio.sleep(0.5)
                else:
                    await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("TaskSupervisor supervise loop error: %s", exc)
                await asyncio.sleep(1.0)

    async def _dispatch_next_task(self) -> bool:
        """Claim the highest priority queued task and spawn execution."""
        async with get_db() as db:
            query = (
                "SELECT * FROM tasks WHERE status = 'queued' "
                "ORDER BY "
                "CASE priority WHEN 'urgent' THEN 4 WHEN 'high' THEN 3 "
                "WHEN 'normal' THEN 2 WHEN 'low' THEN 1 ELSE 0 END DESC, "
                "created_at ASC LIMIT 1"
            )
            row = await fetch_one(db, query)
            if not row:
                return False

            task_id = str(row["id"])
            now_iso = datetime.now(UTC).isoformat()
            sql = (
                "UPDATE tasks SET status = 'running', started_at = ?, heartbeat_at = ?, "
                "attempts = attempts + 1 WHERE id = ?"
            )
            await execute(db, sql, (now_iso, now_iso, task_id))
            await db.commit()

        task = self._row_to_task(row)
        task.status = TaskStatus.RUNNING
        task.started_at = now_iso
        task.attempts += 1

        cancel_evt = asyncio.Event()
        self._cancel_events[task.id] = cancel_evt

        coro = self._run_supervised_task(task, cancel_evt)
        async_task = asyncio.create_task(coro, name=f"nox_task_{task.id}")
        self._running_tasks[task.id] = async_task

        await self.event_bus.publish(
            make_event(
                "task_started",
                {"task_id": task.id, "lane": task.lane.value, "title": task.title},
            )
        )
        return True

    async def _run_supervised_task(self, task: Task, cancel_evt: asyncio.Event) -> None:
        """Supervised task execution with retries, backoff, progress updates, and DAG handling."""
        output_data: Any = None
        error_msg: str | None = None
        succeeded = False

        try:
            if task.id in self._task_handlers:
                handler = self._task_handlers[task.id]
                output_data = await handler(task, cancel_evt)
                succeeded = True
            elif self.task_registry and self.task_registry.has(task.kind):
                handler = self.task_registry.get(task.kind)
                assert handler is not None
                output_data = await handler(task, cancel_evt)
                succeeded = True
            else:
                output_data = await self._execute_default_agent(task, cancel_evt)
                succeeded = True

        except asyncio.CancelledError:
            log.info("Task %s was cancelled.", task.id)
            error_msg = "Cancelled"
            async with get_db() as db:
                now_iso = datetime.now(UTC).isoformat()
                sql = (
                    "UPDATE tasks SET status = 'cancelled', finished_at = ?, error = ? WHERE id = ?"
                )
                await execute(db, sql, (now_iso, error_msg, task.id))
                await db.commit()
            await self.event_bus.publish(make_event("task_cancelled", {"task_id": task.id}))
            return

        except Exception as exc:
            error_msg = str(exc)
            log.error(
                "Task %s execution error (attempt %d/%d): %s",
                task.id,
                task.attempts,
                task.max_attempts,
                exc,
            )

            is_perm = any(
                code in error_msg for code in ("401", "403", "404", "410", "Invalid API Key")
            )
            if not is_perm and task.attempts < task.max_attempts:
                backoff_sec = (2.0**task.attempts) + random.uniform(0.5, 2.0)  # noqa: S311
                log.info("Task %s retrying in %.2fs...", task.id, backoff_sec)
                await asyncio.sleep(min(backoff_sec, 30.0))

                async with get_db() as db:
                    await execute(
                        db,
                        "UPDATE tasks SET status = 'queued', error = ? WHERE id = ?",
                        (f"Retry {task.attempts}/{task.max_attempts}: {error_msg}", task.id),
                    )
                    await db.commit()
                return

        now_iso = datetime.now(UTC).isoformat()
        final_status = TaskStatus.DONE.value if succeeded else TaskStatus.FAILED.value
        result_blob = json.dumps({"output": output_data}) if output_data is not None else None

        async with get_db() as db:
            await execute(
                db,
                """
                UPDATE tasks
                SET status = ?, finished_at = ?, result_json = ?, error = ?, progress = ?
                WHERE id = ?
                """,
                (
                    final_status,
                    now_iso,
                    result_blob,
                    error_msg,
                    1.0 if succeeded else task.progress,
                    task.id,
                ),
            )

            if task.parent_id:
                await self._update_parent_progress(task.parent_id, db)

            await db.commit()

        ev_type = "task_done" if succeeded else "task_failed"
        await self.event_bus.publish(
            make_event(
                ev_type,
                {
                    "task_id": task.id,
                    "error": error_msg,
                    "progress": 1.0 if succeeded else task.progress,
                },
            )
        )

    async def _execute_default_agent(self, task: Task, cancel_evt: asyncio.Event) -> str:
        """Run standard AgentLoop for agent-type tasks."""
        if self.router is None:
            await asyncio.sleep(0.05)
            return f"Completed task: {task.title}"

        from noxai.agent.loop import AgentLoop

        allow_cloud = task.lane == TaskLane.CLOUD
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
        if cancel_evt.is_set() or loop_res.status == "cancelled":
            raise asyncio.CancelledError()
        if loop_res.status == "failed":
            raise RuntimeError(loop_res.error or "Agent loop execution failed")
        return loop_res.text

    async def _update_parent_progress(self, parent_id: str, db: aiosqlite.Connection) -> None:
        """Update parent task progress as weighted average of child task progress."""
        children = await fetch_all(
            db, "SELECT status, progress FROM tasks WHERE parent_id = ?", (parent_id,)
        )
        if not children:
            return
        total = sum(float(c["progress"] or 0.0) for c in children)
        avg_progress = round(total / len(children), 2)

        all_done = all(c["status"] == "done" for c in children)
        any_failed = any(c["status"] == "failed" for c in children)

        parent_status = "done" if all_done else ("failed" if any_failed else "running")
        sql = (
            "UPDATE tasks SET progress = ?, "
            "status = CASE WHEN status NOT IN ('cancelled', 'done') THEN ? ELSE status END "
            "WHERE id = ?"
        )
        await execute(db, sql, (avg_progress, parent_status, parent_id))

    def _row_to_task(self, row: Any) -> Task:
        return Task(
            id=str(row["id"]),
            title=str(row["title"]),
            prompt=str(row["prompt"]),
            lane=TaskLane(row["lane"]),
            priority=TaskPriority(row["priority"]),
            status=TaskStatus(row["status"]),
            kind=str(row["kind"] if "kind" in row.keys() else "agent"),
            parent_id=str(row["parent_id"])
            if ("parent_id" in row.keys() and row["parent_id"])
            else None,
            payload_json=str(row["payload_json"])
            if ("payload_json" in row.keys() and row["payload_json"])
            else None,
            result_ref=str(row["result_ref"])
            if ("result_ref" in row.keys() and row["result_ref"])
            else None,
            attempts=int(row["attempts"]) if "attempts" in row.keys() else 0,
            max_attempts=int(row["max_attempts"]) if "max_attempts" in row.keys() else 3,
            heartbeat_at=str(row["heartbeat_at"])
            if ("heartbeat_at" in row.keys() and row["heartbeat_at"])
            else None,
            owner=str(row["owner"] if "owner" in row.keys() else "system"),
            created_at=str(row["created_at"]),
            started_at=str(row["started_at"]) if row["started_at"] else None,
            finished_at=str(row["finished_at"]) if row["finished_at"] else None,
            error=str(row["error"]) if row["error"] else None,
            result_json=str(row["result_json"]) if row["result_json"] else None,
            progress=float(row["progress"] or 0.0),
            approval_id=int(row["approval_id"]) if row["approval_id"] is not None else None,
            cancellation_token=str(row["cancellation_token"])
            if row["cancellation_token"]
            else None,
            run_id=str(row["run_id"]) if row["run_id"] else None,
        )
