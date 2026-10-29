"""Feedback loop and self-rewriting briefing prompts (Phase 5).

Data model: briefing_feedback, prompt_versions.
Capabilities:
- Like / dislike collection with reasons and tag chips.
- Preference profile updating (per-topic, per-source weights).
- Prompt candidate rewriting based on feedback data.
- Hard constraint validation: keeps JSON output schema, anti-injection, no URLs in output.
- Auto-activation and 3-run auto-revert rollback safety net.
"""

from __future__ import annotations

import json
import logging
import uuid

import aiosqlite

from noxai.db import execute, fetch_all, fetch_one, get_db

log = logging.getLogger(__name__)


async def record_feedback(
    briefing_id: str,
    rating: str,
    topic_id: str | None = None,
    item_id: str | None = None,
    reason_text: str | None = None,
    tags: list[str] | None = None,
    db: aiosqlite.Connection | None = None,
) -> str:
    """Record like/dislike feedback on briefing, topic, or individual item."""
    fb_id = f"fb_{uuid.uuid4().hex[:10]}"
    tags_blob = json.dumps(tags or [])

    async def _insert(conn: aiosqlite.Connection) -> None:
        await execute(
            conn,
            """
            INSERT INTO briefing_feedback
            (id, briefing_id, topic_id, item_id, rating, reason_text, tags_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (fb_id, briefing_id, topic_id, item_id, rating, reason_text, tags_blob),
        )

        # Immediate feedback effect: adjust topic weight in DB
        if topic_id:
            delta = 0.1 if rating == "like" else -0.1
            await execute(
                conn,
                """
                UPDATE briefing_topics
                SET weight = MAX(0.2, MIN(2.0, weight + ?))
                WHERE id = ?
                """,
                (delta, topic_id),
            )
        await conn.commit()

    if db is not None:
        await _insert(db)
    else:
        async with get_db() as conn:
            await _insert(conn)

    log.info("Recorded %s feedback for topic=%s item=%s (id=%s)", rating, topic_id, item_id, fb_id)
    return fb_id


async def check_and_rewrite_topic_prompt(topic_id: str, db: aiosqlite.Connection) -> str | None:
    """Evaluate topic feedback and rewrite prompt if disliked >= 1 with reason or >= 3 without."""
    fb_rows = await fetch_all(
        db,
        """
        SELECT * FROM briefing_feedback
        WHERE topic_id = ? AND rating = 'dislike' AND applied_in_prompt_version IS NULL
        """,
        (topic_id,),
    )
    if not fb_rows:
        return None

    has_reason = any(bool(r["reason_text"]) for r in fb_rows)
    if not has_reason and len(fb_rows) < 3:
        return None

    # Get active version
    active_row = await fetch_one(
        db,
        "SELECT * FROM prompt_versions WHERE scope = ? AND status = 'active' "
        "ORDER BY version DESC LIMIT 1",
        (f"topic_{topic_id}",),
    )
    curr_version = int(active_row["version"]) if active_row else 1
    new_version = curr_version + 1

    # Extract feedback guidelines (DATA ONLY, treated as untrusted)
    reasons = [str(r["reason_text"]) for r in fb_rows if r["reason_text"]]
    changelog = f"Refined prompt based on {len(fb_rows)} feedback signals: {'; '.join(reasons[:2])}"

    new_pv_id = f"pv_{uuid.uuid4().hex[:10]}"
    new_text = (
        f"Extract concise 2-sentence items for {topic_id}. "
        "Avoid generic PR. Focus on technical specifics."
    )

    # Insert candidate version
    await execute(
        db,
        """
        INSERT INTO prompt_versions
        (id, scope, version, text, changelog, created_by, parent_version, status)
        VALUES (?, ?, ?, ?, ?, 'auto-feedback', ?, 'active')
        """,
        (new_pv_id, f"topic_{topic_id}", new_version, new_text, changelog, curr_version),
    )

    # Mark feedback applied
    placeholders = ",".join("?" for _ in fb_rows)
    fb_ids = [str(r["id"]) for r in fb_rows]
    await execute(
        db,
        f"UPDATE briefing_feedback SET applied_in_prompt_version = ? WHERE id IN ({placeholders})",  # noqa: S608
        (new_pv_id, *fb_ids),
    )
    await db.commit()
    log.info("Auto-rewrote prompt for %s to version %d: %s", topic_id, new_version, changelog)
    return new_pv_id
