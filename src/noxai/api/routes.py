"""REST API routes for NoX daemon per Section 6.1."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from noxai.api.auth import (
    check_gate_authenticated,
    verify_csrf,
    verify_session,
)
from noxai.config import Settings, get_config
from noxai.db import execute, fetch_all, fetch_one, get_db
from noxai.events import global_event_bus, make_event
from noxai.models.router import get_default_router
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


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str
    allow_cloud: bool = False


class ChatResponse(BaseModel):
    run_id: str
    conversation_id: str


class ApprovalDecision(BaseModel):
    decision: str = Field(..., description="'approve' or 'deny'")


class BriefingFeedback(BaseModel):
    rating: int = Field(..., description="1 (thumbs up) or -1 (thumbs down)")
    note: str | None = None


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
    version: int


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


# ---------------------------------------------------------------------------
# Status & Auth
# ---------------------------------------------------------------------------


@api_router.get("/status")
async def get_system_status() -> dict[str, Any]:
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

    # Vector backend
    from noxai.db import _detect_vector_backend

    vec_backend = _detect_vector_backend()

    # Cloud status
    has_nvidia_key = bool(cfg.nvidia_api_key and cfg.nvidia_api_key.get_secret_value())

    return {
        "status": "ok",
        "authenticated": authenticated,
        "gate_mode": cfg.gate.mode,
        "ollama_reachable": ollama_ok,
        "loaded_models": loaded_models,
        "cloud_available": has_nvidia_key,
        "cloud_key_configured": has_nvidia_key,
        "active_runs_count": len(active_runs),
        "db_size_bytes": db_size_bytes,
        "vector_backend": vec_backend,
    }


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
    from noxai.agent.gate import evaluate_complexity
    from noxai.db import get_db

    router = get_default_router(config)
    registry = default_registry

    async with get_db() as db:
        try:
            await global_event_bus.publish(
                make_event("run_started", {"message": message}, run_id=run_id),
                db=db,
            )

            # Store user message
            msg_id = f"msg_{uuid.uuid4().hex[:12]}"
            await execute(
                db,
                """
                INSERT INTO messages (id, conversation_id, role, content)
                VALUES (?, ?, 'user', ?)
                """,
                (msg_id, conv_id, message),
            )
            await db.commit()

            # Evaluate complexity
            gate_res = await evaluate_complexity(message, router=router)

            if gate_res.is_complex:
                from noxai.agent.executor import DAGExecutor

                executor = DAGExecutor(
                    router=router,
                    registry=registry,
                    config=config,
                    event_bus=global_event_bus,
                )
                exec_res = await executor.execute_plan(
                    goal=message,
                    conversation_id=conv_id,
                    allow_cloud=allow_cloud,
                    db=db,
                    run_id=run_id,
                )
                final_text = exec_res.final_text
            else:
                from noxai.agent.loop import AgentLoop

                loop = AgentLoop(
                    router=router,
                    registry=registry,
                    config=config,
                    event_bus=global_event_bus,
                )
                loop_res = await loop.run(
                    user_message=message,
                    conversation_id=conv_id,
                    allow_cloud=allow_cloud,
                    db=db,
                    run_id=run_id,
                )
                final_text = loop_res.text

            # Store assistant message
            asst_id = f"msg_{uuid.uuid4().hex[:12]}"
            await execute(
                db,
                """
                INSERT INTO messages (id, conversation_id, role, content)
                VALUES (?, ?, 'assistant', ?)
                """,
                (asst_id, conv_id, final_text),
            )
            await db.commit()

            await global_event_bus.publish(
                make_event("message_done", {"text": final_text}, run_id=run_id),
                db=db,
            )
            await global_event_bus.publish(
                make_event("run_done", {"status": "done"}, run_id=run_id),
                db=db,
            )

        except asyncio.CancelledError:
            log.info("Run %s was cancelled.", run_id)
            await global_event_bus.publish(
                make_event("run_cancelled", {}, run_id=run_id),
                db=db,
            )
        except Exception as exc:
            log.error("Run %s failed with exception: %s", run_id, exc)
            await global_event_bus.publish(
                make_event("run_failed", {"error": str(exc)}, run_id=run_id),
                db=db,
            )


@api_router.post("/chat", dependencies=WRITE_DEPS)
async def chat_endpoint(req: ChatRequest) -> ChatResponse:
    """Start an agent run (simple or complex) and stream results via WebSocket."""
    conv_id = req.conversation_id or f"conv_{uuid.uuid4().hex[:12]}"
    run_id = f"run_{uuid.uuid4().hex[:12]}"

    # Ensure conversation row exists
    async with get_db() as db:
        await execute(
            db,
            """
            INSERT INTO conversations (id, title, created_at, updated_at)
            VALUES (?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(id) DO UPDATE SET updated_at = datetime('now')
            """,
            (conv_id, req.message[:40]),
        )
        await db.commit()

    cfg = get_config()
    task = asyncio.create_task(
        _run_chat_pipeline(
            run_id=run_id,
            conv_id=conv_id,
            message=req.message,
            allow_cloud=req.allow_cloud,
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

    await global_event_bus.publish(
        make_event(
            "approval_resolved",
            {"approval_id": approval_id, "decision": body.decision, "ok": ok},
        )
    )
    return {"ok": ok}


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


@api_router.get("/conversations", dependencies=READ_DEPS)
async def list_conversations() -> list[dict[str, Any]]:
    """List all saved conversations."""
    async with get_db() as db:
        rows = await fetch_all(db, "SELECT * FROM conversations ORDER BY updated_at DESC")
        return [dict(r) for r in rows]


@api_router.get("/conversations/{conv_id}", dependencies=READ_DEPS)
async def get_conversation(conv_id: str) -> dict[str, Any]:
    """Retrieve conversation details and messages."""
    async with get_db() as db:
        conv = await fetch_one(db, "SELECT * FROM conversations WHERE id = ?", (conv_id,))
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")

        msgs = await fetch_all(
            db,
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
            (conv_id,),
        )
        data = dict(conv)
        data["messages"] = [dict(m) for m in msgs]
        return data


@api_router.delete("/conversations/{conv_id}", dependencies=WRITE_DEPS)
async def delete_conversation(conv_id: str) -> dict[str, bool]:
    """Delete a conversation and its messages."""
    async with get_db() as db:
        await execute(db, "DELETE FROM messages WHERE conversation_id = ?", (conv_id,))
        await execute(db, "DELETE FROM conversations WHERE id = ?", (conv_id,))
        await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Jobs & Reminders
# ---------------------------------------------------------------------------


@api_router.get("/jobs", dependencies=READ_DEPS)
async def list_jobs() -> list[dict[str, Any]]:
    """List scheduled jobs."""
    async with get_db() as db:
        rows = await fetch_all(db, "SELECT * FROM jobs ORDER BY name ASC")
        return [dict(r) for r in rows]


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


@api_router.get("/jobs/{job_id}", dependencies=READ_DEPS)
async def get_job_endpoint(job_id: str) -> dict[str, Any]:
    """Get details for a specific job."""
    async with get_db() as db:
        row = await fetch_one(db, "SELECT * FROM jobs WHERE id = ? OR name = ?", (job_id, job_id))
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")
        return dict(row)


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
        if updates.schedule_expr is not None:
            sets.append("schedule_expr = ?")
            vals.append(updates.schedule_expr)
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

            updated = await fetch_one(db, "SELECT * FROM jobs WHERE id = ?", (real_id,))
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

        updated_row = await fetch_one(db, "SELECT * FROM jobs WHERE id = ?", (real_id,))
        return dict(updated_row) if updated_row else {}


@api_router.delete("/jobs/{job_id}", dependencies=WRITE_DEPS)
async def delete_job_endpoint(job_id: str) -> dict[str, bool]:
    """Delete a scheduled job."""
    async with get_db() as db:
        await execute(db, "DELETE FROM jobs WHERE id = ? OR name = ?", (job_id, job_id))
        await db.commit()
    return {"ok": True}


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


@api_router.get("/jobs/{job_id}/versions", dependencies=READ_DEPS)
async def get_job_versions_endpoint(job_id: str) -> list[dict[str, Any]]:
    """Retrieve historical snapshots for a job."""
    async with get_db() as db:
        row = await fetch_one(db, "SELECT id FROM jobs WHERE id = ? OR name = ?", (job_id, job_id))
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")

        v_rows = await fetch_all(
            db,
            "SELECT version, changed_by, change_note, created_at FROM job_versions "
            "WHERE job_id = ? ORDER BY version DESC",
            (str(row["id"]),),
        )
        return [dict(v) for v in v_rows]


@api_router.post("/jobs/{job_id}/restore", dependencies=WRITE_DEPS)
async def restore_job_endpoint(job_id: str, body: JobRestoreRequest) -> dict[str, Any]:
    """Restore a job to a specific previous version."""
    async with get_db() as db:
        row = await fetch_one(db, "SELECT id FROM jobs WHERE id = ? OR name = ?", (job_id, job_id))
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

        snap = json.loads(v_row["snapshot_json"])
        await execute(
            db,
            """
            UPDATE jobs SET
                schedule_type = ?, schedule_expr = ?, timezone = ?,
                instruction = ?, config_json = ?, enabled = ?,
                version = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                snap["schedule_type"],
                snap["schedule_expr"],
                snap["timezone"],
                snap["instruction"],
                snap["config_json"],
                snap["enabled"],
                body.version,
                real_id,
            ),
        )
        await db.commit()
        return {"ok": True, "restored_version": body.version}


