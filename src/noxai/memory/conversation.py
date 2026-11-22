# Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
# commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
# Modified for NoX: 4-part budgeted context assembly, rolling background turn summarization, and FTS5 BM25 turn retrieval.

"""Continuous conversation memory with 4-part budget context assembly (Workstream D1).

Architecture:
1. Every turn stored in messages (conversation_id, role, text, created_at, token_estimate, turn_index, topic_label).
2. 4-part model context assembly within 75% of model context budget:
   - Part 1: System prompt and pinned facts
   - Part 2: Rolling summary of older turns (conversation_summaries)
   - Part 3: Last 8 turns verbatim (auto-shrinking if over budget)
   - Part 4: Up to K retrieved older snippets relevant to new message via FTS5 BM25, each tagged with [Turn {turn_index}].
3. Background rolling summarization when verbatim history exceeds 60% of budget.
4. Recall phrasing trigger ('earlier', 'last time', 'we talked about') enabling cross-conversation search.
5. Topic continuity using topic_label per segment.
6. Absolute exception safety: falls back to verbatim recent turns without interrupting user on error.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import logging
import re
import uuid
from typing import Any, Sequence

import aiosqlite

from noxai.db import execute, fetch_all, fetch_one, get_db
from noxai.models.base import Message
from noxai.taint import Content, DataClass, TaintFlag

log = logging.getLogger(__name__)

RECALL_PHRASES = (
    "earlier",
    "last time",
    "we talked about",
    "remember when",
    "you said",
    "previously",
    "before",
    "as i mentioned",
)

DEFAULT_CONTEXT_LIMIT = 8192
VERBATIM_TURN_COUNT = 8
ANSWER_BUDGET_RATIO = 0.25  # Leave 25% for answer
SUMMARY_TRIGGER_RATIO = 0.60  # Trigger summarization when verbatim history > 60% of budget


def estimate_tokens(text: str) -> int:
    """Fast, conservative heuristic estimation of tokens for a text string."""
    if not text:
        return 0
    # Standard rule of thumb: ~4 characters per token in English
    return max(1, len(text) // 4)


async def get_or_create_active_conversation(
    db: aiosqlite.Connection,
    requested_id: str | None = None,
    default_title: str = "New Conversation",
) -> str:
    """Return requested conversation, or the most recent active conversation, or create a new one."""
    if requested_id:
        row = await fetch_one(db, "SELECT id FROM conversations WHERE id = ?", (requested_id,))
        if row:
            return str(row["id"])
        # Insert requested if not existing
        now_iso = datetime.now(UTC).isoformat()
        await execute(
            db,
            """
            INSERT INTO conversations (id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (requested_id, default_title, now_iso, now_iso),
        )
        return requested_id

    # Find latest conversation updated within the last 24 hours
    row = await fetch_one(
        db,
        """
        SELECT id FROM conversations
        WHERE deleted_at IS NULL
        ORDER BY updated_at DESC
        LIMIT 1
        """,
    )
    if row:
        return str(row["id"])

    # Create new conversation
    new_id = f"conv_{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(UTC).isoformat()
    await execute(
        db,
        """
        INSERT INTO conversations (id, title, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (new_id, default_title, now_iso, now_iso),
    )
    return new_id


async def record_conversation_turn(
    db: aiosqlite.Connection,
    conversation_id: str,
    role: str,
    text: str,
    message_id: str | None = None,
    topic_label: str = "",
    taint_flags: int = 0,
) -> tuple[str, int]:
    """Persist a conversation turn with auto-incrementing turn_index and token_estimate."""
    msg_id = message_id or f"msg_{uuid.uuid4().hex[:12]}"
    tokens = estimate_tokens(text)

    # Determine next turn_index for conversation
    max_row = await fetch_one(
        db,
        "SELECT MAX(turn_index) as max_idx FROM messages WHERE conversation_id = ?",
        (conversation_id,),
    )
    next_turn = 1
    if max_row and max_row["max_idx"] is not None:
        next_turn = int(max_row["max_idx"]) + 1

    now_iso = datetime.now(UTC).isoformat()
    await execute(
        db,
        """
        INSERT INTO messages (
            id, conversation_id, role, content, text, taint_flags,
            token_estimate, turn_index, topic_label, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            msg_id,
            conversation_id,
            role,
            text,
            text,
            taint_flags,
            tokens,
            next_turn,
            topic_label or "",
            now_iso,
        ),
    )

    # Update conversation updated_at
    await execute(
        db,
        "UPDATE conversations SET updated_at = ? WHERE id = ?",
        (now_iso, conversation_id),
    )

    return msg_id, next_turn


STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "if", "because", "as", "what",
    "which", "this", "that", "these", "those", "then", "just", "so", "than",
    "such", "both", "through", "about", "for", "is", "of", "while", "during",
    "to", "from", "in", "out", "on", "off", "over", "under", "again", "further",
    "then", "once", "here", "there", "when", "where", "why", "how", "all",
    "any", "both", "each", "few", "more", "most", "other", "some", "such",
    "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very",
    "can", "will", "just", "should", "now", "was", "were", "are", "been",
    "have", "has", "had", "having", "do", "does", "did", "doing", "would",
}


def extract_fts_query(query: str, max_tokens: int = 10) -> str:
    """Extract filtered content terms for FTS5 queries."""
    clean = re.sub(r"[^\w\s]", " ", query).strip()
    if not clean:
        return ""
    words = [w.lower() for w in clean.split() if len(w) > 2]
    content_words = [w for w in words if w not in STOP_WORDS]
    selected = content_words[:max_tokens] if content_words else words[:max_tokens]
    if not selected:
        return ""
    return " OR ".join(selected)


async def search_messages_bm25(
    db: aiosqlite.Connection,
    query: str,
    conversation_id: str | None = None,
    limit: int = 6,
    cross_conversation: bool = False,
    exclude_recent_turns: int = 8,
) -> list[dict[str, Any]]:
    """Search messages using SQLite FTS5 BM25 ranking.

    Optionally restricts to current conversation or searches across threads when recall phrases appear.
    Excludes the most recent N turns which are already presented in verbatim history.
    """
    fts_match_query = extract_fts_query(query)
    if not fts_match_query:
        return []

    # Detect max turn index for current conversation
    max_turn = 0
    if conversation_id:
        mt_row = await fetch_one(
            db,
            "SELECT MAX(turn_index) as m FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        if mt_row and mt_row["m"] is not None:
            max_turn = int(mt_row["m"])

    cutoff_turn = max(0, max_turn - exclude_recent_turns)

    results: list[dict[str, Any]] = []
    try:
        if conversation_id and not cross_conversation:
            sql = """
                SELECT m.id, m.conversation_id, m.role, m.content, coalesce(m.text, m.content) as text, m.turn_index, m.topic_label,
                       coalesce(m.taint_flags, 0) as taint_flags, bm25(fts_messages) as rank
                FROM fts_messages f
                JOIN messages m ON f.rowid = m.rowid
                WHERE fts_messages MATCH ?
                  AND m.conversation_id = ?
                  AND m.turn_index <= ?
                ORDER BY rank ASC
                LIMIT ?
            """
            rows = await fetch_all(db, sql, (fts_match_query, conversation_id, cutoff_turn, limit))
        else:
            sql = """
                SELECT m.id, m.conversation_id, m.role, m.content, coalesce(m.text, m.content) as text, m.turn_index, m.topic_label,
                       coalesce(m.taint_flags, 0) as taint_flags, bm25(fts_messages) as rank
                FROM fts_messages f
                JOIN messages m ON f.rowid = m.rowid
                WHERE fts_messages MATCH ?
                ORDER BY rank ASC
                LIMIT ?
            """
            rows = await fetch_all(db, sql, (fts_match_query, limit))

        for r in rows:
            results.append({
                "id": r["id"],
                "conversation_id": r["conversation_id"],
                "role": r["role"],
                "text": str(r["content"]),
                "turn_index": int(r["turn_index"] or 0),
                "topic_label": str(r["topic_label"] or ""),
                "taint_flags": int(r["taint_flags"] or 0),
                "score": float(r["rank"]),
            })
    except Exception as exc:
        log.warning("FTS5 message search failed: %s", exc)

    return results


async def get_latest_conversation_summary(
    db: aiosqlite.Connection,
    conversation_id: str,
    topic_label: str | None = None,
) -> dict[str, Any] | None:
    """Retrieve the most recent rolling summary for the conversation."""
    if topic_label:
        row = await fetch_one(
            db,
            """
            SELECT id, covers_turn_range, text, topic_label, token_count, created_at
            FROM conversation_summaries
            WHERE conversation_id = ? AND topic_label = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (conversation_id, topic_label),
        )
        if row:
            return dict(row)

    row = await fetch_one(
        db,
        """
        SELECT id, covers_turn_range, text, topic_label, token_count, created_at
        FROM conversation_summaries
        WHERE conversation_id = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (conversation_id,),
    )
    return dict(row) if row else None


async def trigger_rolling_summary(
    conversation_id: str,
    up_to_turn: int,
    db: aiosqlite.Connection | None = None,
) -> None:
    """Generate compact rolling summary of turns up to up_to_turn in the background.

    Preserves names, decisions, numbers, file paths, commands, and open questions.
    """
    async def _run_summary(conn: aiosqlite.Connection) -> None:
        from noxai.models.engine import get_engine
        from noxai.tracing.recorder import trace_execution

        # Fetch turns to summarize
        rows = await fetch_all(
            conn,
            """
            SELECT turn_index, role, content, topic_label
            FROM messages
            WHERE conversation_id = ? AND turn_index <= ?
            ORDER BY turn_index ASC
            """,
            (conversation_id, up_to_turn),
        )
        if not rows:
            return

        min_turn = rows[0]["turn_index"]
        max_turn = rows[-1]["turn_index"]
        turn_range = f"{min_turn}-{max_turn}"

        transcript_lines = []
        for r in rows:
            transcript_lines.append(f"Turn {r['turn_index']} ({r['role']}): {r['content']}")
        transcript_text = "\n".join(transcript_lines)

        async with trace_execution(
            source="chat",
            request_text=f"Rolling conversation summary for {conversation_id} turns {turn_range}",
            intent="summarize",
        ) as tr_rec:
            summary_prompt = (
                "You are NoX's Memory Compression Engine.\n"
                "Summarize the following conversation history into a concise, high-density factual summary.\n"
                "CRITICAL RULES:\n"
                "1. MUST retain all proper names, entities, technical decisions, numbers, ports, file paths, shell commands, and unresolved questions.\n"
                "2. Tag key details with their turn number when relevant (e.g. '[Turn 1: redis port 6380]').\n"
                "3. Keep output under 400 words without commentary or greetings.\n\n"
                f"Transcript:\n{transcript_text}"
            )

            summary_text = ""
            engine = get_engine("nvidia")
            try:
                res = await engine.generate(
                    model="meta/llama-3.3-70b-instruct",
                    messages=[Message(role="user", content=summary_prompt)],
                    temperature=0.2,
                    max_tokens=600,
                )
                summary_text = res.get("text", "").strip()
            except Exception as e_cloud:
                tr_rec.record_step("cloud_summary_failed", str(e_cloud), status="failed")
                try:
                    # Fallback to local resident model
                    loc_engine = get_engine("ollama")
                    res_loc = await loc_engine.generate(
                        model="llama3.2:3b",
                        messages=[Message(role="user", content=summary_prompt)],
                        temperature=0.2,
                        max_tokens=600,
                    )
                    summary_text = res_loc.get("text", "").strip()
                except Exception as e_loc:
                    tr_rec.record_step("local_summary_failed", str(e_loc), status="failed")
                    # Fallback to extractive key lines
                    summary_text = f"Summary of turns {turn_range}:\n" + "\n".join(
                        f"- {r['role']} (turn {r['turn_index']}): {r['content'][:120]}"
                        for r in rows[-5:]
                    )

            if summary_text:
                sum_id = f"csum_{uuid.uuid4().hex[:12]}"
                tok_cnt = estimate_tokens(summary_text)
                await execute(
                    conn,
                    """
                    INSERT INTO conversation_summaries (
                        id, conversation_id, covers_turn_range, text, token_count
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (sum_id, conversation_id, turn_range, summary_text, tok_cnt),
                )
                await conn.commit()
                tr_rec.record_step("summary_persisted", f"covers_{turn_range}", status="success")

    try:
        if db is not None:
            await _run_summary(db)
        else:
            async with get_db() as conn:
                await _run_summary(conn)
    except Exception as exc:
        log.warning("Background rolling summarization failed: %s", exc)


async def build_four_part_context(
    conversation_id: str,
    current_user_message: Message,
    db: aiosqlite.Connection,
    context_limit: int = DEFAULT_CONTEXT_LIMIT,
    base_system_prompt: str | Message | None = None,
    pinned_facts: list[str] | None = None,
    active_topic_label: str = "",
) -> list[Message]:
    """Assemble model context from 4 parts within budget.

    Budget Allocation:
    - Maximum available budget = context_limit * (1 - ANSWER_BUDGET_RATIO) (e.g. 75%)
    - Part 1: System prompt + pinned facts
    - Part 2: Rolling summary of older turns
    - Part 3: Last 8 turns verbatim (auto-shrinking if needed)
    - Part 4: Up to K retrieved older snippets relevant to new message (tagged [Turn X])
    """
    total_budget = int(context_limit * (1.0 - ANSWER_BUDGET_RATIO))
    user_query = current_user_message.get_text()
    user_tokens = estimate_tokens(user_query)

    # -------------------------------------------------------------------------
    # Part 1: System prompt and pinned facts
    # -------------------------------------------------------------------------
    if isinstance(base_system_prompt, Message):
        sys_text = base_system_prompt.get_text()
        sys_taint = base_system_prompt.content.taint
    else:
        sys_text = base_system_prompt or "You are NoX, a personal autonomous AI operating environment."
        sys_taint = TaintFlag.NONE

    facts_lines = []
    if pinned_facts:
        facts_lines.append("[PINNED FACTS]")
        for f in pinned_facts:
            facts_lines.append(f"- {f}")

    part1_text = sys_text
    if facts_lines:
        part1_text += "\n\n" + "\n".join(facts_lines)
    part1_tokens = estimate_tokens(part1_text)

    remaining_budget = max(100, total_budget - part1_tokens - user_tokens)

    # -------------------------------------------------------------------------
    # Part 2: Rolling summary of older turns
    # -------------------------------------------------------------------------
    summary_row = await get_latest_conversation_summary(db, conversation_id, topic_label=active_topic_label)
    part2_text = ""
    part2_tokens = 0
    if summary_row and summary_row.get("text"):
        part2_text = (
            f"[CONVERSATION SUMMARY - TURNS {summary_row['covers_turn_range']}]\n"
            f"{summary_row['text']}"
        )
        part2_tokens = estimate_tokens(part2_text)
        if part2_tokens > (remaining_budget // 2):
            # Truncate summary if oversized
            part2_text = part2_text[: remaining_budget * 2]
            part2_tokens = estimate_tokens(part2_text)
        remaining_budget -= part2_tokens

    # -------------------------------------------------------------------------
    # Part 3: Most recent N turns verbatim (up to 8 turns)
    # -------------------------------------------------------------------------
    history_rows = await fetch_all(
        db,
        """
        SELECT id, role, content, turn_index, token_estimate
        FROM messages
        WHERE conversation_id = ?
        ORDER BY turn_index DESC, id DESC
        LIMIT ?
        """,
        (conversation_id, VERBATIM_TURN_COUNT),
    )
    # Reverse so they are in chronological order
    recent_turns: list[dict[str, Any]] = list(reversed(history_rows))

    # Check if verbatim history exceeds 60% of total budget -> trigger background summarization
    total_verbatim_tokens = sum(int(r["token_estimate"] or estimate_tokens(str(r["content"]))) for r in recent_turns)
    if total_verbatim_tokens > (total_budget * SUMMARY_TRIGGER_RATIO) and len(recent_turns) >= VERBATIM_TURN_COUNT:
        oldest_verbatim_turn = min(int(r["turn_index"]) for r in recent_turns)
        if oldest_verbatim_turn > 1:
            # Trigger background summarization for turns prior to oldest verbatim turn
            asyncio.create_task(trigger_rolling_summary(conversation_id, oldest_verbatim_turn - 1))

    # Shrink verbatim turns if over remaining budget
    verbatim_messages: list[Message] = []
    verbatim_tokens_used = 0
    for r in reversed(recent_turns):
        t_text = str(r["content"])
        t_tokens = int(r["token_estimate"] or estimate_tokens(t_text))
        if verbatim_tokens_used + t_tokens <= remaining_budget:
            verbatim_messages.insert(0, Message(role=str(r["role"]), content=Content.chat(t_text)))
            verbatim_tokens_used += t_tokens
        else:
            break

    remaining_budget -= verbatim_tokens_used

    # -------------------------------------------------------------------------
    # Part 4: Retrieved older snippets relevant to new message (BM25 FTS5)
    # -------------------------------------------------------------------------
    has_recall_phrase = any(phrase in user_query.lower() for phrase in RECALL_PHRASES)
    retrieved_snippets: list[str] = []
    if remaining_budget > 150:
        found_snippets = await search_messages_bm25(
            db=db,
            query=user_query,
            conversation_id=conversation_id,
            limit=4,
            cross_conversation=has_recall_phrase,
            exclude_recent_turns=len(verbatim_messages),
        )
        if found_snippets:
            for snip in found_snippets:
                snip_str = f"[Turn {snip['turn_index']}]: {snip['text']}"
                snip_tok = estimate_tokens(snip_str)
                if snip_tok <= remaining_budget:
                    retrieved_snippets.append(snip_str)
                    remaining_budget -= snip_tok
                    if snip.get("taint_flags", 0) & TaintFlag.PRIVATE.value:
                        sys_taint |= TaintFlag.PRIVATE

    # -------------------------------------------------------------------------
    # Part 4b: Retrieved notes / documents if query refers to notes or vault
    # -------------------------------------------------------------------------
    note_recall_phrases = (
        "in my notes",
        "in my vault",
        "in the vault",
        "from my notes",
        "in my study",
        "what did i write about",
        "what did i write in",
        "what did i note",
        "my obsidian",
        "in notes",
    )
    has_note_phrase = any(phrase in user_query.lower() for phrase in note_recall_phrases)
    retrieved_doc_chunks: list[str] = []
    if has_note_phrase and remaining_budget > 150:
        try:
            from noxai.memory.docs import search_documents

            doc_matches = await search_documents(query=user_query, k=4, db=db)
            if doc_matches:
                for dm in doc_matches:
                    heading_info = f", Heading: {dm.heading_path}" if dm.heading_path else ""
                    dm_str = (
                        f"Source File: {dm.path} (Lines {dm.line_start}-{dm.line_end}{heading_info}):\n"
                        f"{dm.text}"
                    )
                    dm_tok = estimate_tokens(dm_str)
                    if dm_tok <= remaining_budget:
                        retrieved_doc_chunks.append(dm_str)
                        remaining_budget -= dm_tok
        except Exception as exc:
            log.warning("Document note retrieval failed: %s", exc)

    # -------------------------------------------------------------------------
    # Combine into Final Messages List
    # -------------------------------------------------------------------------
    system_sections = [part1_text]
    if part2_text:
        system_sections.append(part2_text)
    if retrieved_snippets:
        system_sections.append("[RELEVANT HISTORICAL TURNS]\n" + "\n".join(retrieved_snippets))
    if retrieved_doc_chunks:
        sys_taint |= TaintFlag.PRIVATE
        doc_header = (
            "[RELEVANT USER NOTES & DOCUMENTS]\n"
            "The following excerpts were retrieved from the user's local notes and files. "
            "Answer the user's request directly using these excerpts and ALWAYS cite the exact source file path in your reply.\n\n"
            + "\n\n---\n\n".join(retrieved_doc_chunks)
        )
        system_sections.append(doc_header)

    combined_system_text = "\n\n".join(system_sections)
    is_private_context = bool(sys_taint & TaintFlag.PRIVATE)
    final_messages = [
        Message(
            role="system",
            content=Content(
                text=combined_system_text,
                taint=sys_taint,
                data_class=DataClass.LOCAL_ONLY if is_private_context else DataClass.CHAT,
            ),
        ),
        *verbatim_messages,
        current_user_message,
    ]

    return final_messages
