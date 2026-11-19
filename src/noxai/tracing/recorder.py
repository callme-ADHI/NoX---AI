# Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
# commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
# Modified for NoX: SQLite WAL trace recorder with secret redaction, step-level metrics, and crash recovery.

"""Step-level trace recorder and query API for NoX (Workstream C).

Records:
- id, started_at, finished_at, source ('chat', 'briefing', 'job', 'schedule')
- schedule_run_id, request_text (max 4000 chars), intent
- engine, model, key_slot_index (never the key!)
- steps (JSON array of: name, tool_or_skill, duration_ms, status)
- token_counts (JSON dict: prompt, completion, total)
- final_status ('running', 'success', 'failed', 'interrupted', 'cancelled')
- error_text, feedback (JSON dict)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import logging
import statistics
import time
import uuid
from typing import Any

import aiosqlite

from noxai.db import execute, fetch_all, fetch_one, get_db
from noxai.tracing.redact import redact_secrets

log = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class TraceRecorder:
    """Async context manager to record execution trace with step tracking and automatic redaction."""

    def __init__(
        self,
        source: str,
        request_text: str | None = None,
        intent: str | None = None,
        schedule_run_id: str | None = None,
        trace_id: str | None = None,
        engine: str | None = None,
        model: str | None = None,
        key_slot_index: int | None = None,
    ) -> None:
        self.trace_id = trace_id or f"tr_{uuid.uuid4().hex[:12]}"
        self.source = source
        # Truncate request text to 4000 characters per requirement
        clean_text = (request_text or "")[:4000]
        self.request_text = redact_secrets(clean_text) if clean_text else None
        self.intent = intent
        self.schedule_run_id = schedule_run_id
        self.engine = engine
        self.model = model
        self.key_slot_index = key_slot_index
        self.steps: list[dict[str, Any]] = []
        self.token_counts: dict[str, int] | None = None
        self.final_status = "running"
        self.error_text: str | None = None
        self.feedback: dict[str, Any] | None = None
        self.started_at = _utc_now_iso()
        self.finished_at: str | None = None
        self._start_perf = time.perf_counter()

    async def __aenter__(self) -> TraceRecorder:
        """Insert trace in 'running' status into the database."""
        try:
            async with get_db() as db:
                await execute(
                    db,
                    """
                    INSERT INTO traces (
                        id, started_at, source, schedule_run_id, request_text,
                        intent, engine, model, key_slot_index, steps,
                        token_counts, final_status, error_text, feedback
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.trace_id,
                        self.started_at,
                        self.source,
                        self.schedule_run_id,
                        self.request_text,
                        self.intent,
                        self.engine,
                        self.model,
                        self.key_slot_index,
                        json.dumps(self.steps),
                        json.dumps(self.token_counts) if self.token_counts else None,
                        self.final_status,
                        self.error_text,
                        json.dumps(self.feedback) if self.feedback else None,
                    ),
                )
                await db.commit()
        except Exception as exc:
            log.warning("Failed to initialize trace %s: %s", self.trace_id, exc)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Close trace with success or failure and persist final state."""
        self.finished_at = _utc_now_iso()
        if exc_val is not None:
            self.final_status = "failed"
            self.error_text = redact_secrets(str(exc_val))
        elif self.final_status == "running":
            self.final_status = "success"

        try:
            async with get_db() as db:
                await execute(
                    db,
                    """
                    UPDATE traces SET
                        finished_at = ?,
                        engine = ?,
                        model = ?,
                        key_slot_index = ?,
                        steps = ?,
                        token_counts = ?,
                        final_status = ?,
                        error_text = ?,
                        feedback = ?
                    WHERE id = ?
                    """,
                    (
                        self.finished_at,
                        self.engine,
                        self.model,
                        self.key_slot_index,
                        json.dumps(redact_secrets(self.steps)),
                        json.dumps(self.token_counts) if self.token_counts else None,
                        self.final_status,
                        self.error_text,
                        json.dumps(redact_secrets(self.feedback)) if self.feedback else None,
                        self.trace_id,
                    ),
                )
                await db.commit()
        except Exception as exc:
            log.warning("Failed to finalize trace %s: %s", self.trace_id, exc)

    def record_step(
        self,
        name: str,
        tool_or_skill: str | None = None,
        duration_ms: float = 0.0,
        status: str = "success",
    ) -> None:
        """Record an intermediate execution step."""
        step_dict = {
            "name": redact_secrets(name),
            "tool_or_skill": redact_secrets(tool_or_skill) if tool_or_skill else None,
            "duration_ms": round(duration_ms, 2),
            "status": status,
        }
        self.steps.append(step_dict)

    def set_engine_and_model(
        self,
        engine: str | None,
        model: str | None,
        key_slot_index: int | None = None,
    ) -> None:
        """Set or update the engine, model, and key slot index (never the key)."""
        if engine:
            self.engine = engine
        if model:
            self.model = model
        if key_slot_index is not None:
            self.key_slot_index = key_slot_index

    def set_tokens(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
    ) -> None:
        """Record token counts returned by the model provider."""
        self.token_counts = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens or (prompt_tokens + completion_tokens),
        }


def trace_execution(
    source: str,
    request_text: str | None = None,
    intent: str | None = None,
    schedule_run_id: str | None = None,
    trace_id: str | None = None,
    engine: str | None = None,
    model: str | None = None,
    key_slot_index: int | None = None,
) -> TraceRecorder:
    """Create a TraceRecorder context manager."""
    return TraceRecorder(
        source=source,
        request_text=request_text,
        intent=intent,
        schedule_run_id=schedule_run_id,
        trace_id=trace_id,
        engine=engine,
        model=model,
        key_slot_index=key_slot_index,
    )


async def recover_interrupted_traces(db: aiosqlite.Connection | None = None) -> int:
    """Mark any traces left in 'running' state as 'interrupted' (e.g. after daemon crash)."""
    sql = """
        UPDATE traces
        SET final_status = 'interrupted',
            finished_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
            error_text = 'Process interrupted unexpectedly'
        WHERE final_status = 'running'
    """
    if db is not None:
        res = await execute(db, sql)
        await db.commit()
        return res.rowcount if hasattr(res, "rowcount") else 0
    else:
        async with get_db() as conn:
            res = await execute(conn, sql)
            await conn.commit()
            return res.rowcount if hasattr(res, "rowcount") else 0


async def get_trace(trace_id: str, db: aiosqlite.Connection | None = None) -> dict[str, Any] | None:
    """Retrieve full trace details by ID."""
    sql = "SELECT * FROM traces WHERE id = ?"
    if db is not None:
        row = await fetch_one(db, sql, (trace_id,))
    else:
        async with get_db() as conn:
            row = await fetch_one(conn, sql, (trace_id,))

    if not row:
        return None

    d = dict(row)
    if d.get("steps") and isinstance(d["steps"], str):
        try:
            d["steps"] = json.loads(d["steps"])
        except Exception:
            pass
    if d.get("token_counts") and isinstance(d["token_counts"], str):
        try:
            d["token_counts"] = json.loads(d["token_counts"])
        except Exception:
            pass
    if d.get("feedback") and isinstance(d["feedback"], str):
        try:
            d["feedback"] = json.loads(d["feedback"])
        except Exception:
            pass
    return d


async def list_traces(
    limit: int = 20,
    source: str | None = None,
    status: str | None = None,
    db: aiosqlite.Connection | None = None,
) -> list[dict[str, Any]]:
    """List most recent traces."""
    query = "SELECT * FROM traces"
    params: list[Any] = []
    clauses: list[str] = []

    if source:
        clauses.append("source = ?")
        params.append(source)
    if status:
        clauses.append("final_status = ?")
        params.append(status)

    if clauses:
        query += " WHERE " + " AND ".join(clauses)

    query += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)

    if db is not None:
        rows = await fetch_all(db, query, tuple(params))
    else:
        async with get_db() as conn:
            rows = await fetch_all(conn, query, tuple(params))

    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        if d.get("steps") and isinstance(d["steps"], str):
            try:
                d["steps"] = json.loads(d["steps"])
            except Exception:
                pass
        if d.get("token_counts") and isinstance(d["token_counts"], str):
            try:
                d["token_counts"] = json.loads(d["token_counts"])
            except Exception:
                pass
        if d.get("feedback") and isinstance(d["feedback"], str):
            try:
                d["feedback"] = json.loads(d["feedback"])
            except Exception:
                pass
        out.append(d)
    return out


async def get_trace_stats(days: int = 7, db: aiosqlite.Connection | None = None) -> dict[str, Any]:
    """Calculate aggregate statistics over the last N days."""
    from datetime import timedelta
    since_dt = datetime.now(UTC) - timedelta(days=days)
    since_iso = since_dt.isoformat()

    sql = "SELECT source, final_status, started_at, finished_at, engine, model, error_text FROM traces WHERE started_at >= ?"
    if db is not None:
        rows = await fetch_all(db, sql, (since_iso,))
    else:
        async with get_db() as conn:
            rows = await fetch_all(conn, sql, (since_iso,))

    counts_by_source: dict[str, int] = {}
    counts_by_status: dict[str, int] = {}
    durations_ms: list[float] = []
    fallback_count = 0

    for r in rows:
        src = str(r["source"])
        st = str(r["final_status"])
        counts_by_source[src] = counts_by_source.get(src, 0) + 1
        counts_by_status[st] = counts_by_status.get(st, 0) + 1

        # Check for fallback
        err = str(r["error_text"] or "")
        engine = str(r["engine"] or "")
        if "fallback" in err.lower() or "ollama" in engine.lower():
            fallback_count += 1

        if r["started_at"] and r["finished_at"]:
            try:
                t0 = datetime.fromisoformat(str(r["started_at"]))
                t1 = datetime.fromisoformat(str(r["finished_at"]))
                dur = (t1 - t0).total_seconds() * 1000.0
                if dur >= 0:
                    durations_ms.append(dur)
            except Exception:
                pass

    median_duration = statistics.median(durations_ms) if durations_ms else 0.0

    return {
        "days": days,
        "total_traces": len(rows),
        "by_source": counts_by_source,
        "by_status": counts_by_status,
        "median_duration_ms": round(median_duration, 2),
        "fallback_count": fallback_count,
    }


async def record_trace_feedback(
    trace_id: str,
    rating: str,
    note: str | None = None,
    topic: str | None = None,
    db: aiosqlite.Connection | None = None,
) -> str:
    """Record like/dislike feedback on a trace and insert into prompt_feedback if topic given."""
    fb_dict = {
        "rating": rating,
        "note": redact_secrets(note) if note else None,
        "topic": topic,
        "timestamp": _utc_now_iso(),
    }
    fb_json = json.dumps(fb_dict)

    pfb_id = f"pfb_{uuid.uuid4().hex[:10]}"

    async def _save(conn: aiosqlite.Connection) -> None:
        await execute(
            conn,
            "UPDATE traces SET feedback = ? WHERE id = ?",
            (fb_json, trace_id),
        )
        if topic:
            await execute(
                conn,
                """
                INSERT INTO prompt_feedback (id, topic, trace_id, note, created_at, applied)
                VALUES (?, ?, ?, ?, ?, 0)
                """,
                (pfb_id, topic, trace_id, redact_secrets(note) if note else None, _utc_now_iso()),
            )
        await conn.commit()

    if db is not None:
        await _save(db)
    else:
        async with get_db() as conn:
            await _save(conn)

    return pfb_id