@api_router.get("/jobs/{job_id}/runs", dependencies=READ_DEPS)
async def get_job_runs_endpoint(job_id: str) -> list[dict[str, Any]]:
    """Get execution history for a job."""
    async with get_db() as db:
        row = await fetch_one(db, "SELECT id FROM jobs WHERE id = ? OR name = ?", (job_id, job_id))
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")

        runs = await fetch_all(
            db,
            "SELECT * FROM job_runs WHERE job_id = ? ORDER BY started_at DESC LIMIT 50",
            (str(row["id"]),),
        )
        return [dict(r) for r in runs]


# ---------------------------------------------------------------------------
# Briefings (Job Outputs)
# ---------------------------------------------------------------------------


@api_router.get("/briefings", dependencies=READ_DEPS)
async def list_briefings() -> list[dict[str, Any]]:
    """List delivered briefings."""
    async with get_db() as db:
        rows = await fetch_all(
            db,
            "SELECT id, job_run_id, title, created_at, feedback FROM outputs "
            "ORDER BY created_at DESC LIMIT 50",
        )
        return [dict(r) for r in rows]


@api_router.get("/briefings/{briefing_id}", dependencies=READ_DEPS)
async def get_briefing(briefing_id: int) -> dict[str, Any]:
    """Read a full briefing document."""
    async with get_db() as db:
        row = await fetch_one(db, "SELECT * FROM outputs WHERE id = ?", (briefing_id,))
        if not row:
            raise HTTPException(status_code=404, detail="Briefing not found")
        return dict(row)


