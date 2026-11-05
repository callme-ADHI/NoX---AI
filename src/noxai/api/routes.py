"""REST API routes for NoX daemon per Section 6.1."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from noxai.api.auth import (
    check_gate_authenticated,
    verify_csrf,
    verify_session,
)
from noxai.config import Settings, get_config
from noxai.db import VECTOR_BACKEND, execute, fetch_all, fetch_one, get_db
from noxai.events import global_event_bus, make_event
from noxai.models.router import get_default_router
from noxai.tasks.center import TaskCenter
from noxai.tools.registry import (
    approve_approval,
    default_registry,
    deny_approval,
)

log = logging.getLogger(__name__)

api_router = APIRouter(prefix="/api")

# Dependency shortcuts for read-only and state-changing routes
READ_DEPS = [Depends(verify_session)]
WRITE_DEPS = [Depends(verify_session), Depends(verify_csrf)]

# Active running background tasks: run_id -> asyncio.Task
active_runs: dict[str, asyncio.Task[Any]] = {}


def _safe_json_loads(val: Any) -> Any:
    """Parse JSON string safely or return original value on decode error."""
    if not isinstance(val, str):
        return val
    try:
        return json.loads(val)
    except Exception:
        return val


# ---------------------------------------------------------------------------
# Request & Response Models (Explicit schemas per Spec §6.1 & §7)
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str
    allow_cloud: bool = True
    run_id: str | None = None


class ChatResponse(BaseModel):
    run_id: str
    conversation_id: str


class ApprovalDecision(BaseModel):
    decision: str = Field(..., description="'approve' or 'deny'")


class CreateTaskRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    prompt: str = Field(..., min_length=1)
    lane: str = Field(default="local")
    priority: str = Field(default="normal")
    run_id: str | None = None


class TaskResponse(BaseModel):
    id: str
    title: str
    prompt: str
    lane: str
    priority: str
    status: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    result_json: str | None = None
    progress: float = 0.0
    approval_id: int | None = None
    cancellation_token: str | None = None
    run_id: str | None = None


class BriefingFeedback(BaseModel):
    rating: int = Field(..., description="1 (thumbs up) or -1 (thumbs down)")
    note: str | None = None


class DetailedBriefingFeedback(BaseModel):
    rating: str = Field(..., description="'like' or 'dislike'")
    topic_id: str | None = None
    item_id: str | None = None
    reason_text: str | None = None
    tags: list[str] = Field(default_factory=list)


class OpportunityStatusRequest(BaseModel):
    canonical_url: str = Field(..., description="Canonical URL of the opportunity")
    status: str = Field(
        ...,
        description="'new', 'interested', 'applied', 'interview', 'offer', 'rejected', 'dismissed'",
    )


class RunBriefingRequest(BaseModel):
    slot: str | None = Field(
        default=None,
        description="'morning' or 'evening' (optional; defaults to current window)",
    )
    force: bool = False


class FactUpdate(BaseModel):
    value: str


class FactPinRequest(BaseModel):
    pinned: bool


class JobCreateRequest(BaseModel):
    name: str
    description: str = ""
    schedule_type: str = "cron"
    schedule_expr: str
    timezone: str = "Asia/Kolkata"
    instruction: str
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class JobUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    schedule_type: str | None = None
    schedule_expr: str | None = None
    timezone: str | None = None
    instruction: str | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class JobRestoreRequest(BaseModel):
    version: int | None = None


class SourceCreateRequest(BaseModel):
    name: str
    url: str
    kind: str = "rss"
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True


class SourceUpdateRequest(BaseModel):
    name: str | None = None
    url: str | None = None
    kind: str | None = None
    tags: list[str] | None = None
    enabled: bool | None = None


class StandardSuccessResponse(BaseModel):
    ok: bool = True


class SystemStatusResponse(BaseModel):
    status: str
    authenticated: bool
    gate_mode: str
    ollama_reachable: bool
    loaded_models: list[str]
    cloud_available: bool
    cloud_key_configured: bool
    cloud_enabled: bool = True
    active_runs_count: int
    db_size_bytes: int
    vector_backend: str
    cloud_health: dict[str, Any] | None = None
    cloud_budget: dict[str, Any] | None = None


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    taint_flags: int = 0
    created_at: str


class ConversationSummaryResponse(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class ConversationDetailResponse(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[MessageResponse] = Field(default_factory=list)


class JobResponse(BaseModel):
    id: str
    name: str
    description: str
    schedule_type: str
    schedule_expr: str
    timezone: str
    instruction: str
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool
    created_by: str
    version: int
    created_at: str
    updated_at: str
    last_run_at: str | None = None
    next_run_at: str | None = None


class JobRunResponse(BaseModel):
    id: str
    job_id: str
    started_at: str
    finished_at: str | None = None
    status: str
    task_run_id: str | None = None
    output_markdown: str | None = None
    error: str | None = None
    heartbeat_at: str | None = None
    current_step_index: int | None = None
    total_steps: int | None = None
    due_slot: str | None = None
    plan_version: int | None = None


class JobVersionResponse(BaseModel):
    id: int
    job_id: str
    version: int
    snapshot_json: str
    changed_by: str
    change_note: str
    created_at: str


class BriefingResponse(BaseModel):
    id: int
    job_run_id: str
    title: str
    markdown: str
    sources: list[Any] = Field(default_factory=list)
    feedback: int | None = None
    feedback_note: str | None = None
    created_at: str


class FactResponse(BaseModel):
    id: int
    key: str
    value: str
    category: str
    confidence: float
    source: str
    pinned: bool
    created_at: str
    updated_at: str
    last_used_at: str | None = None
    use_count: int = 0


class EpisodeResponse(BaseModel):
    id: int
    ts: str
    kind: str
    task_id: str | None = None
    summary: str
    importance: float
    archived_at: str | None = None


class MemoryFactResult(BaseModel):
    id: int = 0
    key: str
    value: str
    category: str = "other"
    confidence: float = 1.0
    pinned: bool = False
    score: float = 0.0


class MemoryEpisodeResult(BaseModel):
    id: int = 0
    summary: str
    importance: float = 0.5
    ts: str = ""
    score: float = 0.0


class MemorySearchResponse(BaseModel):
    query: str
    facts: list[MemoryFactResult] = Field(default_factory=list)
    episodes: list[MemoryEpisodeResult] = Field(default_factory=list)
    formatted: str = ""


class AuditLogResponse(BaseModel):
    id: int
    ts: str
    run_id: str | None = None
    tool: str
    args_json: str
    tier: str
    decision: str
    approver: str
    result_summary: str | None = None
    duration_ms: int | None = None
    ok: bool


class EgressRecordResponse(BaseModel):
    id: int
    timestamp: str
    provider: str
    model: str
    prompt_tokens_est: int
    data_class: str
    decision: str
    reason: str
    sha256_hash: str
    created_at: str


class SourceResponse(BaseModel):
    id: int
    name: str
    url: str
    kind: str
    tags: list[str] = Field(default_factory=list)
    enabled: bool
    last_ok_at: str | None = None
    last_error: str | None = None
    created_at: str


class ApprovalResponse(BaseModel):
    id: int
    tool_name: str
    args_json: str
    args_hash: str | None = None
    reason: str | None = None
    requested_at: str
    expires_at: str | None = None
    status: str
    decided_at: str | None = None
    decided_by: str | None = None
    run_id: str | None = None


# ---------------------------------------------------------------------------
# Status & Auth
# ---------------------------------------------------------------------------


@api_router.get("/status", response_model=SystemStatusResponse)
@api_router.get("/health", response_model=SystemStatusResponse)
async def get_system_status() -> SystemStatusResponse:
    """Retrieve daemon status, health checks, gate status, and loaded models."""
    cfg = get_config()
    authenticated = await check_gate_authenticated(cfg)

    # Check Ollama
    ollama_ok = False
    loaded_models: list[str] = []
    try:
        import httpx

        resp = httpx.get(f"{cfg.models.ollama.base_url}/api/tags", timeout=1.5)
        if resp.status_code == 200:
            ollama_ok = True
            models_data = resp.json().get("models", [])
            loaded_models = [m.get("name", "") for m in models_data]
    except Exception:
        ollama_ok = False

    # Check DB size
    from noxai.cli import DB_PATH

    db_size_bytes = DB_PATH.stat().st_size if DB_PATH.exists() else 0

    has_nvidia_key = bool(
        (cfg.nvidia_api_keys and len(cfg.nvidia_api_keys) > 0)
        or (cfg.nvidia_api_key and cfg.nvidia_api_key.get_secret_value())
    )
    from noxai.models.cloud_health import CloudHealthState, get_cloud_health

    ch = get_cloud_health(has_key=has_nvidia_key)
    if ch.secrets_path:
        ch.check_secrets_file()
    if has_nvidia_key and ch.state in (CloudHealthState.NO_KEY, CloudHealthState.OFFLINE):
        ch._transition(CloudHealthState.ACTIVE, "Ready")
    cloud_enabled = True
    try:
        async with get_db() as db:
            row = await fetch_one(db, "SELECT value FROM settings WHERE key = 'cloud.enabled'")
            if row:
                try:
                    cloud_enabled = json.loads(row["value"]) is True
                except Exception:
                    cloud_enabled = str(row["value"]).lower() in ("true", "1")
    except Exception as exc:
        log.debug("Failed to query cloud.enabled: %s", exc)

    cloud_is_available = has_nvidia_key and ch.is_active() and cloud_enabled

    return SystemStatusResponse(
        status="ok",
        authenticated=authenticated,
        gate_mode=cfg.gate.mode,
        ollama_reachable=ollama_ok,
        loaded_models=loaded_models,
        cloud_available=cloud_is_available,
        cloud_key_configured=has_nvidia_key,
        cloud_enabled=cloud_enabled,
        active_runs_count=len(active_runs),
        db_size_bytes=db_size_bytes,
        vector_backend=VECTOR_BACKEND,
        cloud_health=ch.to_dict(),
    )


# ---------------------------------------------------------------------------
# Chat & Runs
# ---------------------------------------------------------------------------


async def _run_chat_pipeline(
    run_id: str,
    conv_id: str,
    message: str,
    allow_cloud: bool,
    config: Settings,
) -> None:
    """Execute chat pipeline in background and publish events."""
    from noxai.orchestrator import orchestrate_chat_turn

    try:
        await orchestrate_chat_turn(
            message=message,
            conversation_id=conv_id,
            run_id=run_id,
            allow_cloud=allow_cloud,
            config=config,
        )
    except asyncio.CancelledError:
        log.info("Run %s was cancelled.", run_id)
        await global_event_bus.publish(make_event("run_cancelled", {}, run_id=run_id))
    except Exception as exc:
        log.error("Run %s failed with exception: %s", run_id, exc)
        await global_event_bus.publish(make_event("run_failed", {"error": str(exc)}, run_id=run_id))


@api_router.post("/chat", response_model=ChatResponse, dependencies=WRITE_DEPS)
async def chat_endpoint(req: ChatRequest) -> ChatResponse:
    """Start an agent run (simple or complex) and stream results via WebSocket."""
    conv_id = req.conversation_id or f"conv_{uuid.uuid4().hex[:12]}"
    run_id = req.run_id or f"run_{uuid.uuid4().hex[:12]}"

    cloud_enabled = True
    try:
        async with get_db() as db:
            row = await fetch_one(db, "SELECT value FROM settings WHERE key = 'cloud.enabled'")
            if row:
                try:
                    cloud_enabled = json.loads(row["value"]) is True
                except Exception:
                    cloud_enabled = str(row["value"]).lower() in ("true", "1")
    except Exception as exc:
        log.debug("Failed to query cloud.enabled in chat_endpoint: %s", exc)
    effective_allow_cloud = bool(req.allow_cloud and cloud_enabled)

    async with get_db() as db:
        await execute(
            db,
            """
            INSERT INTO conversations (id, title, created_at, updated_at)
            VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                          strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            ON CONFLICT(id) DO UPDATE SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            """,
            (conv_id, req.message[:40]),
        )
        await execute(
            db,
            """
            INSERT INTO runs (id, kind, conversation_id, status, allow_cloud, started_at)
            VALUES (?, 'chat', ?, 'running', ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            ON CONFLICT(id) DO UPDATE SET status = 'running'
            """,
            (run_id, conv_id, 1 if effective_allow_cloud else 0),
        )
        await db.commit()

    cfg = get_config()
    task = asyncio.create_task(
        _run_chat_pipeline(
            run_id=run_id,
            conv_id=conv_id,
            message=req.message,
            allow_cloud=effective_allow_cloud,
            config=cfg,
        )
    )
    active_runs[run_id] = task
    task.add_done_callback(lambda _: active_runs.pop(run_id, None))

    return ChatResponse(run_id=run_id, conversation_id=conv_id)


@api_router.post("/runs/{run_id}/cancel", dependencies=WRITE_DEPS)
async def cancel_run_endpoint(run_id: str) -> dict[str, str]:
    """Cancel a running task."""
    task = active_runs.get(run_id)
    if task and not task.done():
        task.cancel()
        return {"status": "cancelled", "run_id": run_id}

    async with get_db() as db:
        row = await fetch_one(db, "SELECT status FROM runs WHERE id = ?", (run_id,))
        if row and row["status"] == "running":
            await execute(
                db,
                "UPDATE runs SET status = 'cancelled', finished_at = datetime('now') WHERE id = ?",
                (run_id,),
            )
            await db.commit()
            return {"status": "cancelled", "run_id": run_id}

    return {"status": "not_running", "run_id": run_id}


@api_router.post("/approvals/{approval_id}", dependencies=WRITE_DEPS)
async def resolve_approval_endpoint(approval_id: int, body: ApprovalDecision) -> dict[str, bool]:
    """Approve or deny an approval request."""
    if body.decision == "approve":
        ok = await approve_approval(approval_id)
    elif body.decision == "deny":
        ok = await deny_approval(approval_id)
    else:
        raise HTTPException(status_code=400, detail="Decision must be 'approve' or 'deny'")

    # Unpark or cancel any task waiting for this approval in TaskCenter
    try:
        cfg = get_config()
        tc = TaskCenter(config=cfg)
        await tc.handle_approval_resolved(approval_id, approved=(body.decision == "approve"))
    except Exception as exc:
        log.warning("Failed to notify TaskCenter of approval %s: %s", approval_id, exc)

    await global_event_bus.publish(
        make_event(
            "approval_resolved",
            {"approval_id": approval_id, "decision": body.decision, "ok": ok},
        )
    )
    return {"ok": ok}


# ---------------------------------------------------------------------------
# Task Center (Background Tasks §1.4, §G3)
# ---------------------------------------------------------------------------


@api_router.get("/tasks", response_model=list[TaskResponse], dependencies=READ_DEPS)
async def list_tasks_endpoint(
    status: str | None = Query(None),
    lane: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> list[TaskResponse]:
    """List background tasks with optional filtering."""
    cfg = get_config()
    tc = TaskCenter(config=cfg)
    tasks = await tc.list_tasks(status=status, lane=lane, limit=limit)
    return [TaskResponse(**t.to_dict()) for t in tasks]


@api_router.post("/tasks", response_model=TaskResponse, dependencies=WRITE_DEPS)
async def create_task_endpoint(body: CreateTaskRequest) -> TaskResponse:
    """Create and enqueue a background task."""
    cfg = get_config()
    tc = TaskCenter(config=cfg)
    try:
        task = await tc.create_task(
            title=body.title,
            prompt=body.prompt,
            lane=body.lane,
            priority=body.priority,
            run_id=body.run_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return TaskResponse(**task.to_dict())


@api_router.get("/tasks/{task_id}", response_model=TaskResponse, dependencies=READ_DEPS)
async def get_task_endpoint(task_id: str) -> TaskResponse:
    """Retrieve details of a background task."""
    cfg = get_config()
    tc = TaskCenter(config=cfg)
    task = await tc.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return TaskResponse(**task.to_dict())


@api_router.post("/tasks/{task_id}/cancel", dependencies=WRITE_DEPS)
async def cancel_task_endpoint(task_id: str) -> dict[str, Any]:
    """Cancel a queued or running background task."""
    cfg = get_config()
    tc = TaskCenter(config=cfg)
    cancelled = await tc.cancel_task(task_id)
    if not cancelled:
        task = await tc.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
        return {"ok": False, "task_id": task_id, "status": task.status.value}
    return {"ok": True, "task_id": task_id, "status": "cancelled"}


@api_router.delete("/tasks/{task_id}", dependencies=WRITE_DEPS)
async def delete_task_endpoint(task_id: str, purge: bool = False) -> dict[str, Any]:
    """Delete a task (soft-delete with 30-day grace period, or purge)."""
    from noxai.jobs.deletion import delete_task

    try:
        return await delete_task(task_id, purge=purge)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err))


@api_router.post("/tasks/{task_id}/restore", dependencies=WRITE_DEPS)
async def restore_task_endpoint(task_id: str) -> dict[str, Any]:
    """Restore a soft-deleted task."""
    from noxai.jobs.deletion import restore_task

    try:
        return await restore_task(task_id)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err))


@api_router.post("/tasks/clear-finished", dependencies=WRITE_DEPS)
async def clear_finished_tasks_endpoint() -> dict[str, Any]:
    """Clear all finished tasks (done, failed, cancelled)."""
    from noxai.jobs.deletion import clear_finished_tasks

    return await clear_finished_tasks()


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


@api_router.get(
    "/conversations", response_model=list[ConversationSummaryResponse], dependencies=READ_DEPS
)
async def list_conversations(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[ConversationSummaryResponse]:
    """List all saved conversations."""
    async with get_db() as db:
        rows = await fetch_all(
            db,
            "SELECT id, title, created_at, updated_at FROM conversations "
            "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [
            ConversationSummaryResponse(
                id=str(r["id"]),
                title=str(r["title"]),
                created_at=str(r["created_at"]),
                updated_at=str(r["updated_at"]),
            )
            for r in rows
        ]


@api_router.get(
    "/conversations/{conv_id}", response_model=ConversationDetailResponse, dependencies=READ_DEPS
)
async def get_conversation(conv_id: str) -> ConversationDetailResponse:
    """Retrieve conversation details and messages."""
    async with get_db() as db:
        conv = await fetch_one(
            db,
            "SELECT id, title, created_at, updated_at FROM conversations WHERE id = ?",
            (conv_id,),
        )
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")

        msgs = await fetch_all(
            db,
            "SELECT id, role, content, taint_flags, created_at FROM messages "
            "WHERE conversation_id = ? ORDER BY created_at ASC",
            (conv_id,),
        )
        return ConversationDetailResponse(
            id=str(conv["id"]),
            title=str(conv["title"]),
            created_at=str(conv["created_at"]),
            updated_at=str(conv["updated_at"]),
            messages=[
                MessageResponse(
                    id=str(m["id"]),
                    role=str(m["role"]),
                    content=str(m["content"]),
                    taint_flags=int(m["taint_flags"] or 0),
                    created_at=str(m["created_at"]),
                )
                for m in msgs
            ],
        )


@api_router.delete("/conversations/{conv_id}", dependencies=WRITE_DEPS)
async def delete_conversation(conv_id: str) -> dict[str, bool]:
    """Delete a conversation and its messages."""
    async with get_db() as db:
        await execute(
            db,
            "UPDATE runs SET conversation_id = NULL WHERE conversation_id = ?",
            (conv_id,),
        )
        await execute(db, "DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
        await execute(db, "DELETE FROM conversations WHERE id = ?", (conv_id,))
        await db.commit()
    return {"ok": True}


@api_router.delete("/conversations", dependencies=WRITE_DEPS)
async def delete_all_conversations() -> dict[str, bool]:
    """Delete all conversations and their messages."""
    async with get_db() as db:
        await execute(db, "UPDATE runs SET conversation_id = NULL")
        await execute(db, "DELETE FROM messages")
        await execute(db, "DELETE FROM conversations")
        await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Jobs & Reminders
# ---------------------------------------------------------------------------


@api_router.get("/jobs", response_model=list[JobResponse], dependencies=READ_DEPS)
async def list_jobs() -> list[JobResponse]:
    """List scheduled jobs."""
    async with get_db() as db:
        rows = await fetch_all(
            db,
            """
            SELECT id, name, description, schedule_type, schedule_expr, timezone,
                   instruction, config_json, enabled, created_by, version,
                   created_at, updated_at, last_run_at, next_run_at
            FROM jobs WHERE deleted_at IS NULL ORDER BY name ASC
            """,
        )
        results: list[JobResponse] = []
        for r in rows:
            cfg = _safe_json_loads(r["config_json"])
            results.append(
                JobResponse(
                    id=str(r["id"]),
                    name=str(r["name"]),
                    description=str(r["description"]),
                    schedule_type=str(r["schedule_type"]),
                    schedule_expr=str(r["schedule_expr"]),
                    timezone=str(r["timezone"]),
                    instruction=str(r["instruction"]),
                    config=cfg if isinstance(cfg, dict) else {},
                    enabled=bool(r["enabled"]),
                    created_by=str(r["created_by"]),
                    version=int(r["version"] or 1),
                    created_at=str(r["created_at"]),
                    updated_at=str(r["updated_at"]),
                    last_run_at=str(r["last_run_at"]) if r["last_run_at"] else None,
                    next_run_at=str(r["next_run_at"]) if r["next_run_at"] else None,
                )
            )
        return results


@api_router.post("/jobs", dependencies=WRITE_DEPS)
async def create_job_endpoint(job: JobCreateRequest) -> dict[str, Any]:
    """Create a new scheduled job."""
    from noxai.scheduler.time_utils import compute_next_run

    job_id = f"job_{uuid.uuid4().hex[:12]}"
    next_dt = compute_next_run(job.schedule_type, job.schedule_expr, job.timezone)
    next_iso = next_dt.isoformat() if next_dt else None
    cfg_json = json.dumps(job.config)

    async with get_db() as db:
        await execute(
            db,
            """
            INSERT INTO jobs (
                id, name, description, schedule_type, schedule_expr, timezone,
                instruction, config_json, enabled, created_by, version, next_run_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'owner', 1, ?)
            """,
            (
                job_id,
                job.name,
                job.description,
                job.schedule_type,
                job.schedule_expr,
                job.timezone,
                job.instruction,
                cfg_json,
                1 if job.enabled else 0,
                next_iso,
            ),
        )
        snapshot = {
            "id": job_id,
            "name": job.name,
            "schedule_type": job.schedule_type,
            "schedule_expr": job.schedule_expr,
            "timezone": job.timezone,
            "instruction": job.instruction,
            "config_json": cfg_json,
            "enabled": 1 if job.enabled else 0,
            "version": 1,
        }
        await execute(
            db,
            """
            INSERT INTO job_versions (job_id, version, snapshot_json, changed_by, change_note)
            VALUES (?, 1, ?, 'owner', 'Created via API')
            """,
            (job_id, json.dumps(snapshot)),
        )
        await db.commit()

    return {"id": job_id, "name": job.name, "next_run_at": next_iso}


@api_router.get("/jobs/{job_id}", response_model=JobResponse, dependencies=READ_DEPS)
async def get_job_endpoint(job_id: str) -> JobResponse:
    """Get details for a specific job."""
    async with get_db() as db:
        r = await fetch_one(
            db,
            """
            SELECT id, name, description, schedule_type, schedule_expr, timezone,
                   instruction, config_json, enabled, created_by, version,
                   created_at, updated_at, last_run_at, next_run_at
            FROM jobs WHERE (id = ? OR name = ?) AND deleted_at IS NULL
            """,
            (job_id, job_id),
        )
        if not r:
            raise HTTPException(status_code=404, detail="Job not found")

        cfg = _safe_json_loads(r["config_json"])
        return JobResponse(
            id=str(r["id"]),
            name=str(r["name"]),
            description=str(r["description"]),
            schedule_type=str(r["schedule_type"]),
            schedule_expr=str(r["schedule_expr"]),
            timezone=str(r["timezone"]),
            instruction=str(r["instruction"]),
            config=cfg if isinstance(cfg, dict) else {},
            enabled=bool(r["enabled"]),
            created_by=str(r["created_by"]),
            version=int(r["version"] or 1),
            created_at=str(r["created_at"]),
            updated_at=str(r["updated_at"]),
            last_run_at=str(r["last_run_at"]) if r["last_run_at"] else None,
            next_run_at=str(r["next_run_at"]) if r["next_run_at"] else None,
        )


@api_router.patch("/jobs/{job_id}", dependencies=WRITE_DEPS)
async def update_job_endpoint(job_id: str, updates: JobUpdateRequest) -> dict[str, Any]:
    """Update properties of an existing job."""
    async with get_db() as db:
        row = await fetch_one(db, "SELECT * FROM jobs WHERE id = ? OR name = ?", (job_id, job_id))
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")

        real_id = str(row["id"])
        new_v = int(row["version"] or 1) + 1

        sets: list[str] = []
        vals: list[Any] = []

        if updates.name is not None:
            sets.append("name = ?")
            vals.append(updates.name)
        if updates.description is not None:
            sets.append("description = ?")
            vals.append(updates.description)
        if updates.schedule_type is not None:
            sets.append("schedule_type = ?")
            vals.append(updates.schedule_type)
        if updates.schedule_expr is not None:
            sets.append("schedule_expr = ?")
            vals.append(updates.schedule_expr)
        if updates.timezone is not None:
            sets.append("timezone = ?")
            vals.append(updates.timezone)
        if updates.schedule_expr is not None or updates.schedule_type is not None:
            eff_type = updates.schedule_type or str(row["schedule_type"])
            eff_expr = updates.schedule_expr or str(row["schedule_expr"])
            eff_tz = updates.timezone or str(row["timezone"] or "Asia/Kolkata")
            try:
                from noxai.scheduler.time_utils import compute_next_run

                next_dt = compute_next_run(eff_type, eff_expr, eff_tz)
                sets.append("next_run_at = ?")
                vals.append(next_dt.isoformat())
            except Exception:
                pass
        if updates.instruction is not None:
            sets.append("instruction = ?")
            vals.append(updates.instruction)
        if updates.enabled is not None:
            sets.append("enabled = ?")
            vals.append(1 if updates.enabled else 0)
        if updates.config is not None:
            sets.append("config_json = ?")
            vals.append(json.dumps(updates.config))

        if sets:
            sets.extend(["version = ?", "updated_at = datetime('now')"])
            vals.extend([new_v, real_id])
            await execute(db, f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", tuple(vals))  # noqa: S608

            updated = await fetch_one(
                db,
                """
                SELECT id, name, description, schedule_type, schedule_expr, timezone,
                       instruction, config_json, enabled, created_by, version
                FROM jobs WHERE id = ?
                """,
                (real_id,),
            )
            snapshot = dict(updated) if updated else {}
            await execute(
                db,
                """
                INSERT INTO job_versions (job_id, version, snapshot_json, changed_by, change_note)
                VALUES (?, ?, ?, 'owner', 'Updated via API')
                """,
                (real_id, new_v, json.dumps(snapshot)),
            )
            await db.commit()

        updated_row = await fetch_one(
            db,
            """
            SELECT id, name, description, schedule_type, schedule_expr, timezone,
                   instruction, config_json, enabled, created_by, version,
                   created_at, updated_at, last_run_at, next_run_at
            FROM jobs WHERE id = ?
            """,
            (real_id,),
        )
        return dict(updated_row) if updated_row else {}


@api_router.delete("/jobs/{job_id}", dependencies=WRITE_DEPS)
async def delete_job_endpoint(job_id: str, purge: bool = False) -> dict[str, Any]:
    """Delete a scheduled job (soft-delete with 30-day grace period, or purge)."""
    from noxai.jobs.deletion import delete_job

    try:
        return await delete_job(job_id, purge=purge)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err))


@api_router.post("/jobs/{job_id}/run", dependencies=WRITE_DEPS)
async def run_job_endpoint(job_id: str) -> dict[str, Any]:
    """Trigger immediate execution of a job."""
    from noxai.scheduler.scheduler import JobScheduler

    cfg = get_config()
    router = get_default_router(cfg)
    scheduler = JobScheduler(router=router, registry=default_registry, config=cfg)

    async with get_db() as db:
        res = await scheduler.run_job_now(job_id, db)
        return res


@api_router.get(
    "/jobs/{job_id}/versions", response_model=list[JobVersionResponse], dependencies=READ_DEPS
)
async def get_job_versions_endpoint(job_id: str) -> list[JobVersionResponse]:
    """Retrieve historical snapshots for a job."""
    async with get_db() as db:
        row = await fetch_one(db, "SELECT id FROM jobs WHERE id = ? OR name = ?", (job_id, job_id))
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")

        v_rows = await fetch_all(
            db,
            "SELECT id, job_id, version, snapshot_json, changed_by, "
            "change_note, created_at FROM job_versions "
            "WHERE job_id = ? ORDER BY version DESC",
            (str(row["id"]),),
        )
        return [
            JobVersionResponse(
                id=int(v["id"]),
                job_id=str(v["job_id"]),
                version=int(v["version"]),
                snapshot_json=str(v["snapshot_json"]),
                changed_by=str(v["changed_by"]),
                change_note=str(v["change_note"]),
                created_at=str(v["created_at"]),
            )
            for v in v_rows
        ]


@api_router.post("/jobs/{job_id}/restore", dependencies=WRITE_DEPS)
async def restore_job_endpoint(
    job_id: str, body: JobRestoreRequest | None = None
) -> dict[str, Any]:
    """Restore a job from soft-delete or restore a specific historical version snapshot."""
    if body and body.version is not None:
        async with get_db() as db:
            row = await fetch_one(
                db, "SELECT id FROM jobs WHERE id = ? OR name = ?", (job_id, job_id)
            )
            if not row:
                raise HTTPException(status_code=404, detail="Job not found")

            real_id = str(row["id"])
            v_row = await fetch_one(
                db,
                "SELECT snapshot_json FROM job_versions WHERE job_id = ? AND version = ?",
                (real_id, body.version),
            )
            if not v_row:
                raise HTTPException(status_code=404, detail=f"Version {body.version} not found")

            snap = _safe_json_loads(v_row["snapshot_json"])
            if not isinstance(snap, dict):
                snap = {}

            schedule_type = snap.get("schedule_type", "cron")
            schedule_expr = snap.get("schedule_expr", "0 0 * * *")
            timezone = snap.get("timezone", "Asia/Kolkata")
            instruction = snap.get("instruction", "")
            config_val = snap.get("config")
            config_json = (
                json.dumps(config_val)
                if isinstance(config_val, dict)
                else snap.get("config_json", "{}")
            )
            enabled = int(snap.get("enabled", 1))

            await execute(
                db,
                """
                UPDATE jobs SET
                    schedule_type = ?, schedule_expr = ?, timezone = ?,
                    instruction = ?, config_json = ?, enabled = ?,
                    version = ?, updated_at = datetime('now'), deleted_at = NULL
                WHERE id = ?
                """,
                (
                    schedule_type,
                    schedule_expr,
                    timezone,
                    instruction,
                    config_json,
                    enabled,
                    body.version,
                    real_id,
                ),
            )
            await db.commit()
            return {"ok": True, "restored_version": body.version}
    else:
        from noxai.jobs.deletion import restore_job

        try:
            return await restore_job(job_id)
        except ValueError as err:
            raise HTTPException(status_code=404, detail=str(err))


@api_router.get("/jobs/{job_id}/plan", dependencies=READ_DEPS)
async def get_job_plan_endpoint(job_id: str) -> dict[str, Any]:
    """Retrieve the latest plan for a job along with folder summaries."""
    async with get_db() as db:
        row = await fetch_one(
            db, "SELECT id, instruction FROM jobs WHERE id = ? OR name = ?", (job_id, job_id)
        )
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")
        real_id = str(row["id"])

        plan_row = await fetch_one(
            db,
            """
            SELECT version, steps_json, status, source, approved_at, approved_by, created_at
            FROM job_plans WHERE job_id = ? ORDER BY version DESC LIMIT 1
            """,
            (real_id,),
        )
        if not plan_row:
            return {"job_id": real_id, "has_plan": False}

        steps_data = _safe_json_loads(plan_row["steps_json"])

        from noxai.jobs.template import calculate_folder_summary

        folders_summary = []
        if isinstance(steps_data, list):
            seen_folders = set()
            for s in steps_data:
                target_folder = s.get("target_folder")
                if target_folder and target_folder not in seen_folders:
                    seen_folders.add(target_folder)
                    summary = calculate_folder_summary(target_folder)
                    folders_summary.append(summary)

        return {
            "job_id": real_id,
            "has_plan": True,
            "version": plan_row["version"],
            "status": plan_row["status"],
            "source": plan_row["source"],
            "approved_at": plan_row["approved_at"],
            "approved_by": plan_row["approved_by"],
            "created_at": plan_row["created_at"],
            "steps": steps_data,
            "folders": folders_summary,
        }


@api_router.post("/jobs/{job_id}/plan/generate", dependencies=WRITE_DEPS)
async def generate_job_plan_endpoint(job_id: str, force_template: bool = False) -> dict[str, Any]:
    """Generate a plan for a job using cloud API or deterministic fallback template."""
    async with get_db() as db:
        row = await fetch_one(
            db, "SELECT id, instruction FROM jobs WHERE id = ? OR name = ?", (job_id, job_id)
        )
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")
        real_id = str(row["id"])
        instruction = str(row["instruction"])

    from noxai.jobs.planner import generate_plan_for_job

    try:
        plan, version = await generate_plan_for_job(
            real_id, instruction, force_template=force_template
        )
        return {
            "ok": True,
            "job_id": real_id,
            "version": version,
            "steps": [s.model_dump() for s in plan.steps],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@api_router.post("/jobs/{job_id}/plan/approve", dependencies=WRITE_DEPS)
async def approve_job_plan_endpoint(job_id: str, version: int | None = None) -> dict[str, Any]:
    """Approve a draft plan for execution."""
    async with get_db() as db:
        row = await fetch_one(db, "SELECT id FROM jobs WHERE id = ? OR name = ?", (job_id, job_id))
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")
        real_id = str(row["id"])

    from noxai.jobs.planner import approve_job_plan

    try:
        app_version = await approve_job_plan(real_id, version=version)
        return {"ok": True, "job_id": real_id, "version": app_version, "status": "approved"}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@api_router.get("/jobs/{job_id}/runs/{run_id}/steps", dependencies=READ_DEPS)
async def get_job_run_steps_endpoint(job_id: str, run_id: str) -> list[dict[str, Any]]:
    """Get step-by-step execution details for a job run."""
    async with get_db() as db:
        rows = await fetch_all(
            db,
            """
            SELECT id, step_index, kind, label, state, output_json, error, duration_ms, started_at, finished_at
            FROM job_run_steps WHERE run_id = ? ORDER BY step_index ASC
            """,
            (run_id,),
        )
        return [
            {
                "id": r["id"],
                "step_index": r["step_index"],
                "kind": r["kind"],
                "label": r["label"],
                "state": r["state"],
                "output": _safe_json_loads(r["output_json"]),
                "error": r["error"],
                "duration_ms": r["duration_ms"],
                "started_at": r["started_at"],
                "finished_at": r["finished_at"],
            }
            for r in rows
        ]


@api_router.get("/jobs/{job_id}/runs", response_model=list[JobRunResponse], dependencies=READ_DEPS)
async def get_job_runs_endpoint(
    job_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[JobRunResponse]:
    """Get execution history for a job."""
    async with get_db() as db:
        row = await fetch_one(db, "SELECT id FROM jobs WHERE id = ? OR name = ?", (job_id, job_id))
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")

        runs = await fetch_all(
            db,
            """
            SELECT id, job_id, started_at, finished_at, status, task_run_id, output_markdown, error,
                   heartbeat_at, current_step_index, total_steps, due_slot, plan_version
            FROM job_runs WHERE job_id = ? ORDER BY started_at DESC LIMIT ? OFFSET ?
            """,
            (str(row["id"]), limit, offset),
        )
        return [
            JobRunResponse(
                id=str(r["id"]),
                job_id=str(r["job_id"]),
                started_at=str(r["started_at"]),
                finished_at=str(r["finished_at"]) if r["finished_at"] else None,
                status=str(r["status"]),
                task_run_id=str(r["task_run_id"]) if r["task_run_id"] else None,
                output_markdown=str(r["output_markdown"]) if r["output_markdown"] else None,
                error=str(r["error"]) if r["error"] else None,
                heartbeat_at=str(r["heartbeat_at"]) if r["heartbeat_at"] else None,
                current_step_index=int(r["current_step_index"])
                if r["current_step_index"] is not None
                else None,
                total_steps=int(r["total_steps"]) if r["total_steps"] is not None else None,
                due_slot=str(r["due_slot"]) if r["due_slot"] else None,
                plan_version=int(r["plan_version"]) if r["plan_version"] is not None else None,
            )
            for r in runs
        ]


# ---------------------------------------------------------------------------
# Briefings (Job Outputs)
# ---------------------------------------------------------------------------


@api_router.get("/briefings", response_model=list[BriefingResponse], dependencies=READ_DEPS)
async def list_briefings(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[BriefingResponse]:
    """List delivered briefings."""
    async with get_db() as db:
        rows = await fetch_all(
            db,
            """
            SELECT id, job_run_id, title, markdown, sources_json,
                   feedback, feedback_note, created_at
            FROM outputs ORDER BY created_at DESC LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        results: list[BriefingResponse] = []
        for r in rows:
            srcs = _safe_json_loads(r["sources_json"])
            results.append(
                BriefingResponse(
                    id=int(r["id"]),
                    job_run_id=str(r["job_run_id"]),
                    title=str(r["title"]),
                    markdown=str(r["markdown"]),
                    sources=srcs if isinstance(srcs, list) else [],
                    feedback=int(r["feedback"]) if r["feedback"] is not None else None,
                    feedback_note=str(r["feedback_note"]) if r["feedback_note"] else None,
                    created_at=str(r["created_at"]),
                )
            )
        return results


@api_router.post("/briefings/run", dependencies=WRITE_DEPS)
async def run_briefing_slot(request: Request, req: RunBriefingRequest | None = None) -> Any:
    """Trigger an on-demand briefing pipeline run through the TaskSupervisor."""
    from fastapi.responses import JSONResponse

    from noxai.briefing.scheduler import BriefingScheduler

    sched: BriefingScheduler | None = getattr(request.app.state, "briefing_scheduler", None)
    if not sched:
        from noxai.tasks.supervisor import get_task_supervisor

        sup = get_task_supervisor()
        sched = BriefingScheduler(supervisor=sup)

    slot_val = req.slot if req and req.slot in ("morning", "evening") else None
    res = await sched.manual_run(slot=slot_val)
    if res.get("active"):
        return JSONResponse(status_code=200, content=res)
    return JSONResponse(status_code=202, content=res)


@api_router.get("/briefings/status", dependencies=READ_DEPS)
async def get_briefing_status(request: Request) -> dict[str, Any]:
    """Retrieve live status, active progress, and next scheduled runs."""
    from noxai.briefing.scheduler import BriefingScheduler

    sched: BriefingScheduler | None = getattr(request.app.state, "briefing_scheduler", None)
    if not sched:
        from noxai.tasks.supervisor import get_task_supervisor

        sup = get_task_supervisor()
        sched = BriefingScheduler(supervisor=sup)

    return await sched.get_status()


@api_router.get("/briefing/active", dependencies=READ_DEPS)
@api_router.get("/briefings/active", dependencies=READ_DEPS)
async def get_briefing_active(request: Request) -> dict[str, Any]:
    """Retrieve active briefing run for UI attachment and progress tracking."""
    from noxai.briefing.scheduler import BriefingScheduler

    sched: BriefingScheduler | None = getattr(request.app.state, "briefing_scheduler", None)
    if not sched:
        from noxai.tasks.supervisor import get_task_supervisor

        sup = get_task_supervisor()
        sched = BriefingScheduler(supervisor=sup)

    st = await sched.get_status()
    if st.get("active") and st.get("active_run"):
        return {"active": True, "run": st["active_run"]}
    return {"active": False, "run": None}


@api_router.post("/briefings/runs/{run_id}/cancel", dependencies=WRITE_DEPS)
async def cancel_briefing_run_endpoint(run_id: str, request: Request) -> dict[str, Any]:
    """Explicitly cancel an active briefing run."""
    from noxai.briefing.scheduler import BriefingScheduler

    sched: BriefingScheduler | None = getattr(request.app.state, "briefing_scheduler", None)
    if not sched:
        from noxai.tasks.supervisor import get_task_supervisor

        sup = get_task_supervisor()
        sched = BriefingScheduler(supervisor=sup)

    ok = await sched.cancel_run(run_id)
    return {"ok": ok, "run_id": run_id}


@api_router.get("/briefings/{briefing_id}", response_model=BriefingResponse, dependencies=READ_DEPS)
async def get_briefing(briefing_id: int) -> BriefingResponse:
    """Read a full briefing document."""
    async with get_db() as db:
        row = await fetch_one(
            db,
            """
            SELECT id, job_run_id, title, markdown, sources_json,
                   feedback, feedback_note, created_at
            FROM outputs WHERE id = ?
            """,
            (briefing_id,),
        )
        if not row:
            raise HTTPException(status_code=404, detail="Briefing not found")

        srcs = _safe_json_loads(row["sources_json"])
        return BriefingResponse(
            id=int(row["id"]),
            job_run_id=str(row["job_run_id"]),
            title=str(row["title"]),
            markdown=str(row["markdown"]),
            sources=srcs if isinstance(srcs, list) else [],
            feedback=int(row["feedback"]) if row["feedback"] is not None else None,
            feedback_note=str(row["feedback_note"]) if row["feedback_note"] else None,
            created_at=str(row["created_at"]),
        )


@api_router.post("/briefings/{briefing_id}/feedback", dependencies=WRITE_DEPS)
async def rate_briefing(briefing_id: int, fb: BriefingFeedback) -> dict[str, bool]:
    """Submit rating and feedback for a briefing output."""
    async with get_db() as db:
        await execute(
            db,
            "UPDATE outputs SET feedback = ?, feedback_note = ? WHERE id = ?",
            (fb.rating, fb.note, briefing_id),
        )
        await db.commit()
    return {"ok": True}


@api_router.get("/briefings/{briefing_id}/items", dependencies=READ_DEPS)
async def get_briefing_items(briefing_id: str) -> list[dict[str, Any]]:
    """Retrieve structured briefing items with joined opportunity metadata."""
    async with get_db() as db:
        # Check if briefing_id matches outputs.id or briefing_runs.id
        row = await fetch_one(db, "SELECT job_run_id FROM outputs WHERE id = ?", (briefing_id,))
        run_id = row["job_run_id"] if row else briefing_id

        rows = await fetch_all(
            db,
            """
            SELECT
                bi.id, bi.briefing_id, bi.topic_id, bi.category,
                bi.title, bi.summary, bi.source_name, bi.url, bi.created_at,
                op.type AS kind, op.org AS organization, op.deadline,
                op.score,
                COALESCE(op.status, 'new') AS opportunity_status,
                op.raw_json
            FROM briefing_items bi
            LEFT JOIN opportunity_registry op ON op.canonical_url = bi.url
            WHERE bi.briefing_id = ?
            ORDER BY bi.created_at ASC
            """,
            (run_id,),
        )
        results: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            raw = _safe_json_loads(d.pop("raw_json", None))
            if isinstance(raw, dict):
                d["benefits"] = raw.get("benefits")
                d["eligibility"] = raw.get("eligibility")
                d["score_breakdown"] = raw.get("score_breakdown")
                d["source_evidence_snippet"] = raw.get("source_evidence_snippet")
                d["source_evidence_url"] = raw.get("source_evidence_url")
                d["country"] = raw.get("country")
                d["mode"] = raw.get("mode")
                d["raw"] = raw
            else:
                d["benefits"] = None
                d["eligibility"] = None
                d["score_breakdown"] = None
                d["source_evidence_snippet"] = None
                d["source_evidence_url"] = None
                d["country"] = None
                d["mode"] = None
                d["raw"] = None
            results.append(d)
        return results


@api_router.post("/briefings/opportunities/status", dependencies=WRITE_DEPS)
async def update_opportunity_status(req: OpportunityStatusRequest) -> dict[str, Any]:
    """Update user tracking status for an internship/hackathon opportunity."""
    valid_statuses = {
        "new",
        "interested",
        "applied",
        "interview",
        "offer",
        "rejected",
        "dismissed",
    }
    if req.status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{req.status}'. Must be one of: {sorted(valid_statuses)}",
        )
    async with get_db() as db:
        res = await execute(
            db,
            """
            UPDATE opportunity_registry
            SET status = ?, last_changed = (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            WHERE canonical_url = ?
            """,
            (req.status, req.canonical_url),
        )
        if res.rowcount == 0:
            await execute(
                db,
                """
                INSERT INTO opportunity_registry (
                    canonical_url, type, title, org, first_seen, last_seen, status, last_changed
                )
                VALUES (
                    ?, 'opportunity', ?, '',
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    ?,
                    (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                )
                ON CONFLICT(canonical_url) DO UPDATE
                SET status = excluded.status, last_changed = excluded.last_changed
                """,
                (req.canonical_url, req.canonical_url, req.status),
            )
        await db.commit()
    return {"ok": True, "canonical_url": req.canonical_url, "status": req.status}


