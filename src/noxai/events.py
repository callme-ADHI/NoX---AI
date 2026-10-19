"""Event model, event types, and asynchronous event bus with ring buffer replay."""

from __future__ import annotations

import asyncio
import datetime
import logging
import uuid
from collections import deque
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

import aiosqlite

log = logging.getLogger(__name__)

# Standard event types defined by Phase 1 spec Section 5 & 8
EVENT_TYPES = {
    "run_started",
    "token",
    "message_done",
    "tool_call",
    "approval_required",
    "approval_resolved",
    "tool_result",
    "plan_created",
    "node_started",
    "node_progress",
    "node_done",
    "node_failed",
    "synthesis_started",
    "model_used",
    "notice",
    "run_done",
    "run_failed",
    "run_cancelled",
    "job_changed",
    "reminder_fired",
    "briefing_ready",
    "trace",
}


@dataclass
class Event:
    """A single system or run event matching the WebSocket JSON schema."""

    id: str
    ts: str
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary matching WebSocket wire format."""
        return {
            "id": self.id,
            "ts": self.ts,
            "run_id": self.run_id,
            "type": self.type,
            "data": self.data,
        }


def make_event(
    event_type: str,
    data: dict[str, Any] | None = None,
    *,
    run_id: str | None = None,
) -> Event:
    """Create a new Event with auto-generated UUID and UTC ISO timestamp."""
    return Event(
        id=uuid.uuid4().hex,
        ts=datetime.datetime.now(datetime.UTC).isoformat(),
        type=event_type,
        data=data or {},
        run_id=run_id,
    )


EventListener = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus:
    """Asynchronous event bus supporting subscriptions and bounded replay ring buffers."""

    def __init__(self, replay_buffer_size: int = 200) -> None:
        self.replay_buffer_size = replay_buffer_size
        self._listeners: list[EventListener] = []
        self._run_buffers: dict[str, deque[Event]] = {}
        self._global_buffer: deque[Event] = deque(maxlen=replay_buffer_size)

    def subscribe(self, listener: EventListener) -> Callable[[], None]:
        """Register an async callback listener. Returns an unsubscribe function."""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    async def publish(
        self,
        event: Event,
        db: aiosqlite.Connection | None = None,
    ) -> None:
        """Publish an event to all subscribers and append to the ring buffer.

        Key events (run_started, run_done, run_failed, run_cancelled) also persist
        state to the database if a connection is provided.
        """
        # 1. Append to ring buffer
        if event.run_id:
            if event.run_id not in self._run_buffers:
                self._run_buffers[event.run_id] = deque(maxlen=self.replay_buffer_size)
            self._run_buffers[event.run_id].append(event)
        self._global_buffer.append(event)

        # 2. Persist key run state transitions to SQLite if db is provided
        if db and event.run_id:
            try:
                if event.type == "run_started":
                    await db.execute(
                        """
                        INSERT INTO runs (id, kind, status, started_at)
                        VALUES (?, 'chat', 'running', ?)
                        ON CONFLICT(id) DO UPDATE SET
                            status = 'running',
                            started_at = excluded.started_at
                        """,
                        (event.run_id, event.ts),
                    )
                    await db.commit()
                elif event.type == "run_done":
                    await db.execute(
                        """
                        UPDATE runs
                        SET status = 'done', finished_at = ?
                        WHERE id = ?
                        """,
                        (event.ts, event.run_id),
                    )
                    await db.commit()
                elif event.type == "run_failed":
                    error_msg = str(event.data.get("error", "Unknown error"))
                    await db.execute(
                        """
                        UPDATE runs
                        SET status = 'failed', finished_at = ?, error = ?
                        WHERE id = ?
                        """,
                        (event.ts, error_msg, event.run_id),
                    )
                    await db.commit()
                elif event.type == "run_cancelled":
                    await db.execute(
                        """
                        UPDATE runs
                        SET status = 'cancelled', finished_at = ?
                        WHERE id = ?
                        """,
                        (event.ts, event.run_id),
                    )
                    await db.commit()
            except Exception as exc:
                log.warning("Failed to persist run status from event %s: %s", event.type, exc)

        # 3. Notify subscribers concurrently
        if not self._listeners:
            return

        tasks = [
            asyncio.create_task(self._safe_dispatch(listener, event))
            for listener in self._listeners
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_dispatch(self, listener: EventListener, event: Event) -> None:
        try:
            await listener(event)
        except Exception as exc:
            log.error("Exception in event listener for %s: %s", event.type, exc, exc_info=True)

    def get_replay_events(self, run_id: str) -> list[Event]:
        """Return the buffered events for the specified run_id (up to replay_buffer_size)."""
        buffer = self._run_buffers.get(run_id)
        if not buffer:
            return []
        return list(buffer)

    def get_recent_events(self, limit: int = 200) -> list[Event]:
        """Return the most recent global events (up to limit)."""
        events = list(self._global_buffer)
        return events[-limit:]


# Global event bus singleton instance
global_event_bus = EventBus()
