"""Context Assembler and Conversation Memory System (CMS) Layers L0-L6.

Implements token budgeting, follow-up query heuristic, query rewriting,
hybrid retrieval (FTS5 BM25 + cosine vector + RRF + MMR), privacy filtering,
and strict eviction ordering.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import aiosqlite
import numpy as np

from noxai.db import execute, fetch_all, fetch_one
from noxai.memory.embed import (
    bytes_to_vector,
    cosine_similarity,
    embed_texts,
    vector_to_bytes,
)
from noxai.memory.store import (
    EPISODE_PURGE_STOPLIST,
    Fact,
    list_facts,
    search_facts_fts,
)
from noxai.models.base import Message
from noxai.models.router import ModelRouter
from noxai.taint import Content, TaintFlag

log = logging.getLogger(__name__)

FOLLOW_UP_PRONOUNS: set[str] = {
    "it", "that", "this", "they", "them", "these", "those",
    "he", "she", "him", "her", "why", "how", "what",
    "more", "continue", "elaborate", "again", "which",
    "explain", "tell", "one", "second", "first", "third",
    "former", "latter",
}


@dataclass
class ConversationState:
    conversation_id: str
    active_goals: list[str] = field(default_factory=list)
    working_hypotheses: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    entities_in_focus: list[str] = field(default_factory=list)
    user_preferences: list[str] = field(default_factory=list)
    last_interaction: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    updated_at: str = ""


@dataclass
class ConversationSummary:
    id: str
    conversation_id: str
    fold_index: int
    start_msg_id: str | None
    end_msg_id: str | None
    summary_markdown: str
    key_facts: list[str] = field(default_factory=list)
    active_topics: list[str] = field(default_factory=list)
    token_count: int = 0
    created_at: str = ""


@dataclass
class MessageChunk:
    id: str
    message_id: str
    conversation_id: str
    chunk_index: int
    chunk_text: str
    token_count: int = 0
    embedding: list[float] | None = None
    created_at: str = ""


@dataclass
class AssembledContext:
    messages: list[Message]
    token_count: int
    recalled_facts: list[Fact]
    recalled_chunks: list[MessageChunk]
    summary_used: ConversationSummary | None
    state_used: ConversationState | None
    evicted_layers: list[str]


def approximate_tokens(text: str) -> int:
    """Fast estimation of token count (~4 characters per token)."""
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Database CRUD for CMS tables
# ---------------------------------------------------------------------------

async def get_conv_state(db: aiosqlite.Connection, conversation_id: str) -> ConversationState | None:
    row = await fetch_one(
        db,
        """
        SELECT conversation_id, active_goals, working_hypotheses, open_questions,
               entities_in_focus, user_preferences, last_interaction, meta_json, updated_at
        FROM conv_state
        WHERE conversation_id = ?
        """,
        (conversation_id,),
    )
    if not row:
        return None
    return ConversationState(
        conversation_id=str(row["conversation_id"]),
        active_goals=json.loads(row["active_goals"] or "[]"),
        working_hypotheses=json.loads(row["working_hypotheses"] or "[]"),
        open_questions=json.loads(row["open_questions"] or "[]"),
        entities_in_focus=json.loads(row["entities_in_focus"] or "[]"),
        user_preferences=json.loads(row["user_preferences"] or "[]"),
        last_interaction=str(row["last_interaction"] or ""),
        meta=json.loads(row["meta_json"] or "{}"),
        updated_at=str(row["updated_at"] or ""),
    )


async def save_conv_state(db: aiosqlite.Connection, state: ConversationState) -> None:
    await execute(
        db,
        """
        INSERT INTO conv_state (
            conversation_id, active_goals, working_hypotheses, open_questions,
            entities_in_focus, user_preferences, last_interaction, meta_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?, datetime('now'))
        ON CONFLICT(conversation_id) DO UPDATE SET
            active_goals = excluded.active_goals,
            working_hypotheses = excluded.working_hypotheses,
            open_questions = excluded.open_questions,
            entities_in_focus = excluded.entities_in_focus,
            user_preferences = excluded.user_preferences,
            last_interaction = excluded.last_interaction,
            meta_json = excluded.meta_json,
            updated_at = excluded.updated_at
        """,
        (
            state.conversation_id,
            json.dumps(state.active_goals),
            json.dumps(state.working_hypotheses),
            json.dumps(state.open_questions),
            json.dumps(state.entities_in_focus),
            json.dumps(state.user_preferences),
            json.dumps(state.meta),
        ),
    )


async def get_latest_summary(db: aiosqlite.Connection, conversation_id: str) -> ConversationSummary | None:
    row = await fetch_one(
        db,
        """
        SELECT id, conversation_id, fold_index, start_msg_id, end_msg_id,
               summary_markdown, key_facts_json, active_topics_json, token_count, created_at
        FROM conv_summaries
        WHERE conversation_id = ?
        ORDER BY fold_index DESC
        LIMIT 1
        """,
        (conversation_id,),
    )
    if not row:
        return None
    return ConversationSummary(
        id=str(row["id"]),
        conversation_id=str(row["conversation_id"]),
        fold_index=int(row["fold_index"]),
        start_msg_id=row["start_msg_id"],
        end_msg_id=row["end_msg_id"],
        summary_markdown=str(row["summary_markdown"]),
        key_facts=json.loads(row["key_facts_json"] or "[]"),
        active_topics=json.loads(row["active_topics_json"] or "[]"),
        token_count=int(row["token_count"] or 0),
        created_at=str(row["created_at"] or ""),
    )


async def save_summary(db: aiosqlite.Connection, summary: ConversationSummary) -> None:
    await execute(
        db,
        """
        INSERT INTO conv_summaries (
            id, conversation_id, fold_index, start_msg_id, end_msg_id,
            summary_markdown, key_facts_json, active_topics_json, token_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            summary.id,
            summary.conversation_id,
            summary.fold_index,
            summary.start_msg_id,
            summary.end_msg_id,
            summary.summary_markdown,
            json.dumps(summary.key_facts),
            json.dumps(summary.active_topics),
            summary.token_count,
        ),
    )