@api_router.post("/briefings/{briefing_id}/feedback/detailed", dependencies=WRITE_DEPS)
async def rate_briefing_detailed(briefing_id: str, fb: DetailedBriefingFeedback) -> dict[str, Any]:
    """Submit granular feedback for briefing self-rewriting prompt engine."""
    from noxai.briefing.feedback import record_feedback

    fb_id = await record_feedback(
        briefing_id=briefing_id,
        rating=fb.rating,
        topic_id=fb.topic_id,
        item_id=fb.item_id,
        reason_text=fb.reason_text,
        tags=fb.tags,
    )
    return {"ok": True, "feedback_id": fb_id}


@api_router.delete("/briefings/{briefing_id}", dependencies=WRITE_DEPS)
async def delete_briefing_endpoint(briefing_id: str, purge: bool = False) -> dict[str, Any]:
    """Delete a briefing (soft delete with 30-day grace period, or purge)."""
    from noxai.jobs.deletion import delete_briefing

    try:
        return await delete_briefing(briefing_id, purge=purge)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err))


@api_router.post("/briefings/{briefing_id}/restore", dependencies=WRITE_DEPS)
async def restore_briefing_endpoint(briefing_id: str) -> dict[str, Any]:
    """Restore a soft-deleted briefing."""
    from noxai.jobs.deletion import restore_briefing

    try:
        return await restore_briefing(briefing_id)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err))


