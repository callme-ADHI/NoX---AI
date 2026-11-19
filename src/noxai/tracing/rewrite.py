# Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
# commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
# Modified for NoX: Feedback-driven prompt rewriting with validation gates and version rollback.

"""Feedback-driven prompt improvement and rollback for briefing topics (Workstream C).

Workflow:
1. Fetch current active prompt text for topic.
2. Fetch last 5 unapplied dislike feedback signals and notes.
3. Query model (via engine abstraction respecting health and fallback policy).
4. Validate:
   - Non-empty
   - Under length limit (<= 2000 chars)
   - Must mention the topic
   - Must contain no secrets
5. Save old prompt as inactive, new prompt as active version.
6. Mark feedback as applied.
7. Support instant rollback via rollback_topic_prompt().
"""

from __future__ import annotations

from datetime import UTC, datetime
import logging
import uuid
from typing import Any

import aiosqlite

from noxai.db import execute, fetch_all, fetch_one, get_db
from noxai.models.base import Message
from noxai.models.engine import get_engine
from noxai.tracing.redact import contains_secret, redact_secrets

log = logging.getLogger(__name__)

MAX_PROMPT_LENGTH = 2000


def _normalize_topic_scope(topic: str) -> str:
    """Normalize topic identifier to a standard scope string."""
    clean = topic.strip()
    return clean if clean.startswith("topic_") else f"topic_{clean}"


async def get_active_topic_prompt(
    topic: str,
    db: aiosqlite.Connection | None = None,
) -> tuple[str, int]:
    """Return the active prompt text and version number for a topic."""
    scope = _normalize_topic_scope(topic)
    sql = "SELECT version, text FROM prompt_versions WHERE scope = ? AND status = 'active' ORDER BY version DESC LIMIT 1"

    if db is not None:
        row = await fetch_one(db, sql, (scope,))
    else:
        async with get_db() as conn:
            row = await fetch_one(conn, sql, (scope,))

    if row:
        return str(row["text"]), int(row["version"])

    # Fallback to initial baseline
    default_text = (
        f"Extract concise, verified technical intelligence for {topic}. "
        "Focus on architecture, releases, and actionable details. Avoid marketing fluff."
    )
    return default_text, 1


