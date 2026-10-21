"""Front desk dispatch tools: background tasks, reminders, job edits, approvals (§1.4, §G3)."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from noxai.db import execute
from noxai.scheduler.time_utils import (
    DEFAULT_TIMEZONE,
    format_human_time,
    parse_natural_time,
)
from noxai.tools.builtin.scheduler_tools import resolve_schedule_expression
from noxai.tools.registry import ToolContext, ToolResult, record_pending_approval, tool

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# dispatch_task
# ---------------------------------------------------------------------------


class DispatchTaskParams(BaseModel):
    model_config = {"extra": "forbid"}

    title: str = Field(..., description="Short title describing the task to execute.", min_length=1)
    prompt: str = Field(
        ..., description="Detailed instructions or prompt for the background task.", min_length=1
    )
    lane: str = Field(
        default="local",
        description="Execution lane for the task: 'local', 'cloud', or 'scheduler'.",
    )
    priority: str = Field(
        default="normal",
        description="Priority level: 'low', 'normal', or 'high'.",
    )


@tool(
    name="dispatch_task",
    description="Dispatch a background task to the Task Center for execution.",
    params=DispatchTaskParams,
    tier="write",
    is_meta_tool=True,
)
async def dispatch_task(params: DispatchTaskParams, ctx: ToolContext) -> ToolResult:
    lane = params.lane.lower().strip()
    if lane not in ("local", "cloud", "scheduler"):
        lane = "local"

    priority = params.priority.lower().strip()
    if priority not in ("low", "normal", "high"):
        priority = "normal"

    task_id = f"task_{uuid.uuid4().hex[:12]}"

    if ctx.db is not None:
        try:
            # If tasks table exists (Phase F5), record it
            await execute(
                ctx.db,
                """
                INSERT INTO tasks (id, title, prompt, lane, priority, status, created_at)
                VALUES (?, ?, ?, ?, ?, 'queued', datetime('now'))
                """,
                (task_id, params.title, params.prompt, lane, priority),
            )
            await ctx.db.commit()
        except Exception as exc:
            log.debug("Tasks table not available or insert skipped: %s", exc)

    if ctx.emit:
        ctx.emit(
            {
                "type": "task_dispatched",
                "task_id": task_id,
                "title": params.title,
                "prompt": params.prompt,
                "lane": lane,
                "priority": priority,
            }
        )

    output = (
        f"Dispatched background task '{params.title}' "
        f"(id={task_id}, lane={lane}, priority={priority}). "
        "The Task Center will execute this asynchronously."
    )
    return ToolResult(
        ok=True,
        output=output,
        summary=f"Dispatched task: {params.title}",
    )


# ---------------------------------------------------------------------------
# dispatch_reminder
# ---------------------------------------------------------------------------


class DispatchReminderParams(BaseModel):
    model_config = {"extra": "forbid"}

    reminder_text: str = Field(..., description="The reminder message to display.", min_length=1)
    delay_seconds: int | None = Field(
        default=None, description="Delay in seconds before triggering.", ge=1
    )
    due_at: str | None = Field(
        default=None, description="Natural language or ISO time when reminder is due."
    )


@tool(
    name="dispatch_reminder",
    description="Schedule a reminder for a future time or after a delay.",
    params=DispatchReminderParams,
    tier="write",
    is_meta_tool=True,
)
async def dispatch_reminder(params: DispatchReminderParams, ctx: ToolContext) -> ToolResult:
    now_utc = datetime.now(UTC)
    target_dt: datetime | None = None
    human_str = ""

    if params.delay_seconds is not None:
        target_dt = now_utc + timedelta(seconds=params.delay_seconds)
        human_str = f"in {params.delay_seconds} seconds"
    elif params.due_at:
        target_dt = parse_natural_time(params.due_at, tz_name=DEFAULT_TIMEZONE)
        if target_dt is None:
            try:
                target_dt = datetime.fromisoformat(params.due_at)
                if target_dt.tzinfo is None:
                    target_dt = target_dt.replace(tzinfo=UTC)
            except Exception:
                target_dt = None

        if target_dt is None:
            return ToolResult(
                ok=False,
                output=(
                    f"Could not parse due time '{params.due_at}'. Please specify a clearer time."
                ),
                summary="Invalid due time",
            )
        human_str = format_human_time(target_dt, tz_name=DEFAULT_TIMEZONE)
    else:
        # Default to 5 minutes
        target_dt = now_utc + timedelta(minutes=5)
        human_str = "in 5 minutes"

    job_id = f"rem_{uuid.uuid4().hex[:10]}"
    job_name = f"reminder-{job_id}"
    iso_time = target_dt.isoformat()

    if ctx.db is not None:
        cfg = {
            "kind": "reminder",
            "task_type": "reminder",
            "message": params.reminder_text,
            "delivery": ["ui", "desktop"],
            "catchup": "once",
        }
        await execute(
            ctx.db,
            """
            INSERT INTO jobs (
                id, name, description, schedule_type, schedule_expr, timezone,
                instruction, config_json, enabled, created_by, version, next_run_at
            )
            VALUES (?, ?, ?, 'once', ?, ?, ?, ?, 1, 'agent', 1, ?)
            """,
            (
                job_id,
                job_name,
                f"Reminder: {params.reminder_text[:50]}",
                iso_time,
                DEFAULT_TIMEZONE,
                params.reminder_text,
                json.dumps(cfg),
                iso_time,
            ),
        )
        await ctx.db.commit()

    if ctx.emit:
        ctx.emit(
            {
                "type": "reminder_dispatched",
                "job_id": job_id,
                "reminder_text": params.reminder_text,
                "due_at": iso_time,
            }
        )

    output = f"Reminder set for {human_str}: '{params.reminder_text}' (ID: {job_id})"
    return ToolResult(
        ok=True,
        output=output,
        summary=f"Reminder set for {human_str} (ID: {job_id})",
    )


# ---------------------------------------------------------------------------
# dispatch_job_edit
# ---------------------------------------------------------------------------


class DispatchJobEditParams(BaseModel):
    model_config = {"extra": "forbid"}

    job_id: str = Field(
        ..., description="ID or name of the scheduled job to modify.", min_length=1
    )
    action: str = Field(
        ...,
        description="Action to perform: 'pause', 'resume', 'cancel', 'delete', or 'edit'.",
    )
    new_schedule: str | None = Field(
        default=None, description="New schedule expression if editing/rescheduling."
    )


@tool(
    name="dispatch_job_edit",
    description="Modify, pause, resume, edit, or cancel a scheduled job or reminder.",
    params=DispatchJobEditParams,
    tier="write",
    is_meta_tool=True,
)
async def dispatch_job_edit(params: DispatchJobEditParams, ctx: ToolContext) -> ToolResult:
    import re

    raw_id = params.job_id.strip()
    clean_id = re.sub(r"^[\(\[\{\s]*(?:id\s*:\s*)?", "", raw_id, flags=re.IGNORECASE)
    clean_id = re.sub(r"[\)\]\}\s\"',;:]+$", "", clean_id).strip()

    act = params.action.lower().strip()
    valid_actions = (
        "pause",
        "disable",
        "resume",
        "enable",
        "cancel",
        "delete",
        "remove",
        "edit",
        "reschedule",
        "update",
    )
    if act not in valid_actions:
        return ToolResult(
            ok=False,
            output=(
                f"Unknown job action '{params.action}'. "
                "Valid actions: pause, resume, cancel, delete, edit."
            ),
            summary="Unknown job action",
        )

    out_msg = ""
    if act in ("pause", "disable"):
        if ctx.db is not None:
            await execute(
                ctx.db,
                "UPDATE jobs SET enabled = 0 WHERE id = ? OR name = ? OR id = ? OR name = ?",
                (clean_id, clean_id, f"rem_{clean_id}", f"reminder-{clean_id}"),
            )
            await ctx.db.commit()
        out_msg = f"Job '{clean_id}' has been paused/disabled."
    elif act in ("resume", "enable"):
        if ctx.db is not None:
            await execute(
                ctx.db,
                "UPDATE jobs SET enabled = 1 WHERE id = ? OR name = ?",
                (params.job_id, params.job_id),
            )
            await ctx.db.commit()
        out_msg = f"Job '{params.job_id}' has been resumed/enabled."
    elif act in ("cancel", "delete", "remove"):
        if ctx.db is not None:
            await execute(
                ctx.db,
                "DELETE FROM jobs WHERE id = ? OR name = ?",
                (params.job_id, params.job_id),
            )
            await ctx.db.commit()
        out_msg = f"Job '{params.job_id}' has been cancelled/deleted."
    elif act in ("edit", "reschedule", "update"):
        if not params.new_schedule:
            return ToolResult(
                ok=False,
                output="Field 'new_schedule' is required when editing or rescheduling a job.",
                summary="Missing new_schedule",
            )
        sched_type, expr, human = resolve_schedule_expression(params.new_schedule)
        if ctx.db is not None:
            await execute(
                ctx.db,
                "UPDATE jobs SET schedule_type = ?, schedule_expr = ? WHERE id = ? OR name = ?",
                (sched_type, expr, params.job_id, params.job_id),
            )
            await ctx.db.commit()
        out_msg = f"Job '{params.job_id}' rescheduled to '{params.new_schedule}' ({human})."

    if ctx.emit:
        ctx.emit(
            {
                "type": "job_edited",
                "job_id": params.job_id,
                "action": act,
                "new_schedule": params.new_schedule,
            }
        )

    return ToolResult(
        ok=True,
        output=out_msg,
        summary=f"Job {act}: {params.job_id}",
    )


# ---------------------------------------------------------------------------
# request_approval
# ---------------------------------------------------------------------------


class RequestApprovalParams(BaseModel):
    model_config = {"extra": "forbid"}

    action: str = Field(
        ..., description="Action or tool invocation requiring approval.", min_length=1
    )
    reason: str = Field(
        ..., description="Explanation of why owner approval is required.", min_length=1
    )
    details: dict[str, Any] = Field(
        default_factory=dict, description="Contextual parameters or payload for the action."
    )


@tool(
    name="request_approval",
    description="Request explicit owner approval for a sensitive or consequential action.",
    params=RequestApprovalParams,
    tier="write",
    is_meta_tool=True,
)
async def request_approval(params: RequestApprovalParams, ctx: ToolContext) -> ToolResult:
    if ctx.request_approval:
        approved = await ctx.request_approval(
            ctx.run_id,
            params.action,
            params.details,
            "dangerous",
            params.reason,
        )
        if not approved:
            return ToolResult(
                ok=False,
                output=f"Action '{params.action}' was denied by owner or timed out.",
                summary=f"Denied: {params.action}",
            )
        return ToolResult(
            ok=True,
            output=f"Approval granted by owner for action '{params.action}'.",
            summary=f"Approved: {params.action}",
        )

    # Persist pending approval in DB if connected (delegated to registry pipeline)
    approval_id = await record_pending_approval(
        db=ctx.db,
        tool_name=params.action,
        args=params.details,
        reason=params.reason,
        run_id=ctx.run_id,
    )

    if ctx.emit:
        ctx.emit(
            {
                "type": "approval_requested",
                "approval_id": approval_id,
                "action": params.action,
                "reason": params.reason,
                "details": params.details,
            }
        )

    output = (
        f"Action '{params.action}' has been submitted for owner approval "
        f"(approval_id={approval_id}): {params.reason}"
    )
    return ToolResult(
        ok=True,
        output=output,
        summary=f"Approval requested: {params.action}",
    )