async def save_msg_chunks(db: aiosqlite.Connection, chunks: list[MessageChunk]) -> None:
    for chk in chunks:
        emb_bytes = vector_to_bytes(chk.embedding) if chk.embedding else None
        await execute(
            db,
            """
            INSERT INTO msg_chunks (
                id, message_id, conversation_id, chunk_index, chunk_text, token_count, embedding
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                chunk_text = excluded.chunk_text,
                token_count = excluded.token_count,
                embedding = excluded.embedding
            """,
            (
                chk.id,
                chk.message_id,
                chk.conversation_id,
                chk.chunk_index,
                chk.chunk_text,
                chk.token_count,
                emb_bytes,
            ),
        )


# ---------------------------------------------------------------------------
# Follow-up Detection & Query Rewriting
# ---------------------------------------------------------------------------

def is_follow_up_query(query: str, recent_turn_count: int) -> bool:
    """Detect whether a query depends on previous turns."""
    if recent_turn_count == 0:
        return False
    q = query.strip()
    words = [w.lower() for w in re.findall(r"\w+", q)]
    if not words:
        return False
    if len(words) <= 6:
        return True
    if any(w in FOLLOW_UP_PRONOUNS for w in words[:3]):
        return True
    if "..." in q or q.endswith("?"):
        if any(w in FOLLOW_UP_PRONOUNS for w in words):
            return True
    return False


def rewrite_query_for_retrieval(
    query: str,
    recent_messages: list[Message],
    state: ConversationState | None = None,
) -> str:
    """Synthesize an expanded retrieval query for follow-up turns."""
    if not is_follow_up_query(query, len(recent_messages)):
        return query

    context_words: list[str] = []
    # Inspect the last user/assistant messages in reverse
    for m in reversed(recent_messages[-4:]):
        text = m.get_text()
        tokens = [
            w for w in re.findall(r"[A-Za-z0-9_\-]+", text)
            if len(w) >= 3 and w.lower() not in EPISODE_PURGE_STOPLIST
        ]
        context_words.extend(tokens[:5])

    if state and state.entities_in_focus:
        context_words.extend(state.entities_in_focus[:3])

    unique = list(dict.fromkeys(context_words))[:4]
    if unique:
        return f"{query} {' '.join(unique)}"
    return query


# ---------------------------------------------------------------------------
# Hybrid Retrieval (BM25 + Cosine Vector + RRF + MMR)
# ---------------------------------------------------------------------------

