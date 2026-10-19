"""Built-in memory tools for saving, recalling, forgetting, and pinning facts."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from noxai.db import get_db
from noxai.memory.embed import embed_text
from noxai.memory.extract import contains_secret
from noxai.memory.recall import recall_memories
from noxai.memory.store import (
    create_fact,
    get_fact_by_key,
    hard_delete_fact,
    list_facts,
    update_fact,
)
from noxai.memory.store import (
    pin_fact as store_pin_fact,
)
from noxai.models.router import ModelRouter
from noxai.tools.registry import ToolContext, ToolResult, tool

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# save_fact
# ---------------------------------------------------------------------------


class SaveFactParams(BaseModel):
    key: str = Field(..., description="Unique snake_case identifier for the fact.")
    value: str = Field(..., description="The factual information to remember.")
    category: str = Field(
        "other",
        description="Category: profile, preference, project, person, device, other.",
    )
    confidence: float = Field(1.0, ge=0.0, le=1.0, description="Confidence score 0.0 to 1.0.")
    pinned: bool = Field(False, description="Whether to pin this fact to the top of memory.")


@tool(
    name="save_fact",
    description="Save or update a durable fact in NoX's long-term memory.",
    params=SaveFactParams,
    tier="write",
    is_meta_tool=True,
)
async def save_fact(params: SaveFactParams, ctx: ToolContext) -> ToolResult:
    """Save or update a fact in long-term memory."""
    key = params.key.strip().lower()
    value = params.value.strip()

    if not key or not value:
        return ToolResult(
            ok=False,
            output="Error: Both key and value must be non-empty strings.",
            summary="Failed to save fact: empty key or value.",
        )

    if contains_secret(key) or contains_secret(value):
        return ToolResult(
            ok=False,
            output="Error: Fact contains sensitive secrets/credentials and was refused.",
            summary="Refused to save fact containing sensitive credentials.",
        )

    router = ctx.router or ModelRouter(ctx.config)

    async def _execute_with_conn(db: Any) -> ToolResult:
        emb = await embed_text(f"{key}: {value}", router)
        existing = await get_fact_by_key(db, key)

        if existing is not None:
            await update_fact(
                db,
                existing.id,
                value=value,
                category=params.category,
                confidence=params.confidence,
                pinned=params.pinned,
                embedding=emb,
                changed_by="agent",
            )
            return ToolResult(
                ok=True,
                output=f"Updated fact '{key}' ({params.category}): {value}",
                summary=f"Updated fact '{key}'.",
            )
        else:
            await create_fact(
                db,
                key=key,
                value=value,
                category=params.category,
                confidence=params.confidence,
                pinned=params.pinned,
                source="chat",
                embedding=emb,
            )
            return ToolResult(
                ok=True,
                output=f"Remembered fact '{key}' ({params.category}): {value}",
                summary=f"Saved fact '{key}'.",
            )

    if ctx.db is not None:
        return await _execute_with_conn(ctx.db)
    else:
        async with get_db() as db:
            return await _execute_with_conn(db)


# ---------------------------------------------------------------------------
# recall_facts
# ---------------------------------------------------------------------------


class RecallFactsParams(BaseModel):
    query: str = Field(..., description="Query string to search for relevant memories.")
    limit: int = Field(5, ge=1, le=20, description="Maximum number of facts to return.")


@tool(
    name="recall_facts",
    description="Search long-term memory for facts and episodes matching a query.",
    params=RecallFactsParams,
    tier="read",
)
async def recall_facts(params: RecallFactsParams, ctx: ToolContext) -> ToolResult:
    """Retrieve relevant memories using hybrid search."""
    router = ctx.router or ModelRouter(ctx.config)

    async def _execute_with_conn(db: Any) -> ToolResult:
        result = await recall_memories(
            params.query,
            db=db,
            router=router,
            config=ctx.config,
            top_k=params.limit,
        )

        if not result.facts and not result.episodes:
            return ToolResult(
                ok=True,
                output="No relevant memories found.",
                summary="Recalled 0 memories.",
            )

        output_lines: list[str] = []
        if result.facts:
            output_lines.append("Facts:")
            for f in result.facts:
                pin_mark = " [pinned]" if f.pinned else ""
                output_lines.append(f"- {f.key} ({f.category}){pin_mark}: {f.value}")

        if result.episodes:
            output_lines.append("\nEpisodes:")
            for e in result.episodes:
                output_lines.append(f"- ({e.ts[:10]}): {e.summary}")

        formatted = "\n".join(output_lines)
        return ToolResult(
            ok=True,
            output=formatted,
            summary=f"Recalled {len(result.facts)} fact(s) and {len(result.episodes)} episode(s).",
        )

    if ctx.db is not None:
        return await _execute_with_conn(ctx.db)
    else:
        async with get_db() as db:
            return await _execute_with_conn(db)


# ---------------------------------------------------------------------------
# forget_fact
# ---------------------------------------------------------------------------


class ForgetFactParams(BaseModel):
    key: str = Field(..., description="The key or description of the fact to forget.")
    confirmed: bool = Field(False, description="Confirmation flag if >5 episodes match.")


@tool(
    name="forget_fact",
    description="Permanently delete a fact, its history, embeddings, and related episode entries.",
    params=ForgetFactParams,
    tier="write",
    is_meta_tool=True,
)
async def forget_fact(params: ForgetFactParams, ctx: ToolContext) -> ToolResult:
    """Hard-delete fact, history rows, vector embedding, and related episodes."""
    key = params.key.strip().lower()

    async def _execute_with_conn(db: Any) -> ToolResult:
        existing = await get_fact_by_key(db, key)
        if existing is None:
            # Try searching by list_facts to match partial key
            facts = await list_facts(db, limit=50)
            matches = [f for f in facts if key in f.key.lower()]
            if matches:
                existing = matches[0]

        if existing is None:
            return ToolResult(
                ok=False,
                output=f"No fact found matching '{key}'.",
                summary=f"Fact '{key}' not found.",
            )

        res = await hard_delete_fact(db, existing.id, confirmed=params.confirmed)
        if res.get("status") == "requires_approval":
            preview_lines = [f"- #{p['id']}: {p['preview']}..." for p in res.get("preview", [])]
            preview_text = "\n".join(preview_lines)
            if ctx.request_approval:
                reason = (
                    f"Deleting fact '{existing.key}' would purge "
                    f"{res['matching_count']} episodes (>5).\n"
                    f"Preview of episodes:\n{preview_text}"
                )
                approved = await ctx.request_approval(
                    ctx.run_id or "run-forget-fact",
                    "forget_fact",
                    {"key": key, "confirmed": True},
                    "write",
                    reason,
                )
                if approved:
                    res = await hard_delete_fact(db, existing.id, confirmed=True)
                else:
                    return ToolResult(
                        ok=False,
                        output=(
                            f"Purge was denied. Matched {res['matching_count']} episodes.\n"
                            f"Preview:\n{preview_text}"
                        ),
                        summary="Purge denied: >5 episodes matched.",
                    )
            else:
                return ToolResult(
                    ok=False,
                    output=(
                        f"Approval required: deleting fact '{existing.key}' matches "
                        f"{res['matching_count']} episodes (>5).\n"
                        f"Preview:\n{preview_text}\n"
                        f"Re-run with confirmed=True or approve to proceed."
                    ),
                    summary=f"Requires approval: {res['matching_count']} episodes matched.",
                )

        fact_info = res.get("fact", {})
        msg = (
            f"Permanently forgot fact '{existing.key}' (id={existing.id}).\n"
            f"- Deleted fact: {fact_info.get('key', existing.key)} = "
            f"{fact_info.get('value', existing.value)}\n"
            f"- Deleted history rows: {res.get('history_rows', [])}\n"
            f"- Deleted vectors: fact={res.get('vectors', {}).get('fact_vector')}, "
            f"episodes={res.get('vectors', {}).get('episode_vectors', [])}\n"
            f"- Rebuilt FTS: facts={res.get('fts_entries', {}).get('facts_rebuilt')}, "
            f"episodes={res.get('fts_entries', {}).get('episodes_rebuilt')}\n"
            f"- Deleted episode IDs: {res.get('episode_ids', [])}"
        )
        return ToolResult(
            ok=True,
            output=msg,
            summary=(
                f"Hard-deleted fact '{existing.key}'. "
                f"Purged {len(res.get('episode_ids', []))} episode(s)."
            ),
        )

    if ctx.db is not None:
        return await _execute_with_conn(ctx.db)
    else:
        async with get_db() as db:
            return await _execute_with_conn(db)


# ---------------------------------------------------------------------------
# pin_fact
# ---------------------------------------------------------------------------


class PinFactParams(BaseModel):
    key: str = Field(..., description="Key of the fact to pin or unpin.")
    pinned: bool = Field(True, description="True to pin, False to unpin.")


@tool(
    name="pin_fact",
    description="Pin or unpin a fact so it always ranks at the top of memory.",
    params=PinFactParams,
    tier="write",
    is_meta_tool=True,
)
async def pin_fact(params: PinFactParams, ctx: ToolContext) -> ToolResult:
    """Pin or unpin a fact."""
    key = params.key.strip().lower()

    async def _execute_with_conn(db: Any) -> ToolResult:
        existing = await get_fact_by_key(db, key)
        if existing is None:
            return ToolResult(
                ok=False,
                output=f"Fact '{key}' not found.",
                summary=f"Fact '{key}' not found.",
            )

        await store_pin_fact(db, existing.id, pinned=params.pinned)
        status = "pinned" if params.pinned else "unpinned"
        return ToolResult(
            ok=True,
            output=f"Fact '{key}' is now {status}.",
            summary=f"Set '{key}' pinned={params.pinned}.",
        )

    if ctx.db is not None:
        return await _execute_with_conn(ctx.db)
    else:
        async with get_db() as db:
            return await _execute_with_conn(db)
