"""Hybrid recall ranking for NoX memory per Section 6.6.

Formula:
score = 0.55*cosine + 0.20*keyword_bm25_norm + 0.15*recency_decay + 0.10*importance_or_use

Features:
- Recency decay half-life: 30 days (none for pinned facts).
- Pinned facts rank first.
- Strict token budget constraint (default 800 tokens).
- Automatically updates last_used_at and use_count for injected items.
"""

from __future__ import annotations

import datetime
import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import aiosqlite

from noxai.config import MemoryConfig, Settings
from noxai.memory.embed import embed_text, search_episode_vectors, search_fact_vectors
from noxai.memory.store import (
    Episode,
    Fact,
    bump_fact_usage,
    get_episode,
    get_fact,
    list_facts,
    search_episodes_fts,
    search_facts_fts,
)

if TYPE_CHECKING:
    from noxai.models.router import ModelRouter

log = logging.getLogger(__name__)

CHARS_PER_TOKEN = 2.8


@dataclass
class ScoredMemory:
    """A scored memory candidate."""

    item_type: str  # "fact" or "episode"
    item: Fact | Episode
    score: float
    cosine: float
    bm25_norm: float
    recency: float
    importance_or_use: float
    is_pinned: bool = False


@dataclass
class RecallResult:
    """Result of hybrid memory recall."""

    facts: list[Fact]
    episodes: list[Episode]
    formatted_block: str
    tokens_used: int