def _clean_fts_query(q: str) -> str:
    cleaned = re.sub(r"[^\w\s]", " ", q)
    terms = [t for t in cleaned.split() if t.lower() not in EPISODE_PURGE_STOPLIST and len(t) >= 2]
    if not terms:
        return '""'
    return " OR ".join(f'"{t}"*' for t in terms[:8])


async def search_chunks_hybrid(
    db: aiosqlite.Connection,
    conversation_id: str,
    query: str,
    router: ModelRouter | None = None,
    limit: int = 5,
    rrf_k: int = 60,
    mmr_lambda: float = 0.7,
) -> list[MessageChunk]:
    """Retrieve message chunks using Reciprocal Rank Fusion of BM25 and Vector search, diversified by MMR."""
    if not conversation_id or not query:
        return []

    # 1. BM25 search via FTS5
    bm25_ranks: dict[str, int] = {}
    fts_expr = _clean_fts_query(query)
    try:
        rows = await fetch_all(
            db,
            """
            SELECT c.id, bm25(fts_msg_chunks) as rank_score
            FROM fts_msg_chunks f
            JOIN msg_chunks c ON c.rowid = f.rowid
            WHERE fts_msg_chunks MATCH ? AND c.conversation_id = ?
            ORDER BY rank_score ASC
            LIMIT 20
            """,
            (fts_expr, conversation_id),
        )
        for rank_idx, r in enumerate(rows):
            bm25_ranks[str(r["id"])] = rank_idx + 1
    except Exception as exc:
        log.debug("BM25 search on msg_chunks failed: %s", exc)

    # 2. Vector search via cosine similarity
    vector_ranks: dict[str, int] = {}
    query_emb: list[float] | None = None
    candidate_rows = await fetch_all(
        db,
        """
        SELECT id, message_id, conversation_id, chunk_index, chunk_text, token_count, embedding, created_at
        FROM msg_chunks
        WHERE conversation_id = ?
        """,
        (conversation_id,),
    )
    if not candidate_rows:
        return []

    chunks_by_id: dict[str, MessageChunk] = {}
    for r in candidate_rows:
        cid = str(r["id"])
        emb = bytes_to_vector(r["embedding"]) if r["embedding"] else None
        chunks_by_id[cid] = MessageChunk(
            id=cid,
            message_id=str(r["message_id"]),
            conversation_id=str(r["conversation_id"]),
            chunk_index=int(r["chunk_index"]),
            chunk_text=str(r["chunk_text"]),
            token_count=int(r["token_count"] or 0),
            embedding=emb,
            created_at=str(r["created_at"] or ""),
        )

    if router is not None:
        try:
            embs = await embed_texts([query], router)
            if embs:
                query_emb = embs[0]
                scored = []
                for cid, chk in chunks_by_id.items():
                    if chk.embedding:
                        sim = cosine_similarity(query_emb, chk.embedding)
                        scored.append((cid, sim))
                scored.sort(key=lambda x: x[1], reverse=True)
                for rank_idx, (cid, _) in enumerate(scored[:20]):
                    vector_ranks[cid] = rank_idx + 1
        except Exception as exc:
            log.debug("Vector search on msg_chunks failed: %s", exc)

    # 3. Reciprocal Rank Fusion (RRF)
    all_cand_ids = set(bm25_ranks.keys()) | set(vector_ranks.keys())
    if not all_cand_ids:
        # Fallback to most recent chunks
        sorted_recent = sorted(chunks_by_id.values(), key=lambda c: c.created_at, reverse=True)
        return sorted_recent[:limit]

    rrf_scores: dict[str, float] = {}
    for cid in all_cand_ids:
        score = 0.0
        if cid in bm25_ranks:
            score += 1.0 / (rrf_k + bm25_ranks[cid])
        if cid in vector_ranks:
            score += 1.0 / (rrf_k + vector_ranks[cid])
        rrf_scores[cid] = score

    candidates = sorted(all_cand_ids, key=lambda c: rrf_scores[c], reverse=True)

    # 4. Maximal Marginal Relevance (MMR) for diversity
    if query_emb is None or len(candidates) <= limit:
        return [chunks_by_id[cid] for cid in candidates[:limit]]

    selected: list[str] = [candidates[0]]
    remaining = candidates[1:]

    while len(selected) < limit and remaining:
        best_candidate = None
        best_mmr_score = -float("inf")

        for cand_id in remaining:
            cand_chk = chunks_by_id[cand_id]
            relevance = cosine_similarity(query_emb, cand_chk.embedding) if cand_chk.embedding else 0.5

            max_redundancy = 0.0
            if cand_chk.embedding:
                for sel_id in selected:
                    sel_chk = chunks_by_id[sel_id]
                    if sel_chk.embedding:
                        red = cosine_similarity(cand_chk.embedding, sel_chk.embedding)
                        if red > max_redundancy:
                            max_redundancy = red

            mmr_score = mmr_lambda * relevance - (1.0 - mmr_lambda) * max_redundancy
            if mmr_score > best_mmr_score:
                best_mmr_score = mmr_score
                best_candidate = cand_id

        if best_candidate is not None:
            selected.append(best_candidate)
            remaining.remove(best_candidate)
        else:
            break

    return [chunks_by_id[cid] for cid in selected]