@api_router.post("/purge", dependencies=WRITE_DEPS)
async def purge_endpoint(all: bool = False, days: int = 30) -> dict[str, int]:
    """Purge soft-deleted jobs, tasks, and briefings."""
    from noxai.jobs.deletion import purge_expired

    return await purge_expired(max_age_days=days, force_all=all)


@api_router.get("/notifications", dependencies=READ_DEPS)
async def list_notifications_endpoint(
    limit: int = 50, unread_only: bool = False
) -> list[dict[str, Any]]:
    """List in-app notifications from the notification center."""
    async with get_db() as db:
        sql = "SELECT id, category, title, body, payload_json, read, created_at FROM notifications"
        if unread_only:
            sql += " WHERE read = 0"
        sql += " ORDER BY created_at DESC LIMIT ?"
        rows = await fetch_all(db, sql, (limit,))
        return [
            {
                "id": r["id"],
                "category": r["category"],
                "title": r["title"],
                "body": r["body"],
                "payload": _safe_json_loads(r["payload_json"]),
                "read": bool(r["read"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]


@api_router.post("/notifications/clear", dependencies=WRITE_DEPS)
async def clear_notifications_endpoint(all: bool = False) -> dict[str, Any]:
    """Mark all notifications as read (or delete if all=True)."""
    async with get_db() as db:
        if all:
            await execute(db, "DELETE FROM notifications")
        else:
            await execute(db, "UPDATE notifications SET read = 1 WHERE read = 0")
        await db.commit()
    return {"ok": True}


@api_router.post("/notifications/{notif_id}/read", dependencies=WRITE_DEPS)
async def mark_notification_read_endpoint(notif_id: int) -> dict[str, Any]:
    """Mark a single notification as read."""
    async with get_db() as db:
        await execute(db, "UPDATE notifications SET read = 1 WHERE id = ?", (notif_id,))
        await db.commit()
    return {"ok": True, "id": notif_id}


@api_router.get("/notes/info", dependencies=READ_DEPS)
async def get_notes_vault_info() -> dict[str, Any]:
    """Retrieve Notes Vault path, Obsidian URI, and system editor status."""
    import shutil

    from noxai.notes.vault import NotesVault

    vault = NotesVault()
    return {
        "vault_path": str(vault.vault_path),
        "obsidian_uri": vault.get_vault_uri(),
        "has_obsidian": bool(shutil.which("obsidian")),
    }


@api_router.post("/notes/open", dependencies=WRITE_DEPS)
async def open_notes_vault() -> dict[str, Any]:
    """Open Notes Vault in Obsidian or system default application."""
    import shutil
    import subprocess

    from noxai.notes.vault import NotesVault

    vault = NotesVault()
    path_str = str(vault.vault_path)
    uri = vault.get_vault_uri()
    opened = False
    target = path_str
    if shutil.which("obsidian"):
        try:
            subprocess.Popen(
                ["obsidian", uri],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            opened = True
            target = uri
        except OSError:
            pass
    if not opened and shutil.which("xdg-open"):
        try:
            subprocess.Popen(
                ["xdg-open", path_str],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            opened = True
        except OSError:
            pass
    return {
        "ok": True,
        "opened": opened,
        "target": target,
        "vault_path": path_str,
        "obsidian_uri": uri,
    }


@api_router.get("/keys", dependencies=READ_DEPS)
@api_router.get("/keys/status", dependencies=READ_DEPS)
async def get_keys_status() -> list[dict[str, Any]]:
    """Return live status of the NVIDIA API KeyPool."""
    from noxai.models.keypool import get_default_keypool

    pool = get_default_keypool()
    return pool.get_status_table()


@api_router.post("/keys/test", dependencies=WRITE_DEPS)
async def test_keys_endpoint() -> list[dict[str, Any]]:
    """Live probe all configured NVIDIA keys against /v1/models."""
    import time

    import httpx

    from noxai.models.keypool import get_default_keypool

    pool = get_default_keypool()
    results = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for k_id, k_state in pool._keys.items():
            t0 = time.perf_counter()
            try:
                r = await client.get(
                    "https://integrate.api.nvidia.com/v1/models",
                    headers={"Authorization": f"Bearer {k_state.api_key}"},
                )
                lat_ms = round((time.perf_counter() - t0) * 1000, 1)
                results.append(
                    {
                        "key_id": k_id,
                        "masked": k_state.masked_display,
                        "status_code": r.status_code,
                        "latency_ms": lat_ms,
                        "ok": r.status_code == 200,
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "key_id": k_id,
                        "masked": k_state.masked_display,
                        "status_code": 0,
                        "latency_ms": 0.0,
                        "ok": False,
                        "error": str(exc),
                    }
                )
    return results


# ---------------------------------------------------------------------------
# Memory Browser
# ---------------------------------------------------------------------------


class ThreadSummaryUpdate(BaseModel):
    summary: str


@api_router.get("/memory/thread/{conv_id}", dependencies=READ_DEPS)
async def get_thread_memory(conv_id: str) -> dict[str, Any]:
    """Retrieve full CMS L1-L3 thread state, summary, and entities."""
    import json

    async with get_db() as db:
        csum = await fetch_one(
            db,
            """
            SELECT fold_index, summary_markdown, key_facts_json,
                   active_topics_json, token_count, created_at
            FROM conv_summaries WHERE conversation_id = ?
            ORDER BY fold_index DESC LIMIT 1
            """,
            (conv_id,),
        )
        cstate = await fetch_one(
            db,
            """
            SELECT active_goals, working_hypotheses, open_questions, entities_in_focus,
                   user_preferences, meta_json, updated_at
            FROM conv_state WHERE conversation_id = ?
            """,
            (conv_id,),
        )
        facts = await fetch_all(
            db,
            """
            SELECT key, value, category, confidence FROM facts
            WHERE pinned = 1 AND deleted_at IS NULL
            LIMIT 5
            """,
        )
        msg_rows = await fetch_all(
            db,
            """
            SELECT id, role, content, created_at FROM messages
            WHERE conversation_id = ?
            ORDER BY created_at DESC, rowid DESC
            LIMIT 15
            """,
            (conv_id,),
        )
        meta = json.loads(cstate["meta_json"]) if (cstate and cstate["meta_json"]) else {}
        pinned_msg_ids = set(meta.get("pinned_message_ids", []))
        messages_list = [
            {
                "id": str(r["id"]),
                "role": str(r["role"]),
                "content": str(r["content"] or "")[:140],
                "pinned": str(r["id"]) in pinned_msg_ids,
                "created_at": str(r["created_at"]),
            }
            for r in reversed(msg_rows)
        ]

        return {
            "conversation_id": conv_id,
            "summary": csum["summary_markdown"] if csum else None,
            "fold_index": csum["fold_index"] if csum else 0,
            "token_count": csum["token_count"] if csum else 0,
            "active_goals": json.loads(cstate["active_goals"]) if cstate else [],
            "entities_in_focus": json.loads(cstate["entities_in_focus"]) if cstate else [],
            "open_questions": json.loads(cstate["open_questions"]) if cstate else [],
            "working_hypotheses": json.loads(cstate["working_hypotheses"]) if cstate else [],
            "active_facts": [dict(f) for f in facts],
            "messages": messages_list,
        }


@api_router.post("/memory/thread/{conv_id}/messages/{msg_id}/pin", dependencies=WRITE_DEPS)
async def toggle_pin_thread_message(conv_id: str, msg_id: str) -> dict[str, Any]:
    """Pin or unpin a specific dialogue message in thread working memory."""
    async with get_db() as db:
        cstate = await fetch_one(
            db, "SELECT meta_json FROM conv_state WHERE conversation_id = ?", (conv_id,)
        )
        meta = json.loads(cstate["meta_json"]) if (cstate and cstate["meta_json"]) else {}
        pinned_ids = meta.setdefault("pinned_message_ids", [])
        if msg_id in pinned_ids:
            pinned_ids.remove(msg_id)
            is_pinned = False
        else:
            pinned_ids.append(msg_id)
            is_pinned = True
        await execute(
            db,
            """
            INSERT INTO conv_state (conversation_id, meta_json, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(conversation_id) DO UPDATE SET meta_json = excluded.meta_json, updated_at = datetime('now')
            """,
            (conv_id, json.dumps(meta)),
        )
        await db.commit()
    return {"ok": True, "pinned": is_pinned, "message_id": msg_id}


@api_router.post("/memory/thread/{conv_id}/messages/{msg_id}/forget", dependencies=WRITE_DEPS)
@api_router.delete("/memory/thread/{conv_id}/messages/{msg_id}", dependencies=WRITE_DEPS)
async def forget_thread_message(conv_id: str, msg_id: str) -> dict[str, Any]:
    """Forget and delete a message and its search chunks from thread history."""
    async with get_db() as db:
        await execute(
            db, "DELETE FROM messages WHERE id = ? AND conversation_id = ?", (msg_id, conv_id)
        )
        await execute(db, "DELETE FROM msg_chunks WHERE message_id = ?", (msg_id,))
        cstate = await fetch_one(
            db, "SELECT meta_json FROM conv_state WHERE conversation_id = ?", (conv_id,)
        )
        if cstate and cstate["meta_json"]:
            meta = json.loads(cstate["meta_json"])
            pinned_ids = meta.get("pinned_message_ids", [])
            if msg_id in pinned_ids:
                pinned_ids.remove(msg_id)
                await execute(
                    db,
                    "UPDATE conv_state SET meta_json = ?, updated_at = datetime('now') WHERE conversation_id = ?",
                    (json.dumps(meta), conv_id),
                )
        await db.commit()
    return {"ok": True, "forgotten": msg_id}


@api_router.patch("/memory/thread/{conv_id}/summary", dependencies=WRITE_DEPS)
async def update_thread_summary(conv_id: str, body: ThreadSummaryUpdate) -> dict[str, Any]:
    """Manually update or override conversation summary."""
    async with get_db() as db:
        csum = await fetch_one(
            db,
            """
            SELECT id FROM conv_summaries
            WHERE conversation_id = ?
            ORDER BY fold_index DESC LIMIT 1
            """,
            (conv_id,),
        )
        if csum:
            await execute(
                db,
                "UPDATE conv_summaries SET summary_markdown = ? WHERE id = ?",
                (body.summary, csum["id"]),
            )
        else:
            import uuid

            csum_id = f"csum_{uuid.uuid4().hex[:8]}"
            await execute(
                db,
                """
                INSERT INTO conv_summaries (
                    id, conversation_id, fold_index, summary_markdown, token_count
                )
                VALUES (?, ?, 0, ?, ?)
                """,
                (csum_id, conv_id, body.summary, len(body.summary.split())),
            )
        await db.commit()
    return {"ok": True, "summary": body.summary}


@api_router.post("/memory/thread/{conv_id}/reset", dependencies=WRITE_DEPS)
async def reset_thread_memory(conv_id: str) -> dict[str, Any]:
    """Reset memory folds and working state for a conversation thread."""
    async with get_db() as db:
        await execute(db, "DELETE FROM conv_summaries WHERE conversation_id = ?", (conv_id,))
        await execute(db, "DELETE FROM conv_state WHERE conversation_id = ?", (conv_id,))
        await execute(db, "DELETE FROM msg_chunks WHERE conversation_id = ?", (conv_id,))
        await db.commit()
    return {"ok": True, "reset": conv_id}


@api_router.post("/memory/thread/{conv_id}/export", dependencies=WRITE_DEPS)
async def export_thread_to_vault(conv_id: str) -> dict[str, Any]:
    """Export thread digest note to Notes Vault."""
    from noxai.notes.vault import NotesVault

    async with get_db() as db:
        conv = await fetch_one(db, "SELECT title FROM conversations WHERE id = ?", (conv_id,))
        csum = await fetch_one(
            db,
            """
            SELECT summary_markdown FROM conv_summaries
            WHERE conversation_id = ?
            ORDER BY fold_index DESC LIMIT 1
            """,
            (conv_id,),
        )
        title = conv["title"] if conv else f"Chat {conv_id}"
        summary = csum["summary_markdown"] if csum else "No summary recorded."

    vault = NotesVault()
    path = vault.export_conversation_thread(
        conv_id=conv_id,
        title=title,
        summary=summary,
        decisions=[],
        open_questions=[],
        links=[],
    )
    return {"ok": True, "path": str(path), "uri": vault.get_vault_uri()}


@api_router.get("/memory/facts", response_model=list[FactResponse], dependencies=READ_DEPS)
async def list_memory_facts() -> list[FactResponse]:
    """List all stored facts without BLOB embeddings."""
    async with get_db() as db:
        rows = await fetch_all(
            db,
            """
            SELECT id, key, value, category, confidence, source, pinned,
                   created_at, updated_at, last_used_at, use_count
            FROM facts WHERE deleted_at IS NULL ORDER BY pinned DESC, updated_at DESC
            """,
        )
        return [
            FactResponse(
                id=int(r["id"]),
                key=str(r["key"]),
                value=str(r["value"]),
                category=str(r["category"]),
                confidence=float(r["confidence"]),
                source=str(r["source"]),
                pinned=bool(r["pinned"]),
                created_at=str(r["created_at"]),
                updated_at=str(r["updated_at"]),
                last_used_at=str(r["last_used_at"]) if r["last_used_at"] else None,
                use_count=int(r["use_count"] or 0),
            )
            for r in rows
        ]


@api_router.patch("/memory/facts/{fact_id}", response_model=FactResponse, dependencies=WRITE_DEPS)
async def update_memory_fact(fact_id: int, update: FactUpdate) -> FactResponse:
    """Update a memory fact value."""
    async with get_db() as db:
        await execute(
            db,
            "UPDATE facts SET value = ?, updated_at = datetime('now') WHERE id = ?",
            (update.value, fact_id),
        )
        await db.commit()
        row = await fetch_one(
            db,
            """
            SELECT id, key, value, category, confidence, source, pinned,
                   created_at, updated_at, last_used_at, use_count
            FROM facts WHERE id = ?
            """,
            (fact_id,),
        )
        if not row:
            raise HTTPException(status_code=404, detail="Fact not found")

        return FactResponse(
            id=int(row["id"]),
            key=str(row["key"]),
            value=str(row["value"]),
            category=str(row["category"]),
            confidence=float(row["confidence"]),
            source=str(row["source"]),
            pinned=bool(row["pinned"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            last_used_at=str(row["last_used_at"]) if row["last_used_at"] else None,
            use_count=int(row["use_count"] or 0),
        )


@api_router.delete("/memory/facts/{fact_id}", dependencies=WRITE_DEPS)
async def delete_memory_fact(fact_id: int) -> dict[str, bool]:
    """Hard delete a memory fact."""
    from noxai.memory.store import hard_delete_fact

    async with get_db() as db:
        row = await fetch_one(db, "SELECT key FROM facts WHERE id = ?", (fact_id,))
        if not row:
            raise HTTPException(status_code=404, detail="Fact not found")
        await hard_delete_fact(db, fact_id)
    return {"ok": True}


@api_router.post("/memory/facts/{fact_id}/pin", dependencies=WRITE_DEPS)
async def pin_memory_fact(fact_id: int, req: FactPinRequest) -> dict[str, bool]:
    """Pin or unpin a memory fact."""
    async with get_db() as db:
        await execute(
            db,
            "UPDATE facts SET pinned = ?, updated_at = datetime('now') WHERE id = ?",
            (1 if req.pinned else 0, fact_id),
        )
        await db.commit()
    return {"ok": True}


@api_router.get("/memory/episodes", response_model=list[EpisodeResponse], dependencies=READ_DEPS)
async def list_memory_episodes(
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[EpisodeResponse]:
    """List memory episodes without BLOB embeddings."""
    async with get_db() as db:
        rows = await fetch_all(
            db,
            """
            SELECT id, ts, kind, task_id, summary, importance, archived_at
            FROM episodes ORDER BY ts DESC LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [
            EpisodeResponse(
                id=int(r["id"]),
                ts=str(r["ts"]),
                kind=str(r["kind"]),
                task_id=str(r["task_id"]) if r["task_id"] else None,
                summary=str(r["summary"]),
                importance=float(r["importance"]),
                archived_at=str(r["archived_at"]) if r["archived_at"] else None,
            )
            for r in rows
        ]


@api_router.get("/memory/search", response_model=MemorySearchResponse, dependencies=READ_DEPS)
async def search_memory(q: str = Query(..., min_length=1)) -> MemorySearchResponse:
    """Search long-term memory facts and episodes."""
    from noxai.memory.recall import recall_memories

    cfg = get_config()
    router = get_default_router(cfg)
    async with get_db() as db:
        rec = await recall_memories(q, db=db, router=router, config=cfg)
        facts_clean = [
            MemoryFactResult(
                id=int(getattr(f, "id", 0) or 0),
                key=str(f.key),
                value=str(f.value),
                category=str(getattr(f, "category", "other")),
                confidence=float(getattr(f, "confidence", 1.0)),
                pinned=bool(getattr(f, "pinned", False)),
                score=float(getattr(f, "score", 0.0) or 0.0),
            )
            for f in rec.facts
        ]
        episodes_clean = [
            MemoryEpisodeResult(
                id=int(getattr(e, "id", 0) or 0),
                summary=str(e.summary),
                importance=float(getattr(e, "importance", 0.5)),
                ts=str(getattr(e, "ts", "")),
                score=float(getattr(e, "score", 0.0) or 0.0),
            )
            for e in rec.episodes
        ]
        return MemorySearchResponse(
            query=q,
            facts=facts_clean,
            episodes=episodes_clean,
            formatted=str(rec.formatted_block),
        )


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------


@api_router.get("/audit", response_model=list[AuditLogResponse], dependencies=READ_DEPS)
async def get_audit_log(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    tool: str | None = None,
) -> list[AuditLogResponse]:
    """Get paged and filterable audit log records."""
    sql = """
        SELECT id, ts, run_id, tool, args_json, tier, decision, approver,
               result_summary, duration_ms, ok
        FROM audit_log
    """
    args: list[Any] = []
    if tool:
        sql += " WHERE tool = ?"
        args.append(tool)
    sql += " ORDER BY ts DESC LIMIT ? OFFSET ?"
    args.extend([limit, offset])

    async with get_db() as db:
        rows = await fetch_all(db, sql, tuple(args))
        return [
            AuditLogResponse(
                id=int(r["id"]),
                ts=str(r["ts"]),
                run_id=str(r["run_id"]) if r["run_id"] else None,
                tool=str(r["tool"]),
                args_json=str(r["args_json"]),
                tier=str(r["tier"]),
                decision=str(r["decision"]),
                approver=str(r["approver"]),
                result_summary=str(r["result_summary"]) if r["result_summary"] else None,
                duration_ms=int(r["duration_ms"]) if r["duration_ms"] is not None else None,
                ok=bool(r["ok"]),
            )
            for r in rows
        ]


@api_router.get("/egress", response_model=list[EgressRecordResponse], dependencies=READ_DEPS)
async def get_egress_ledger(
    limit: int = Query(50, ge=1, le=200),
) -> list[EgressRecordResponse]:
    """Get recent egress ledger audit entries (zero payload text stored)."""
    from noxai.security.egress_filter import get_recent_egress_records

    records = await get_recent_egress_records(limit=limit)
    return [
        EgressRecordResponse(
            id=int(r["id"]),
            timestamp=str(r["timestamp"]),
            provider=str(r["provider"] or ""),
            model=str(r["model"] or ""),
            prompt_tokens_est=int(r["prompt_tokens_est"] or 0),
            data_class=str(r["data_class"] or ""),
            decision=str(r["decision"] or ""),
            reason=str(r["reason"] or ""),
            sha256_hash=str(r["sha256_hash"] or ""),
            created_at=str(r["created_at"] or ""),
        )
        for r in records
    ]


# ---------------------------------------------------------------------------
# Settings (Non-Safety)
# ---------------------------------------------------------------------------


@api_router.get("/settings", dependencies=READ_DEPS)
async def get_settings_endpoint() -> dict[str, Any]:
    """Get editable non-safety settings (theme, notifications, accent color)."""
    async with get_db() as db:
        rows = await fetch_all(db, "SELECT key, value FROM settings")
        settings_map = {r["key"]: _safe_json_loads(r["value"]) for r in rows}
        return settings_map


@api_router.patch("/settings", dependencies=WRITE_DEPS)
async def update_settings_endpoint(body: dict[str, Any]) -> dict[str, bool]:
    """Update non-safety settings."""
    forbidden_keys = {"safety", "guard", "shell_allowlist", "approval_timeout"}
    async with get_db() as db:
        for k, v in body.items():
            if k in forbidden_keys:
                raise HTTPException(status_code=400, detail=f"Setting '{k}' is read-only.")
            await execute(
                db,
                """
                INSERT INTO settings (key, value, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')
                """,
                (k, json.dumps(v)),
            )
        await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


@api_router.get("/sources", response_model=list[SourceResponse], dependencies=READ_DEPS)
async def list_sources_endpoint() -> list[SourceResponse]:
    """List news and opportunity sources."""
    async with get_db() as db:
        rows = await fetch_all(
            db,
            "SELECT id, name, url, kind, tags, enabled, last_ok_at, "
            "last_error, created_at FROM sources ORDER BY id ASC",
        )
        results: list[SourceResponse] = []
        for r in rows:
            tags = _safe_json_loads(r["tags"])
            results.append(
                SourceResponse(
                    id=int(r["id"]),
                    name=str(r["name"]),
                    url=str(r["url"]),
                    kind=str(r["kind"]),
                    tags=tags if isinstance(tags, list) else [],
                    enabled=bool(r["enabled"]),
                    last_ok_at=str(r["last_ok_at"]) if r["last_ok_at"] else None,
                    last_error=str(r["last_error"]) if r["last_error"] else None,
                    created_at=str(r["created_at"]),
                )
            )
        return results


@api_router.post("/sources", response_model=SourceResponse, dependencies=WRITE_DEPS)
async def create_source_endpoint(src: SourceCreateRequest) -> SourceResponse:
    """Add a new source."""
    async with get_db() as db:
        cursor = await execute(
            db,
            """
            INSERT INTO sources (name, url, kind, tags, enabled)
            VALUES (?, ?, ?, ?, ?)
            """,
            (src.name, src.url, src.kind, json.dumps(src.tags), 1 if src.enabled else 0),
        )
        await db.commit()
        sid = cursor.lastrowid or 0
        row = await fetch_one(
            db,
            "SELECT id, name, url, kind, tags, enabled, last_ok_at, "
            "last_error, created_at FROM sources WHERE id = ?",
            (sid,),
        )
        if not row:
            raise HTTPException(status_code=500, detail="Failed to retrieve created source")

        tags = _safe_json_loads(row["tags"])
        return SourceResponse(
            id=int(row["id"]),
            name=str(row["name"]),
            url=str(row["url"]),
            kind=str(row["kind"]),
            tags=tags if isinstance(tags, list) else [],
            enabled=bool(row["enabled"]),
            last_ok_at=str(row["last_ok_at"]) if row["last_ok_at"] else None,
            last_error=str(row["last_error"]) if row["last_error"] else None,
            created_at=str(row["created_at"]),
        )


@api_router.patch("/sources/{source_id}", response_model=SourceResponse, dependencies=WRITE_DEPS)
async def update_source_endpoint(source_id: int, updates: SourceUpdateRequest) -> SourceResponse:
    """Update a source."""
    async with get_db() as db:
        sets: list[str] = []
        vals: list[Any] = []
        if updates.name is not None:
            sets.append("name = ?")
            vals.append(updates.name)
        if updates.url is not None:
            sets.append("url = ?")
            vals.append(updates.url)
        if updates.kind is not None:
            sets.append("kind = ?")
            vals.append(updates.kind)
        if updates.tags is not None:
            sets.append("tags = ?")
            vals.append(json.dumps(updates.tags))
        if updates.enabled is not None:
            sets.append("enabled = ?")
            vals.append(1 if updates.enabled else 0)

        if sets:
            vals.append(source_id)
            await execute(db, f"UPDATE sources SET {', '.join(sets)} WHERE id = ?", tuple(vals))  # noqa: S608
            await db.commit()

        row = await fetch_one(
            db,
            "SELECT id, name, url, kind, tags, enabled, last_ok_at, "
            "last_error, created_at FROM sources WHERE id = ?",
            (source_id,),
        )
        if not row:
            raise HTTPException(status_code=404, detail="Source not found")

        tags = _safe_json_loads(row["tags"])
        return SourceResponse(
            id=int(row["id"]),
            name=str(row["name"]),
            url=str(row["url"]),
            kind=str(row["kind"]),
            tags=tags if isinstance(tags, list) else [],
            enabled=bool(row["enabled"]),
            last_ok_at=str(row["last_ok_at"]) if row["last_ok_at"] else None,
            last_error=str(row["last_error"]) if row["last_error"] else None,
            created_at=str(row["created_at"]),
        )


@api_router.delete("/sources/{source_id}", dependencies=WRITE_DEPS)
async def delete_source_endpoint(source_id: int) -> dict[str, bool]:
    """Delete a source."""
    async with get_db() as db:
        await execute(db, "DELETE FROM sources WHERE id = ?", (source_id,))
        await db.commit()
    return {"ok": True}