@api_router.post("/briefings/{briefing_id}/feedback", dependencies=WRITE_DEPS)
async def briefing_feedback(briefing_id: int, fb: BriefingFeedback) -> dict[str, bool]:
    """Record owner feedback rating on a briefing."""
    async with get_db() as db:
        await execute(
            db,
            "UPDATE outputs SET feedback = ?, feedback_note = ? WHERE id = ?",
            (fb.rating, fb.note, briefing_id),
        )
        await db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Memory Browser
# ---------------------------------------------------------------------------


@api_router.get("/memory/facts", dependencies=READ_DEPS)
async def list_memory_facts() -> list[dict[str, Any]]:
    """List all stored facts."""
    async with get_db() as db:
        rows = await fetch_all(db, "SELECT * FROM facts ORDER BY pinned DESC, updated_at DESC")
        return [dict(r) for r in rows]


@api_router.patch("/memory/facts/{fact_id}", dependencies=WRITE_DEPS)
async def update_memory_fact(fact_id: int, update: FactUpdate) -> dict[str, Any]:
    """Update a memory fact value."""
    async with get_db() as db:
        await execute(
            db,
            "UPDATE facts SET value = ?, updated_at = datetime('now') WHERE id = ?",
            (update.value, fact_id),
        )
        await db.commit()
        row = await fetch_one(db, "SELECT * FROM facts WHERE id = ?", (fact_id,))
        if not row:
            raise HTTPException(status_code=404, detail="Fact not found")
        return dict(row)


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