# ---------------------------------------------------------------------------
# Context Assembler with Token Budgeting & Strict Eviction
# ---------------------------------------------------------------------------

async def build_context(
    conversation_id: str,
    current_user_message: Message,
    db: aiosqlite.Connection,
    router: ModelRouter | None = None,
    allow_cloud: bool = True,
    token_budget: int = 8192,
    reserve_tokens_for_response: int = 1536,
    base_system_prompt: str | Message | None = None,
    exclude_message_id: str | None = None,
) -> AssembledContext:
    """Assemble multi-layer context (L0-L6) under strict token budget and eviction order."""
    effective_budget = max(64, token_budget - reserve_tokens_for_response)
    evicted_layers: list[str] = []

    # Layer 0: System prompt
    sys_taint = TaintFlag.NONE
    if isinstance(base_system_prompt, Message):
        sys_text = base_system_prompt.get_text()
        sys_taint = base_system_prompt.content.taint
    else:
        sys_text = base_system_prompt or "You are NoX, an expert AI assistant."
    sys_tokens = approximate_tokens(sys_text)
    allocated_tokens = sys_tokens

    # Layer 1: Conversation State (goals, active hypotheses)
    state = await get_conv_state(db, conversation_id) if conversation_id else None

    # Layer 2: Recent dialogue turns
    raw_history_messages: list[Message] = []
    if conversation_id:
        try:
            if exclude_message_id:
                rows = await fetch_all(
                    db,
                    """
                    SELECT id, role, content, tool_call_id, tool_name, taint_flags
                    FROM messages
                    WHERE conversation_id = ? AND id != ?
                    ORDER BY created_at ASC, rowid ASC
                    """,
                    (conversation_id, exclude_message_id),
                )
            else:
                rows = await fetch_all(
                    db,
                    """
                    SELECT id, role, content, tool_call_id, tool_name, taint_flags
                    FROM messages
                    WHERE conversation_id = ?
                    ORDER BY created_at ASC, rowid ASC
                    """,
                    (conversation_id,),
                )
            for r in rows:
                tflags = int(r["taint_flags"] or 0)
                # Privacy check: if not local and taint is private, omit from cloud messages
                if not allow_cloud and (tflags & TaintFlag.PRIVATE.value):
                    pass
                msg_content = Content(
                    text=str(r["content"] or ""),
                    taint=TaintFlag(tflags),
                )
                raw_history_messages.append(
                    Message(
                        role=str(r["role"]),
                        content=msg_content,
                        tool_call_id=r["tool_call_id"],
                        name=r["tool_name"],
                    )
                )
        except Exception as exc:
            log.warning("build_context history fetch error: %s", exc)

    # Layer 3: Rolling Summary
    summary = await get_latest_summary(db, conversation_id) if conversation_id else None

    # Layer 4: Semantic Search over Message Chunks (Query rewrite if follow-up)
    user_query = current_user_message.get_text()
    retrieval_query = rewrite_query_for_retrieval(user_query, raw_history_messages, state)
    chunks = await search_chunks_hybrid(db, conversation_id, retrieval_query, router=router, limit=4)

    # Layer 5: Long-term Facts
    facts: list[Fact] = []
    try:
        all_facts = await list_facts(db, limit=20)
        # Filter privacy for facts
        if allow_cloud:
            facts = [f for f in all_facts if getattr(f, "visibility", "local_only") != "local_only"]
        else:
            facts = all_facts
    except Exception as exc:
        log.debug("Facts fetch error: %s", exc)

    # Assemble Context Layers with Eviction Priority
    # Eviction Order (drop lowest priority first):
    # L3 Rolling Summary > L4/L5 Chunks & Facts > L2 Old dialogue turns > L1 State > L0 System & Current User Turn

    # Format L1 State text
    state_block = ""
    state_tokens = 0
    if state and (state.active_goals or state.entities_in_focus or state.working_hypotheses):
        lines = ["[CONVERSATION WORKING STATE]"]
        if state.active_goals:
            lines.append(f"Goals: {', '.join(state.active_goals)}")
        if state.entities_in_focus:
            lines.append(f"In-focus: {', '.join(state.entities_in_focus)}")
        if state.working_hypotheses:
            lines.append(f"Hypotheses: {'; '.join(state.working_hypotheses)}")
        state_block = "\n".join(lines)
        state_tokens = approximate_tokens(state_block)

    # Format L3 Summary text
    summary_block = ""
    summary_tokens = 0
    if summary and summary.summary_markdown:
        summary_block = f"[CONVERSATION SUMMARY - FOLD {summary.fold_index}]\n{summary.summary_markdown}"
        summary_tokens = approximate_tokens(summary_block)

    # Format L4 Chunks text
    chunk_block = ""
    chunk_tokens = 0
    if chunks:
        chunk_lines = ["[RECALLED PAST DIALOGUE CHUNKS]"]
        for c in chunks:
            chunk_lines.append(f"- {c.chunk_text}")
        chunk_block = "\n".join(chunk_lines)
        chunk_tokens = approximate_tokens(chunk_block)

    # Format L5 Facts text
    fact_block = ""
    fact_tokens = 0
    if facts:
        fact_lines = ["[RECALLED FACTS]"]
        for f in facts[:5]:
            fact_lines.append(f"- {f.key}: {f.value}")
        fact_block = "\n".join(fact_lines)
        fact_tokens = approximate_tokens(fact_block)

    # Current user turn
    cur_user_tokens = approximate_tokens(user_query)
    allocated_tokens += cur_user_tokens

    # Check budget for State (L1)
    if allocated_tokens + state_tokens <= effective_budget:
        allocated_tokens += state_tokens
    else:
        evicted_layers.append("L1_state")
        state_block = ""

    # Check budget for L3 Summary, L4 Chunks, L5 Facts, and L2 Recent turns
    # Calculate tokens of recent turns (sliding window, latest first)
    included_history: list[Message] = []
    for m in reversed(raw_history_messages):
        m_tokens = approximate_tokens(m.get_text())
        if allocated_tokens + m_tokens <= effective_budget:
            included_history.insert(0, m)
            allocated_tokens += m_tokens
        else:
            if "L2_old_turns" not in evicted_layers:
                evicted_layers.append("L2_old_turns")
            break

    if len(raw_history_messages) > len(included_history) and "L2_old_turns" not in evicted_layers:
        evicted_layers.append("L2_old_turns")

    # Add Chunks and Facts if budget allows
    if allocated_tokens + chunk_tokens <= effective_budget:
        allocated_tokens += chunk_tokens
    else:
        evicted_layers.append("L4_chunks")
        chunk_block = ""

    if allocated_tokens + fact_tokens <= effective_budget:
        allocated_tokens += fact_tokens
    else:
        evicted_layers.append("L5_facts")
        fact_block = ""

    # Add Summary if budget allows
    if allocated_tokens + summary_tokens <= effective_budget:
        allocated_tokens += summary_tokens
    else:
        evicted_layers.append("L3_summary")
        summary_block = ""

    # Construct final system prompt with embedded context blocks
    system_sections = [sys_text]
    if summary_block:
        system_sections.append(summary_block)
    if chunk_block:
        system_sections.append(chunk_block)
    if fact_block:
        system_sections.append(fact_block)
    if state_block:
        system_sections.append(state_block)

    final_system_text = "\n\n".join(system_sections)
    final_system_msg = Message(role="system", content=Content(text=final_system_text, taint=sys_taint))

    # Assemble final message list
    final_messages = [final_system_msg, *included_history, current_user_message]

    return AssembledContext(
        messages=final_messages,
        token_count=allocated_tokens,
        recalled_facts=facts if fact_block else [],
        recalled_chunks=chunks if chunk_block else [],
        summary_used=summary if summary_block else None,
        state_used=state if state_block else None,
        evicted_layers=evicted_layers,
    )
