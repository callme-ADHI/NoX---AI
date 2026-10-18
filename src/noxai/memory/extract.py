"""Background memory extraction and deduplication for NoX per Section 6.6.

Executes after completed runs using the local model ('brain') only.
Features:
- Extracts structured facts and episode summary via prompts/extractor.md
- Secret filtering: rejects passwords, tokens, API keys, private keys, cards, SSNs
- Deduplication: checks key and vector similarity (> 0.9) to update or insert
- Snapshots previous values to facts_history when updating
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import aiosqlite

from noxai.agent.json_repair import repair_json
from noxai.db import get_db
from noxai.memory.embed import embed_text, search_fact_vectors
from noxai.memory.store import (
    Episode,
    Fact,
    bump_fact_usage,
    create_episode,
    create_fact,
    get_fact,
    get_fact_by_key,
    update_fact,
)
from noxai.models.base import Message

if TYPE_CHECKING:
    from noxai.models.router import ModelRouter

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Secret Filter Patterns
# ---------------------------------------------------------------------------

SECRET_PATTERNS: list[re.Pattern[str]] = [
    # NVIDIA API key
    re.compile(r"nvapi-[A-Za-z0-9_-]{20,}"),
    # OpenAI / generic API key
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    # GitHub personal access token
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    # Generic bearer authorization header
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/-]{16,}"),
    # Passwords or secrets in key-value formats
    re.compile(r"(?i)\b(?:password|passwd|pwd|secret_key)\s*[:=]\s*\S+"),
    # Private keys (PEM / OpenSSH)
    re.compile(r"-----BEGIN (?:[A-Z0-9_-]+ )?PRIVATE KEY-----"),
    # Credit card numbers (Luhn candidate regex)
    re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b"),
    # US Social Security Number / generic govt ID candidate
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
]


def contains_secret(text: str) -> bool:
    """Check if text contains credentials, tokens, passwords, or private keys."""
    if not text:
        return False
    for pat in SECRET_PATTERNS:
        if pat.search(text):
            return True
    return False


@dataclass
class ExtractionResult:
    """Outcome of background memory extraction."""

    facts_created: list[Fact] = field(default_factory=list)
    facts_updated: list[Fact] = field(default_factory=list)
    episode: Episode | None = None
    dropped_secrets_count: int = 0


def _load_extractor_prompt() -> str:
    prompt_path = Path(__file__).parent.parent / "prompts" / "extractor.md"
    try:
        return prompt_path.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are NoX's Memory Extractor. Extract durable facts and an episode summary. "
            "Output JSON with 'facts' list and 'episode_summary', 'importance'."
        )


async def extract_and_store_turn(
    *,
    user_text: str,
    assistant_text: str,
    router: ModelRouter,
    db: aiosqlite.Connection | None = None,
    task_id: str | None = None,
) -> ExtractionResult:
    """Execute background extraction on a completed conversation turn."""
    if db is None:
        async with get_db() as conn:
            return await extract_and_store_turn(
                user_text=user_text,
                assistant_text=assistant_text,
                router=router,
                db=conn,
                task_id=task_id,
            )

    result = ExtractionResult()

    # Skip extraction on trivial inputs
    if len(user_text.strip()) < 3:
        return result

    extractor_system = _load_extractor_prompt()
    user_prompt = (
        f"Analyze this completed interaction turn:\n\nOwner: {user_text}\n\nNoX: {assistant_text}\n"
    )

    messages = [
        Message(role="system", content=extractor_system),
        Message(role="user", content=user_prompt),
    ]

    # Always extract using local brain role
    raw_response = ""
    try:
        async for chunk in router.chat(role="brain", messages=messages, stream=False):
            if chunk.text:
                raw_response += chunk.text
    except Exception as exc:
        log.warning("Memory extraction LLM call failed: %s", exc)
        return result

    # Parse JSON
    parsed: dict[str, Any] | None = None
    cleaned = raw_response.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()

    try:
        parsed = json.loads(cleaned)
    except Exception:
        # Heuristic repair pass
        try:
            parsed = repair_json(cleaned)
        except Exception:
            log.warning("Failed to parse extractor JSON: %s", raw_response[:200])
            return result

    if not isinstance(parsed, dict):
        return result

    # 1. Process candidate facts
    candidate_facts = parsed.get("facts", [])
    if isinstance(candidate_facts, list):
        for item in candidate_facts:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key", "")).strip().lower()
            val = str(item.get("value", "")).strip()
            cat = str(item.get("category", "other")).strip().lower()
            conf = float(item.get("confidence", 1.0))

            if not key or not val:
                continue

            # Secret filter check
            if contains_secret(key) or contains_secret(val):
                log.warning("Secret detected in candidate fact '%s'; dropping.", key)
                result.dropped_secrets_count += 1
                continue

            # Generate embedding for semantic deduplication
            fact_str = f"{key}: {val}"
            fact_emb = await embed_text(fact_str, router)

            # Check for existing fact by exact key first
            existing = await get_fact_by_key(db, key)

            # If not by key, check embedding similarity (> 0.9)
            if existing is None and fact_emb:
                similar = await search_fact_vectors(db, fact_emb, top_k=1)
                if similar and similar[0][1] >= 0.90:
                    cand_fact = await get_fact(db, similar[0][0])
                    if cand_fact is not None and cand_fact.deleted_at is None:
                        existing = cand_fact

            if existing is not None:
                # Dedupe: if value unchanged, bump usage
                if existing.value == val:
                    await bump_fact_usage(db, [existing.id])
                else:
                    # Update fact: write history snapshot and update
                    updated = await update_fact(
                        db,
                        existing.id,
                        value=val,
                        confidence=conf,
                        category=cat,
                        embedding=fact_emb,
                        changed_by="agent",
                    )
                    if updated:
                        result.facts_updated.append(updated)
            else:
                # Insert novel fact
                created = await create_fact(
                    db,
                    key=key,
                    value=val,
                    category=cat,
                    confidence=conf,
                    source="chat",
                    embedding=fact_emb,
                )
                result.facts_created.append(created)

    # 2. Process episode summary
    ep_summary = str(parsed.get("episode_summary", "")).strip()
    importance = float(parsed.get("importance", 0.5))

    if ep_summary:
        if contains_secret(ep_summary):
            log.warning("Secret detected in episode summary; dropping episode.")
            result.dropped_secrets_count += 1
        else:
            ep_emb = await embed_text(ep_summary, router)
            ep = await create_episode(
                db,
                summary=ep_summary,
                kind="conversation",
                task_id=task_id,
                importance=importance,
                embedding=ep_emb,
            )
            result.episode = ep

    return result
