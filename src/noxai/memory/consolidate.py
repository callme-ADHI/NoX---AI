"""Nightly memory consolidation routine per Section 6.6.

Consolidation:
- Summarizes the day's episodes into one daily-summary episode
- Merges duplicate or near-duplicate facts
- Archives (soft-deletes) unused, low-importance episodes older than episode_archive_days
- Writes a consolidation report to the audit log
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import aiosqlite

from noxai.config import MemoryConfig, Settings
from noxai.db import execute, fetch_all
from noxai.memory.embed import cosine_similarity, embed_text
from noxai.memory.store import (
    create_episode,
    list_facts,
    soft_delete_fact,
)
from noxai.models.base import Message

if TYPE_CHECKING:
    from noxai.models.router import ModelRouter

log = logging.getLogger(__name__)


@dataclass
class ConsolidationReport:
    """Summary of operations performed during consolidation."""

    episodes_summarized: int = 0
    daily_summary_id: int | None = None
    facts_merged: int = 0
    episodes_archived: int = 0


async def run_memory_consolidation(
    db: aiosqlite.Connection,
    router: ModelRouter,
    config: MemoryConfig | Settings | None = None,
) -> ConsolidationReport:
    """Execute memory consolidation."""
    if config is not None and isinstance(config, Settings):
        cfg = config.memory
    elif config is not None and isinstance(config, MemoryConfig):
        cfg = config
    else:
        cfg = MemoryConfig()

    report = ConsolidationReport()

    # 1. Summarize the day's episodes
    recent_episodes = await fetch_all(
        db,
        """
        SELECT id, summary, kind, ts
        FROM episodes
        WHERE ts >= datetime('now', '-24 hours')
          AND kind IN ('conversation', 'job')
          AND archived_at IS NULL
        ORDER BY ts ASC
        """,
    )

    if len(recent_episodes) >= 2:
        episode_texts = [f"- {r['ts'][:16]}: {r['summary']}" for r in recent_episodes]
        prompt = (
            "Summarize these recent activity episodes from the past 24 hours into one concise, "
            "coherent daily activity summary (2-4 sentences):\n\n" + "\n".join(episode_texts)
        )
        messages = [
            Message(
                role="system",
                content="You are NoX's consolidation agent. Provide a concise daily summary.",
            ),
            Message(role="user", content=prompt),
        ]

        summary_text = ""
        try:
            async for chunk in router.chat(role="brain", messages=messages, stream=False):
                if chunk.text:
                    summary_text += chunk.text
        except Exception as exc:
            log.warning("Consolidation LLM summary failed: %s", exc)

        summary_text = summary_text.strip()
        if summary_text:
            emb = await embed_text(summary_text, router)
            ep = await create_episode(
                db,
                summary=summary_text,
                kind="daily_summary",
                importance=0.8,
                embedding=emb,
            )
            report.daily_summary_id = ep.id
            report.episodes_summarized = len(recent_episodes)

    # 2. Merge duplicate or near-duplicate facts
    active_facts = await list_facts(db, include_deleted=False, limit=500)
    merged_ids: set[int] = set()

    for i in range(len(active_facts)):
        f1 = active_facts[i]
        if f1.id in merged_ids:
            continue

        for j in range(i + 1, len(active_facts)):
            f2 = active_facts[j]
            if f2.id in merged_ids:
                continue

            # Compare either matching keys or high semantic similarity with same category
            is_dup = False
            if f1.key == f2.key:
                is_dup = True
            elif f1.category == f2.category and f1.embedding and f2.embedding:
                sim = cosine_similarity(f1.embedding, f2.embedding)
                if sim >= 0.92:
                    is_dup = True

            if is_dup:
                # Keep the fact with higher confidence or newer update
                if f1.confidence >= f2.confidence:
                    victim = f2
                else:
                    victim = f1
                    f1 = f2  # survivor becomes f1 for subsequent comparisons

                await soft_delete_fact(db, victim.id)
                merged_ids.add(victim.id)
                report.facts_merged += 1

    # 3. Archive unused, low-importance episodes older than archive days
    archive_delta = f"-{cfg.episode_archive_days} days"
    archived_cursor = await execute(
        db,
        """
        UPDATE episodes
        SET archived_at = datetime('now')
        WHERE archived_at IS NULL
          AND importance < 0.6
          AND ts < datetime('now', ?)
        """,
        (archive_delta,),
    )
    report.episodes_archived = archived_cursor.rowcount if archived_cursor.rowcount > 0 else 0

    # 4. Write audit log report
    audit_payload = {
        "episodes_summarized": report.episodes_summarized,
        "daily_summary_id": report.daily_summary_id,
        "facts_merged": report.facts_merged,
        "episodes_archived": report.episodes_archived,
    }
    await execute(
        db,
        """
        INSERT INTO audit_log
        (tool, args_json, tier, decision, approver, result_summary, ok)
        VALUES ('consolidation', ?, 'read', 'auto', 'system', 'Memory consolidation report', 1)
        """,
        (json.dumps(audit_payload),),
    )

    log.info(
        "Memory consolidation completed: %d episodes summarized, %d facts merged, "
        "%d episodes archived.",
        report.episodes_summarized,
        report.facts_merged,
        report.episodes_archived,
    )

    return report