def _parse_iso_datetime(dt_str: str | None) -> datetime.datetime:
    if not dt_str:
        return datetime.datetime.now(datetime.UTC)
    try:
        # SQLite datetime('now') produces 'YYYY-MM-DD HH:MM:SS'
        dt = datetime.datetime.fromisoformat(dt_str.replace(" ", "T"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.UTC)
        return dt
    except Exception:
        return datetime.datetime.now(datetime.UTC)


def compute_recency_decay(
    dt_str: str | None,
    half_life_days: float = 30.0,
    is_pinned: bool = False,
) -> float:
    """Calculate exponential recency decay with 30-day half-life. Pinned items have no decay."""
    if is_pinned:
        return 1.0
    dt = _parse_iso_datetime(dt_str)
    now = datetime.datetime.now(datetime.UTC)
    delta_seconds = max(0.0, (now - dt).total_seconds())
    delta_days = delta_seconds / 86400.0
    # 2 ** (-delta_days / 30.0)
    return float(math.pow(2.0, -delta_days / half_life_days))


def calculate_hybrid_score(
    cosine: float,
    bm25_norm: float,
    recency: float,
    importance_or_use: float,
) -> float:
    """Compute score = 0.55*cosine + 0.20*bm25_norm + 0.15*recency + 0.10*importance_or_use."""
    c = max(0.0, min(1.0, cosine))
    b = max(0.0, min(1.0, bm25_norm))
    r = max(0.0, min(1.0, recency))
    u = max(0.0, min(1.0, importance_or_use))
    return 0.55 * c + 0.20 * b + 0.15 * r + 0.10 * u


async def recall_memories(
    query: str,
    *,
    db: aiosqlite.Connection,
    router: ModelRouter,
    config: MemoryConfig | Settings | None = None,
    top_k: int | None = None,
    token_budget: int | None = None,
) -> RecallResult:
    """Retrieve the most relevant facts and episodes for a user prompt."""
    if config is not None and isinstance(config, Settings):
        cfg = config.memory
    elif config is not None and isinstance(config, MemoryConfig):
        cfg = config
    else:
        cfg = MemoryConfig()

    actual_top_k = top_k if top_k is not None else cfg.recall_top_k
    actual_budget = token_budget if token_budget is not None else cfg.recall_token_budget
    max_chars = int(actual_budget * CHARS_PER_TOKEN)

    # 1. Embed query
    query_vec: list[float] = []
    if query.strip():
        query_vec = await embed_text(query, router)

    # 2. Retrieve vector similarity scores
    fact_vec_scores = (
        dict(await search_fact_vectors(db, query_vec, top_k=actual_top_k * 4)) if query_vec else {}
    )
    episode_vec_scores = (
        dict(await search_episode_vectors(db, query_vec, top_k=actual_top_k * 4))
        if query_vec
        else {}
    )

    # 3. Retrieve FTS keyword BM25 ranks
    fact_fts_raw = dict(await search_facts_fts(db, query, limit=actual_top_k * 4))
    episode_fts_raw = dict(await search_episodes_fts(db, query, limit=actual_top_k * 4))

    # Normalize BM25: SQLite FTS5 bm25 returns negative values (lower = stronger match)
    fact_bm25_norm: dict[int, float] = {}
    if fact_fts_raw:
        min_rank = min(fact_fts_raw.values())
        for fid, rank in fact_fts_raw.items():
            fact_bm25_norm[fid] = abs(rank) / abs(min_rank) if min_rank < 0 else 1.0

    episode_bm25_norm: dict[int, float] = {}
    if episode_fts_raw:
        min_rank = min(episode_fts_raw.values())
        for eid, rank in episode_fts_raw.items():
            episode_bm25_norm[eid] = abs(rank) / abs(min_rank) if min_rank < 0 else 1.0

    # 4. Score Candidate Facts
    # Include all pinned facts, plus candidates from vector & FTS
    candidate_fact_ids = set(fact_vec_scores.keys()) | set(fact_bm25_norm.keys())
    pinned_facts = await list_facts(db, pinned_only=True, limit=50)
    for pf in pinned_facts:
        candidate_fact_ids.add(pf.id)

    candidates: list[ScoredMemory] = []

    for fid in candidate_fact_ids:
        fact = await get_fact(db, fid)
        if fact is None or fact.deleted_at is not None or getattr(fact, "needs_review", 0) == 1:
            continue

        cos = fact_vec_scores.get(fid, 0.0)
        bm25 = fact_bm25_norm.get(fid, 0.0)
        rec = compute_recency_decay(
            fact.updated_at or fact.created_at,
            half_life_days=30.0,
            is_pinned=fact.pinned,
        )
        use_factor = 0.5 * fact.confidence + 0.5 * min(1.0, fact.use_count / 10.0)
        score = calculate_hybrid_score(cos, bm25, rec, use_factor)

        candidates.append(
            ScoredMemory(
                item_type="fact",
                item=fact,
                score=score,
                cosine=cos,
                bm25_norm=bm25,
                recency=rec,
                importance_or_use=use_factor,
                is_pinned=fact.pinned,
            )
        )

    # 5. Score Candidate Episodes
    candidate_ep_ids = set(episode_vec_scores.keys()) | set(episode_bm25_norm.keys())
    for eid in candidate_ep_ids:
        ep = await get_episode(db, eid)
        if ep is None or ep.archived_at is not None:
            continue

        cos = episode_vec_scores.get(eid, 0.0)
        bm25 = episode_bm25_norm.get(eid, 0.0)
        rec = compute_recency_decay(ep.ts, half_life_days=30.0, is_pinned=False)
        imp = ep.importance
        score = calculate_hybrid_score(cos, bm25, rec, imp)

        candidates.append(
            ScoredMemory(
                item_type="episode",
                item=ep,
                score=score,
                cosine=cos,
                bm25_norm=bm25,
                recency=rec,
                importance_or_use=imp,
                is_pinned=False,
            )
        )

    # 6. Rank: Pinned facts rank first, then descending by score
    # We sort by (is_pinned DESC, score DESC)
    candidates.sort(key=lambda m: (1 if m.is_pinned else 0, m.score), reverse=True)

    # 7. Select top_k fitting within token budget
    selected_facts: list[Fact] = []
    selected_episodes: list[Episode] = []
    current_chars = 0

    for cand in candidates[: actual_top_k * 2]:
        if len(selected_facts) + len(selected_episodes) >= actual_top_k:
            break

        if cand.item_type == "fact":
            f = cand.item
            assert isinstance(f, Fact)
            snippet = f"- [{f.category}] {f.key}: {f.value}\n"
            if current_chars + len(snippet) > max_chars and (selected_facts or selected_episodes):
                continue
            selected_facts.append(f)
            current_chars += len(snippet)
        else:
            e = cand.item
            assert isinstance(e, Episode)
            snippet = f"- ({e.ts[:10]}): {e.summary}\n"
            if current_chars + len(snippet) > max_chars and (selected_facts or selected_episodes):
                continue
            selected_episodes.append(e)
            current_chars += len(snippet)

    # 8. Update usage for injected items
    if selected_facts:
        await bump_fact_usage(db, [f.id for f in selected_facts])

    # 9. Format markdown block
    formatted_block = format_memory_block(selected_facts, selected_episodes)
    tokens_used = max(1, int(len(formatted_block) / CHARS_PER_TOKEN))

    return RecallResult(
        facts=selected_facts,
        episodes=selected_episodes,
        formatted_block=formatted_block,
        tokens_used=tokens_used,
    )


def format_memory_block(facts: list[Fact], episodes: list[Episode]) -> str:
    """Format facts and episodes into the Relevant memory markdown block."""
    if not facts and not episodes:
        return ""

    lines: list[str] = ["### Relevant memory (local only)"]

    pinned = [f for f in facts if f.pinned]
    unpinned = [f for f in facts if not f.pinned]

    if pinned:
        lines.append("#### Pinned facts")
        for f in pinned:
            lines.append(f"- [{f.category}] {f.key}: {f.value}")

    if unpinned:
        lines.append("#### Facts")
        for f in unpinned:
            lines.append(f"- [{f.category}] {f.key}: {f.value}")

    if episodes:
        lines.append("#### Recent episodes")
        for e in episodes:
            date_prefix = e.ts[:10] if len(e.ts) >= 10 else e.ts
            if getattr(e, "untrusted_origin", 0) == 1:
                lines.append(
                    f"- ({date_prefix}) <untrusted_content>{e.summary}</untrusted_content>"
                )
            else:
                lines.append(f"- ({date_prefix}) {e.summary}")

    return "\n".join(lines)
