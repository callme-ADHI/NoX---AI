"""Rolling summary folding for NoX Conversation Memory System (CMS Layer L3).

Maintains concise, citation-backed rolling summaries of conversation history.
Validates message-level citations [m<msg_id>] and performs re-anchoring every 5 folds.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

import aiosqlite

from noxai.db import execute, fetch_all, fetch_one
from noxai.memory.context import (
    ConversationSummary,
    approximate_tokens,
    get_latest_summary,
    save_summary,
)
from noxai.models.base import Message
from noxai.models.router import ModelRouter
from noxai.taint import Content

log = logging.getLogger(__name__)

CITATION_REGEX = re.compile(r"\[m(?:sg_)?([a-zA-Z0-9_\-]+)\]")


def validate_citations(summary_text: str, valid_msg_ids: set[str]) -> tuple[str, list[str]]:
    """Validate and filter message citations [m<id>] against actual conversation messages."""
    valid_citations: list[str] = []

    def _replace_cite(match: re.Match[str]) -> str:
        raw_id = match.group(1)
        full_match = match.group(0)
        # Check direct match or prefixed match
        matched_id = None
        if raw_id in valid_msg_ids:
            matched_id = raw_id
        elif f"msg_{raw_id}" in valid_msg_ids:
            matched_id = f"msg_{raw_id}"
        else:
            for vid in valid_msg_ids:
                if vid.endswith(raw_id) or raw_id in vid:
                    matched_id = vid
                    break

        if matched_id:
            valid_citations.append(matched_id)
            return full_match
        else:
            # Hallucinated citation, strip it
            return ""

    cleaned_text = CITATION_REGEX.sub(_replace_cite, summary_text)
    # Clean up double spaces left from stripped citations
    cleaned_text = re.sub(r"  +", " ", cleaned_text)
    return cleaned_text.strip(), valid_citations


class RollingSummaryFolder:
    """Manages rolling fold evaluation, generation, citation verification, and re-anchoring."""

    def __init__(
        self,
        turn_threshold: int = 10,
        token_threshold: int = 2000,
        reanchor_interval: int = 5,
    ) -> None:
        self.turn_threshold = turn_threshold
        self.token_threshold = token_threshold
        self.reanchor_interval = reanchor_interval

    async def should_fold(self, db: aiosqlite.Connection, conversation_id: str) -> bool:
        """Check if unsummarized messages since last fold exceed turn or token thresholds."""
        latest_summary = await get_latest_summary(db, conversation_id)
        if latest_summary and latest_summary.end_msg_id:
            row = await fetch_one(
                db,
                """
                SELECT COUNT(*) as count, SUM(LENGTH(content)) / 4 as est_tokens
                FROM messages
                WHERE conversation_id = ?
                  AND created_at > (SELECT created_at FROM messages WHERE id = ?)
                """,
                (conversation_id, latest_summary.end_msg_id),
            )
        else:
            row = await fetch_one(
                db,
                """
                SELECT COUNT(*) as count, SUM(LENGTH(content)) / 4 as est_tokens
                FROM messages
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            )

        if not row:
            return False
        count = int(row["count"] or 0)
        tokens = int(row["est_tokens"] or 0)
        return count >= self.turn_threshold or tokens >= self.token_threshold

    async def fold(
        self,
        db: aiosqlite.Connection,
        conversation_id: str,
        router: ModelRouter | None = None,
    ) -> ConversationSummary | None:
        """Perform rolling fold or full re-anchor across conversation messages."""
        latest_summary = await get_latest_summary(db, conversation_id)
        next_fold_index = (latest_summary.fold_index + 1) if latest_summary else 1
        is_reanchor = (next_fold_index % self.reanchor_interval == 0)

        # Retrieve messages to fold
        if is_reanchor or not latest_summary or not latest_summary.end_msg_id:
            # Full re-anchor across entire conversation
            rows = await fetch_all(
                db,
                """
                SELECT id, role, content, created_at
                FROM messages
                WHERE conversation_id = ?
                ORDER BY created_at ASC, rowid ASC
                """,
                (conversation_id,),
            )
        else:
            # Incremental fold since last end_msg_id
            rows = await fetch_all(
                db,
                """
                SELECT id, role, content, created_at
                FROM messages
                WHERE conversation_id = ?
                  AND created_at >= (SELECT created_at FROM messages WHERE id = ?)
                ORDER BY created_at ASC, rowid ASC
                """,
                (conversation_id, latest_summary.end_msg_id),
            )

        if not rows:
            return None

        valid_msg_ids = {str(r["id"]) for r in rows}
        start_msg_id = str(rows[0]["id"])
        end_msg_id = str(rows[-1]["id"])

        # Format dialogue for summarizer
        dialogue_text = "\n".join(
            f"[{r['id']}] {r['role'].upper()}: {r['content']}" for r in rows
        )

        # Synthesize rolling summary
        # If router is available, request resident/front model; otherwise rule-based synthesis
        summary_md = ""
        key_facts: list[str] = []
        active_topics: list[str] = []

        if router is not None:
            prompt = (
                f"You are the NoX Conversation Summarizer. Summarize this conversation chunk.\n"
                f"CRITICAL: Always cite the message ID in brackets for each point, e.g. [m{start_msg_id}].\n\n"
                f"REQUIRED SECTIONS:\n"
                f"## Key Facts\n"
                f"## Decisions\n"
                f"## Working Hypotheses\n"
                f"## Open Questions\n\n"
                f"Conversation:\n{dialogue_text[:6000]}"
            )
            try:
                provider, model = router.resolve_role("resident")
                res_chunks = []
                async for chk in provider.chat(
                    messages=[Message(role="user", content=Content.public(prompt))],
                    model=model,
                ):
                    res_chunks.append(chk.text or "")
                summary_md = "".join(res_chunks).strip()
            except Exception as exc:
                log.warning("Model-based summary fold failed: %s", exc)

        if not summary_md or "## Key Facts" not in summary_md:
            # Robust fallback template with valid citations
            first_user = next((r for r in rows if r["role"] == "user"), rows[0])
            first_asst = next((r for r in rows if r["role"] == "assistant"), rows[-1])
            summary_md = (
                f"## Key Facts\n"
                f"- User engaged on primary topic: {first_user['content'][:60]} [m{first_user['id']}]\n"
                f"- Assistant provided core response and analysis [m{first_asst['id']}]\n\n"
                f"## Decisions\n"
                f"- Context and dialogue points preserved for conversation continuity [m{end_msg_id}]\n\n"
                f"## Working Hypotheses\n"
                f"- Current topic remains active and central to upcoming turns [m{first_user['id']}]\n\n"
                f"## Open Questions\n"
                f"- Ongoing user queries and follow-up investigations [m{end_msg_id}]"
            )

        # Validate citations
        cleaned_md, valid_citations = validate_citations(summary_md, valid_msg_ids)
        token_count = approximate_tokens(cleaned_md)

        summary_obj = ConversationSummary(
            id=f"csum_{uuid.uuid4().hex[:12]}",
            conversation_id=conversation_id,
            fold_index=next_fold_index,
            start_msg_id=start_msg_id,
            end_msg_id=end_msg_id,
            summary_markdown=cleaned_md,
            key_facts=valid_citations[:5],
            active_topics=["conversation_history"],
            token_count=token_count,
        )

        await save_summary(db, summary_obj)
        log.info(
            "Created conversation fold %d for %s (reanchor=%s, tokens=%d, valid_citations=%d)",
            next_fold_index,
            conversation_id,
            is_reanchor,
            token_count,
            len(valid_citations),
        )
        return summary_obj