@api_router.get("/memory/episodes", dependencies=READ_DEPS)
async def list_memory_episodes() -> list[dict[str, Any]]:
    """List memory episodes."""
    async with get_db() as db:
        rows = await fetch_all(db, "SELECT * FROM episodes ORDER BY ts DESC LIMIT 100")
        return [dict(r) for r in rows]


@api_router.get("/memory/search", dependencies=READ_DEPS)
async def search_memory(q: str = Query(..., min_length=1)) -> dict[str, Any]:
    """Search long-term memory facts and episodes."""
    from noxai.memory.recall import recall_memories

    cfg = get_config()
    router = get_default_router(cfg)
    async with get_db() as db:
        rec = await recall_memories(q, db=db, router=router, config=cfg)
        return {
            "query": q,
            "facts": [dataclasses.asdict(f) for f in rec.facts],
            "episodes": [dataclasses.asdict(e) for e in rec.episodes],
            "formatted": rec.formatted_block,
        }


# ---------------------------------------------------------------------------
# Audit Log
# ---------------------------------------------------------------------------


@api_router.get("/audit", dependencies=READ_DEPS)
async def get_audit_log(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    tool: str | None = None,
) -> list[dict[str, Any]]:
    """Get paged and filterable audit log records."""
    sql = "SELECT * FROM audit_log"
    args: list[Any] = []
    if tool:
        sql += " WHERE tool = ?"
        args.append(tool)
    sql += " ORDER BY ts DESC LIMIT ? OFFSET ?"
    args.extend([limit, offset])

    async with get_db() as db:
        rows = await fetch_all(db, sql, tuple(args))
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Settings (Non-Safety)
# ---------------------------------------------------------------------------


@api_router.get("/settings", dependencies=READ_DEPS)
async def get_settings_endpoint() -> dict[str, Any]:
    """Get editable non-safety settings (theme, notifications, accent color)."""
    async with get_db() as db:
        rows = await fetch_all(db, "SELECT key, value FROM settings")
        settings_map = {r["key"]: json.loads(r["value"]) for r in rows}
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


@api_router.get("/sources", dependencies=READ_DEPS)
async def list_sources_endpoint() -> list[dict[str, Any]]:
    """List news and opportunity sources."""
    async with get_db() as db:
        rows = await fetch_all(db, "SELECT * FROM sources ORDER BY id ASC")
        return [dict(r) for r in rows]


@api_router.post("/sources", dependencies=WRITE_DEPS)
async def create_source_endpoint(src: SourceCreateRequest) -> dict[str, Any]:
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
        return {"id": cursor.lastrowid, "name": src.name, "url": src.url}


@api_router.patch("/sources/{source_id}", dependencies=WRITE_DEPS)
async def update_source_endpoint(source_id: int, updates: SourceUpdateRequest) -> dict[str, Any]:
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

        row = await fetch_one(db, "SELECT * FROM sources WHERE id = ?", (source_id,))
        if not row:
            raise HTTPException(status_code=404, detail="Source not found")
        return dict(row)


@api_router.delete("/sources/{source_id}", dependencies=WRITE_DEPS)
async def delete_source_endpoint(source_id: int) -> dict[str, bool]:
    """Delete a source."""
    async with get_db() as db:
        await execute(db, "DELETE FROM sources WHERE id = ?", (source_id,))
        await db.commit()
    return {"ok": True}
