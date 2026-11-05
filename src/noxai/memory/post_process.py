"""Post-reply background memory processing under TaskSupervisor.

Chunks and embeds new messages, updates conversation state (L1),
extracts long-term facts (L5), and evaluates rolling folds (L3).
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

import aiosqlite

from noxai.db import execute, fetch_all, fetch_one
from noxai.memory.context import (
    ConversationState,
    MessageChunk,
    approximate_tokens,
    get_conv_state,
    save_conv_state,
    save_msg_chunks,
)
from noxai.memory.embed import embed_texts
from noxai.memory.extract import extract_and_store_turn
from noxai.memory.folding import RollingSummaryFolder
from noxai.models.router import ModelRouter

log = logging.getLogger(__name__)


def chunk_message_text(text: str, max_chunk_chars: int = 500) -> list[str]:
    """Split message into overlapping or sentence-aligned chunks."""
    if len(text) <= max_chunk_chars:
        return [text] if text.strip() else []

    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current_chunk: list[str] = []
    current_len = 0

    for p in paragraphs:
        p_clean = p.strip()
        if not p_clean:
            continue
        if current_len + len(p_clean) > max_chunk_chars and current_chunk:
            chunks.append("\n\n".join(current_chunk))
            current_chunk = [p_clean]
            current_len = len(p_clean)
        else:
            current_chunk.append(p_clean)
            current_len += len(p_clean) + 2

    if current_chunk:
        chunks.append("\n\n".join(current_chunk))

    return chunks


async def process_conversation_turn(
    conversation_id: str,
    db: aiosqlite.Connection,
    router: ModelRouter | None = None,
) -> dict[str, Any]:
    """Execute complete post-turn CMS pipeline."""
    if not conversation_id:
        return {"status": "skipped", "reason": "no_conversation_id"}

    results: dict[str, Any] = {
        "conversation_id": conversation_id,
        "new_chunks": 0,
        "facts_extracted": 0,
        "state_updated": False,
        "fold_created": False,
    }

    # 1. Chunk and embed unchunked messages
    unchunked_rows = await fetch_all(
        db,
        """
        SELECT m.id, m.role, m.content
        FROM messages m
        LEFT JOIN msg_chunks c ON m.id = c.message_id
        WHERE m.conversation_id = ? AND c.id IS NULL
        ORDER BY m.created_at ASC
        """,
        (conversation_id,),
    )

    new_chunks: list[MessageChunk] = []
    texts_to_embed: list[str] = []

    for row in unchunked_rows:
        msg_id = str(row["id"])
        content_text = str(row["content"] or "")
        text_chunks = chunk_message_text(content_text)
        for idx, t in enumerate(text_chunks):
            cid = f"chk_{uuid.uuid4().hex[:12]}"
            chk = MessageChunk(
                id=cid,
                message_id=msg_id,
                conversation_id=conversation_id,
                chunk_index=idx,
                chunk_text=t,
                token_count=approximate_tokens(t),
            )
            new_chunks.append(chk)
            texts_to_embed.append(t)

    if new_chunks and router is not None:
        try:
            embeddings = await embed_texts(texts_to_embed, router)
            for chk, emb in zip(new_chunks, embeddings):
                chk.embedding = emb
        except Exception as exc:
            log.warning("Embedding generation for chunks failed: %s", exc)

    if new_chunks:
        await save_msg_chunks(db, new_chunks)
        await db.commit()
        results["new_chunks"] = len(new_chunks)

    # 2. Extract facts from latest user turn
    latest_user_row = await fetch_one(
        db,
        """
        SELECT content FROM messages
        WHERE conversation_id = ? AND role = 'user'
        ORDER BY created_at DESC LIMIT 1
        """,
        (conversation_id,),
    )
    if latest_user_row and router is not None:
        try:
            user_text = str(latest_user_row["content"] or "")
            ex_res = await extract_and_store_turn(
                db=db,
                user_message=user_text,
                assistant_message="",
                router=router,
            )
            await db.commit()
            results["facts_extracted"] = len(ex_res.facts_created) if ex_res else 0
        except Exception as exc:
            log.debug("Fact extraction failed: %s", exc)

    # 3. Update Conversation State (L1)
    state = await get_conv_state(db, conversation_id)
    if not state:
        state = ConversationState(conversation_id=conversation_id)

    if latest_user_row:
        user_query = str(latest_user_row["content"] or "")
        # Heuristic extraction of entities / goals
        words = re.findall(r"\b[A-Z][a-zA-Z0-9_\-]+\b", user_query)
        if words:
            for w in words:
                if w not in state.entities_in_focus and len(state.entities_in_focus) < 10:
                    state.entities_in_focus.append(w)
        if "?" in user_query and user_query not in state.open_questions:
            state.open_questions = [user_query[:100], *state.open_questions[:4]]

        await save_conv_state(db, state)
        await db.commit()
        results["state_updated"] = True

    # 4. Check and perform rolling fold (L3)
    folder = RollingSummaryFolder()
    if await folder.should_fold(db, conversation_id):
        summary = await folder.fold(db, conversation_id, router=router)
        await db.commit()
        results["fold_created"] = bool(summary)

    await db.commit()
    return results