async def improve_topic_prompt(
    topic: str,
    db: aiosqlite.Connection | None = None,
) -> dict[str, Any]:
    """Read last 5 disliked traces/notes for topic and generate a validated improved prompt."""
    scope = _normalize_topic_scope(topic)
    bare_topic = topic.replace("topic_", "")

    async def _execute_improve(conn: aiosqlite.Connection) -> dict[str, Any]:
        # 1. Fetch unapplied feedback
        fb_rows = await fetch_all(
            conn,
            """
            SELECT id, trace_id, note, created_at
            FROM prompt_feedback
            WHERE (topic = ? OR topic = ?) AND applied = 0
            ORDER BY created_at DESC
            LIMIT 5
            """,
            (topic, bare_topic),
        )

        if not fb_rows:
            # Fall back to check briefing_feedback if prompt_feedback had none
            bf_rows = await fetch_all(
                conn,
                """
                SELECT id, reason_text as note, created_at
                FROM briefing_feedback
                WHERE (topic_id = ? OR topic_id = ?) AND rating = 'dislike' AND applied_in_prompt_version IS NULL
                ORDER BY created_at DESC
                LIMIT 5
                """,
                (topic, bare_topic),
            )
            if not bf_rows:
                raise ValueError(f"No unapplied dislike feedback found for topic '{topic}'.")
            notes = [str(r["note"]) for r in bf_rows if r["note"]]
            fb_ids = [str(r["id"]) for r in bf_rows]
            is_briefing_feedback = True
        else:
            notes = [str(r["note"]) for r in fb_rows if r["note"]]
            fb_ids = [str(r["id"]) for r in fb_rows]
            is_briefing_feedback = False

        if not notes:
            notes = ["User disliked recent outputs as generic, irrelevant, or repetitive."]

        current_prompt, current_version = await get_active_topic_prompt(topic, db=conn)

        # Ensure baseline version 1 row exists in DB
        existing_v1 = await fetch_one(conn, "SELECT id FROM prompt_versions WHERE scope = ? AND version = 1", (scope,))
        if not existing_v1:
            base_id = f"pv_base_{uuid.uuid4().hex[:8]}"
            await execute(
                conn,
                """
                INSERT INTO prompt_versions (id, scope, version, text, changelog, created_by, status)
                VALUES (?, ?, 1, ?, 'Initial baseline prompt', 'seed', 'candidate')
                """,
                (base_id, scope, current_prompt),
            )
            current_version = 1

        # 2. Build prompt generation request
        notes_text = "\n".join(f"- {n}" for n in notes)
        system_instruction = (
            f"You are an AI prompt optimizer for NoX. Your job is to improve the briefing extraction prompt for topic '{topic}'.\n"
            f"Current extraction prompt:\n{current_prompt}\n\n"
            f"User feedback and dislike notes:\n{notes_text}\n\n"
            f"Rules for the rewritten prompt:\n"
            f"1. Must directly address user critique and feedback.\n"
            f"2. Must explicitly mention '{topic}'.\n"
            f"3. Must be strictly under {MAX_PROMPT_LENGTH} characters.\n"
            f"4. Do NOT output markdown code fences, greetings, or explanations. Output ONLY the new prompt text.\n"
        )

        # 3. Request improved prompt from engine layer
        engine = get_engine("nvidia")
        model_name = "meta/llama-3.3-70b-instruct"
        try:
            # Probe engine health
            health = await engine.health()
            if health.status == "down":
                log.warning("NvidiaEngine down (%s), trying ollama", health.reason)
                engine = get_engine("ollama")
                model_name = "llama3.2:3b"

            res = await engine.generate(
                model=model_name,
                messages=[Message(role="user", content=system_instruction)],
                temperature=0.3,
                max_tokens=500,
            )
            candidate = res.get("text", "").strip()
        except Exception as exc:
            log.warning("Engine call failed during improve_topic_prompt (%s), using local rule rewrite", exc)
            candidate = (
                f"Extract concise, verified technical intelligence for {topic}. "
                f"Strict constraints based on feedback: {'; '.join(notes[:2])}. "
                "Focus on architecture, releases, and actionable details. Avoid generic PR."
            )

        # Clean candidate
        if candidate.startswith("```"):
            candidate = candidate.strip("`").strip()
            if candidate.lower().startswith("text") or candidate.lower().startswith("markdown"):
                candidate = candidate.split("\n", 1)[-1].strip()

        # 4. Strict Validation
        if not candidate:
            raise ValueError("Rewritten prompt candidate is empty.")
        if len(candidate) > MAX_PROMPT_LENGTH:
            raise ValueError(f"Rewritten prompt exceeds limit ({len(candidate)} > {MAX_PROMPT_LENGTH}).")
        if bare_topic.lower() not in candidate.lower() and topic.lower() not in candidate.lower():
            raise ValueError(f"Rewritten prompt failed to mention topic '{topic}'.")
        if contains_secret(candidate):
            raise ValueError("Rewritten prompt contains forbidden secret or token pattern.")

        # 5. Persist version progression
        new_version = current_version + 1
        new_pv_id = f"pv_{uuid.uuid4().hex[:10]}"
        changelog = f"Refined from {len(fb_ids)} dislike feedback notes: {'; '.join(notes[:2])}"

        # Deactivate old version
        await execute(
            conn,
            "UPDATE prompt_versions SET status = 'candidate' WHERE scope = ? AND status = 'active'",
            (scope,),
        )

        # Insert new active version
        await execute(
            conn,
            """
            INSERT INTO prompt_versions (id, scope, version, text, changelog, created_by, parent_version, status)
            VALUES (?, ?, ?, ?, ?, 'auto-feedback', ?, 'active')
            """,
            (new_pv_id, scope, new_version, candidate, changelog, current_version),
        )

        # 6. Mark feedback as applied
        if is_briefing_feedback:
            placeholders = ",".join("?" for _ in fb_ids)
            await execute(
                conn,
                f"UPDATE briefing_feedback SET applied_in_prompt_version = ? WHERE id IN ({placeholders})",  # noqa: S608
                (new_pv_id, *fb_ids),
            )
        else:
            placeholders = ",".join("?" for _ in fb_ids)
            await execute(
                conn,
                f"UPDATE prompt_feedback SET applied = 1 WHERE id IN ({placeholders})",  # noqa: S608
                (*fb_ids,),
            )

        await conn.commit()
        log.info("Successfully improved topic prompt for %s (v%d -> v%d)", topic, current_version, new_version)

        return {
            "topic": topic,
            "version": new_version,
            "old_prompt": current_prompt,
            "new_prompt": candidate,
            "applied_count": len(fb_ids),
            "changelog": changelog,
        }

    if db is not None:
        return await _execute_improve(db)
    else:
        async with get_db() as conn:
            return await _execute_improve(conn)


async def get_topic_prompt_history(
    topic: str,
    db: aiosqlite.Connection | None = None,
) -> list[dict[str, Any]]:
    """List all version rows for a topic."""
    scope = _normalize_topic_scope(topic)
    sql = "SELECT id, scope, version, text, changelog, created_by, parent_version, status, created_at FROM prompt_versions WHERE scope = ? ORDER BY version ASC"

    if db is not None:
        rows = await fetch_all(db, sql, (scope,))
    else:
        async with get_db() as conn:
            rows = await fetch_all(conn, sql, (scope,))

    return [dict(r) for r in rows]


async def rollback_topic_prompt(
    topic: str,
    target_version: int,
    db: aiosqlite.Connection | None = None,
) -> dict[str, Any]:
    """Roll back active prompt for topic to a specific historical version."""
    scope = _normalize_topic_scope(topic)

    async def _execute_rollback(conn: aiosqlite.Connection) -> dict[str, Any]:
        target_row = await fetch_one(
            conn,
            "SELECT * FROM prompt_versions WHERE scope = ? AND version = ?",
            (scope, target_version),
        )
        if not target_row:
            raise KeyError(f"Version {target_version} does not exist for topic '{topic}'.")

        # Deactivate all versions for this scope
        await execute(
            conn,
            "UPDATE prompt_versions SET status = 'reverted' WHERE scope = ? AND status = 'active'",
            (scope,),
        )
        # Set target version active
        await execute(
            conn,
            "UPDATE prompt_versions SET status = 'active' WHERE scope = ? AND version = ?",
            (scope, target_version),
        )
        await conn.commit()
        log.info("Rolled back prompt for %s to version %d", topic, target_version)
        return {
            "topic": topic,
            "active_version": target_version,
            "text": str(target_row["text"]),
            "status": "active",
        }

    if db is not None:
        return await _execute_rollback(db)
    else:
        async with get_db() as conn:
            return await _execute_rollback(conn)
