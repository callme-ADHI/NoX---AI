"""Data models and enums for the NoX Task Center (§1.4, §G3)."""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(enum.StrEnum):
    """Lifecycle states of a background task."""

    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_APPROVAL = "waiting_approval"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskLane(enum.StrEnum):
    """Execution lane for a background task."""

    LOCAL = "local"
    CLOUD = "cloud"
    SCHEDULER = "scheduler"


class TaskPriority(enum.StrEnum):
    """Priority levels for background tasks."""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"

    @property
    def rank(self) -> int:
        ranks = {
            TaskPriority.LOW: 1,
            TaskPriority.NORMAL: 2,
            TaskPriority.HIGH: 3,
            TaskPriority.URGENT: 4,
        }
        return ranks.get(self, 2)


class Task(BaseModel):
    """Representation of an asynchronous background task in the Task Center."""

    id: str = Field(..., description="Unique task identifier (e.g. task_<hex>).")
    title: str = Field(..., description="Short human-readable title.")
    prompt: str = Field(..., description="Instructions / prompt for the task worker.")
    lane: TaskLane = Field(default=TaskLane.LOCAL, description="Execution lane.")
    priority: TaskPriority = Field(default=TaskPriority.NORMAL, description="Execution priority.")
    status: TaskStatus = Field(default=TaskStatus.QUEUED, description="Current lifecycle state.")
    created_at: str = Field(..., description="UTC ISO timestamp of creation.")
    started_at: str | None = Field(default=None, description="UTC ISO timestamp of start.")
    finished_at: str | None = Field(default=None, description="UTC ISO timestamp of completion.")
    error: str | None = Field(default=None, description="Failure reason if failed.")
    result_json: str | None = Field(default=None, description="JSON string output of the task.")
    progress: float = Field(default=0.0, description="Completion progress from 0.0 to 1.0.")
    approval_id: int | None = Field(default=None, description="Linked pending approval if parked.")
    cancellation_token: str | None = Field(
        default=None, description="Unique cancellation token for this task run."
    )
    run_id: str | None = Field(default=None, description="Linked agent run identifier.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "prompt": self.prompt,
            "lane": self.lane.value,
            "priority": self.priority.value,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "result_json": self.result_json,
            "progress": round(self.progress, 2),
            "approval_id": self.approval_id,
            "cancellation_token": self.cancellation_token,
            "run_id": self.run_id,
        }
