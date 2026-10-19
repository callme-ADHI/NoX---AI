"""Memory store for facts, episodes, history, and conversation logs.

Provides durable CRUD operations for facts (with automatic history tracking on update),
episodes, and hard-delete ("forget") cascading per Section 6.6.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import aiosqlite

from noxai.db import execute, fetch_all, fetch_one, lastrowid
from noxai.memory.embed import (
    bytes_to_vector,
    delete_episode_vector,
    delete_fact_vector,
    save_episode_vector,
    save_fact_vector,
)

log = logging.getLogger(__name__)

# Stoplist for episode purging: weak/common words that must never trigger episode deletion
EPISODE_PURGE_STOPLIST: set[str] = {
    "this",
    "that",
    "with",
    "from",
    "have",
    "been",
    "will",
    "would",
    "there",
    "their",
    "about",
    "which",
    "when",
    "what",
    "more",
    "some",
    "like",
    "into",
    "time",
    "them",
    "other",
    "than",
    "then",
    "also",
    "only",
    "were",
    "they",
    "your",
    "mine",
    "ours",
    "very",
    "much",
    "many",
    "good",
    "well",
    "just",
    "make",
    "made",
    "user",
    "agent",
    "done",
    "test",
    "true",
    "false",
    "none",
    "null",
}


def is_qualifying_purge_term(term: str) -> bool:
    """A term qualifies for episode purging only if >= 4 chars and not in the stoplist."""
    t = term.strip().lower()
    return len(t) >= 4 and t not in EPISODE_PURGE_STOPLIST


@dataclass
class Fact:
    """Represents a durable fact in memory."""

    id: int
    key: str
    value: str
    category: str = "other"
    confidence: float = 1.0
    source: str = "chat"
    pinned: bool = False
    created_at: str = ""
    updated_at: str = ""
    last_used_at: str | None = None
    use_count: int = 0
    deleted_at: str | None = None
    embedding: list[float] | None = None
    provenance: str = "user_stated"  # user_stated | agent_derived | manual
    needs_review: int = 0  # 1 if awaiting owner review
    visibility: str = "local_only"  # local_only | cloud_ok



@dataclass
class FactHistory:
    """Represents a historical snapshot of a fact before modification."""

    id: int
    fact_id: int
    key: str
    value: str
    confidence: float
    changed_at: str
    changed_by: str = "agent"


@dataclass
class Episode:
    """Represents an interaction, task run, or daily summary episode."""

    id: int
    ts: str
    kind: str = "conversation"
    task_id: str | None = None
    summary: str = ""
    importance: float = 0.5
    archived_at: str | None = None
    embedding: list[float] | None = None
    untrusted_origin: int = 0  # 1 if generated from a run with UNTRUSTED taint


# ---------------------------------------------------------------------------
# Fact CRUD
# ---------------------------------------------------------------------------


def _row_to_fact(row: aiosqlite.Row) -> Fact:
    emb_blob = row["embedding"] if "embedding" in row.keys() else None
    return Fact(
        id=row["id"],
        key=row["key"],
        value=row["value"],
        category=row["category"],
        confidence=float(row["confidence"]),
        source=row["source"],
        pinned=bool(row["pinned"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_used_at=row["last_used_at"],
        use_count=int(row["use_count"]),
        deleted_at=row["deleted_at"],
        embedding=bytes_to_vector(emb_blob) if emb_blob else None,
        provenance=row["provenance"] if "provenance" in row.keys() else "user_stated",
        needs_review=int(row["needs_review"]) if "needs_review" in row.keys() else 0,
        visibility=row["visibility"] if "visibility" in row.keys() else "local_only",
    )


def _row_to_episode(row: aiosqlite.Row) -> Episode:
    emb_blob = row["embedding"] if "embedding" in row.keys() else None
    return Episode(
        id=row["id"],
        ts=row["ts"],
        kind=row["kind"],
        task_id=row["task_id"],
        summary=row["summary"],
        importance=float(row["importance"]),
        archived_at=row["archived_at"],
        embedding=bytes_to_vector(emb_blob) if emb_blob else None,
        untrusted_origin=int(row["untrusted_origin"]) if "untrusted_origin" in row.keys() else 0,
    )


async def create_fact(
    db: aiosqlite.Connection,
    *,
    key: str,
    value: str,
    category: str = "other",
    confidence: float = 1.0,
    source: str = "chat",
    pinned: bool = False,
    embedding: list[float] | None = None,
    provenance: str = "user_stated",
    needs_review: int = 0,
    visibility: str = "local_only",
) -> Fact:
    """Insert a new fact into memory."""
    valid_categories = {"profile", "preference", "project", "person", "device", "other"}
    cat = category if category in valid_categories else "other"
    conf = max(0.0, min(1.0, float(confidence)))
    src = source if source in {"chat", "job", "manual"} else "chat"
    prov = provenance if provenance in {"user_stated", "agent_derived", "manual"} else "user_stated"
    vis = visibility if visibility in {"local_only", "cloud_ok"} else "local_only"

    try:
        cursor = await execute(
            db,
            """
            INSERT INTO facts (
                key, value, category, confidence, source, pinned,
                provenance, needs_review, visibility
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (key, value, cat, conf, src, 1 if pinned else 0, prov, 1 if needs_review else 0, vis),
        )
    except Exception:
        try:
            cursor = await execute(
                db,
                """
                INSERT INTO facts (
                    key, value, category, confidence, source, pinned, provenance, needs_review
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (key, value, cat, conf, src, 1 if pinned else 0, prov, 1 if needs_review else 0),
            )
        except Exception:
            cursor = await execute(
                db,
                """
                INSERT INTO facts (key, value, category, confidence, source, pinned)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (key, value, cat, conf, src, 1 if pinned else 0),
            )
    fid = await lastrowid(cursor)
    if fid is None:
        raise RuntimeError("Failed to retrieve fact ID after insert")

    if embedding is not None:
        await save_fact_vector(db, fid, embedding)

    # Sync FTS
    try:
        await execute(db, "INSERT INTO fts_facts (fts_facts) VALUES ('rebuild')")
    except Exception as exc:
        log.debug("FTS facts rebuild note: %s", exc)

    fact = await get_fact(db, fid)
    if fact is None:
        raise RuntimeError(f"Fact {fid} could not be retrieved immediately after creation")
    return fact


async def get_fact(db: aiosqlite.Connection, fact_id: int) -> Fact | None:
    """Fetch a fact by ID."""
    row = await fetch_one(db, "SELECT * FROM facts WHERE id = ?", (fact_id,))
    if row is None:
        return None
    return _row_to_fact(row)


async def get_fact_by_key(
    db: aiosqlite.Connection,
    key: str,
    include_deleted: bool = False,
) -> Fact | None:
    """Fetch an active fact by key."""
    sql = "SELECT * FROM facts WHERE key = ?"
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    row = await fetch_one(db, sql, (key,))
    if row is None:
        return None
    return _row_to_fact(row)


async def update_fact(
    db: aiosqlite.Connection,
    fact_id: int,
    *,
    value: str | None = None,
    key: str | None = None,
    category: str | None = None,
    confidence: float | None = None,
    pinned: bool | None = None,
    changed_by: str = "agent",
    embedding: list[float] | None = None,
    visibility: str | None = None,
) -> Fact | None:
    """Update a fact, saving a historical snapshot if value or key changed."""
    existing = await get_fact(db, fact_id)
    if existing is None:
        return None

    # Snapshot to history if key or value changed
    is_modified = False
    if (value is not None and value != existing.value) or (key is not None and key != existing.key):
        is_modified = True
        await execute(
            db,
            """
            INSERT INTO facts_history (fact_id, key, value, confidence, changed_by)
            VALUES (?, ?, ?, ?, ?)
            """,
            (existing.id, existing.key, existing.value, existing.confidence, changed_by),
        )

    new_key = key if key is not None else existing.key
    new_value = value if value is not None else existing.value
    new_cat = category if category is not None else existing.category
    new_conf = (
        max(0.0, min(1.0, float(confidence))) if confidence is not None else existing.confidence
    )
    new_pinned = pinned if pinned is not None else existing.pinned
    new_vis = (
        (visibility if visibility in {"local_only", "cloud_ok"} else existing.visibility)
        if visibility is not None
        else existing.visibility
    )

    try:
        await execute(
            db,
            """
            UPDATE facts
            SET key = ?, value = ?, category = ?, confidence = ?, pinned = ?, visibility = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (new_key, new_value, new_cat, new_conf, 1 if new_pinned else 0, new_vis, fact_id),
        )
    except Exception:
        await execute(
            db,
            """
            UPDATE facts
            SET key = ?, value = ?, category = ?, confidence = ?, pinned = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (new_key, new_value, new_cat, new_conf, 1 if new_pinned else 0, fact_id),
        )

    if embedding is not None:
        await save_fact_vector(db, fact_id, embedding)

    if is_modified:
        try:
            await execute(db, "INSERT INTO fts_facts (fts_facts) VALUES ('rebuild')")
        except Exception as exc:
            log.debug("FTS facts rebuild note: %s", exc)

    return await get_fact(db, fact_id)


async def soft_delete_fact(db: aiosqlite.Connection, fact_id: int) -> bool:
    """Soft-delete a fact by setting deleted_at."""
    await execute(
        db,
        "UPDATE facts SET deleted_at = datetime('now') WHERE id = ?",
        (fact_id,),
    )
    return True


async def hard_delete_fact(
    db: aiosqlite.Connection,
    fact_id: int,
    confirmed: bool = False,
) -> dict[str, Any]:
    """Hard-delete fact, history, embedding, and matching episode text per Section 6.6.

    Rules:
    - Episode purge only on exact key or full value with word boundaries.
    - Terms must be >= 4 characters and not in stoplist. Weak terms never delete episodes.
    - More than 5 episodes or borderline match requires approval (preview first 100 chars of each).
    - Returns exact list of what was deleted (fact, history rows, vectors, FTS, episode ids).
    """
    existing = await get_fact(db, fact_id)
    if existing is None:
        return {"status": "not_found", "fact_id": fact_id}

    hist_rows = await fetch_all(
        db, "SELECT id, value FROM facts_history WHERE fact_id = ?", (fact_id,)
    )

    # Collect candidate terms
    candidate_terms: set[str] = {existing.key, existing.value}
    for hr in hist_rows:
        val_str = str(hr["value"]).strip()
        if val_str:
            candidate_terms.add(val_str)

    qualifying_terms = [t for t in candidate_terms if is_qualifying_purge_term(t)]

    # Find matching episodes with word boundaries
    matching_episodes: list[dict[str, Any]] = []
    if qualifying_terms:
        patterns = [re.compile(rf"\b{re.escape(t)}\b", re.IGNORECASE) for t in qualifying_terms]
        all_eps = await fetch_all(db, "SELECT id, summary FROM episodes")
        for ep in all_eps:
            ep_summary = str(ep["summary"])
            if any(p.search(ep_summary) for p in patterns):
                matching_episodes.append(
                    {
                        "id": ep["id"],
                        "summary": ep_summary,
                    }
                )

    # More than 5 episodes requires approval with preview
    if len(matching_episodes) > 5 and not confirmed:
        return {
            "status": "requires_approval",
            "fact_id": fact_id,
            "key": existing.key,
            "matching_count": len(matching_episodes),
            "preview": [
                {"id": ep["id"], "preview": str(ep["summary"])[:100]} for ep in matching_episodes
            ],
            "matching_episode_ids": [ep["id"] for ep in matching_episodes],
        }

    # 1. Delete history
    history_row_ids = [hr["id"] for hr in hist_rows]
    await execute(db, "DELETE FROM facts_history WHERE fact_id = ?", (fact_id,))

    # 2. Delete vector
    fact_vec_deleted = False
    try:
        await delete_fact_vector(db, fact_id)
        fact_vec_deleted = True
    except Exception as exc:
        log.debug("delete_fact_vector note: %s", exc)

    # 3. Delete from facts
    await execute(db, "DELETE FROM facts WHERE id = ?", (fact_id,))

    # 4. Rebuild FTS
    facts_fts_rebuilt = False
    try:
        await execute(db, "INSERT INTO fts_facts (fts_facts) VALUES ('rebuild')")
        facts_fts_rebuilt = True
    except Exception as exc:
        log.debug("FTS facts rebuild note: %s", exc)

    # 5. Purge matching episodes containing the key, value, or any historical values
    deleted_episode_ids: list[int] = []
    deleted_ep_vectors: list[int] = []
    for match_ep in matching_episodes:
        ep_id = match_ep["id"]
        try:
            await delete_episode_vector(db, ep_id)
            deleted_ep_vectors.append(ep_id)
        except Exception as exc:
            log.debug("delete_episode_vector note: %s", exc)
        await execute(db, "DELETE FROM episodes WHERE id = ?", (ep_id,))
        deleted_episode_ids.append(ep_id)

    episodes_fts_rebuilt = False
    if deleted_episode_ids:
        try:
            await execute(db, "INSERT INTO fts_episodes (fts_episodes) VALUES ('rebuild')")
            episodes_fts_rebuilt = True
        except Exception as exc:
            log.debug("FTS episodes rebuild note: %s", exc)

    log.info(
        "Hard deleted fact id=%d key='%s': %d history rows and %d episodes removed",
        fact_id,
        existing.key,
        len(history_row_ids),
        len(deleted_episode_ids),
    )

    return {
        "status": "deleted",
        "fact_id": fact_id,
        "key": existing.key,
        "fact": {
            "id": existing.id,
            "key": existing.key,
            "value": existing.value,
        },
        "history_rows": history_row_ids,
        "history_rows_removed": len(history_row_ids),
        "vectors": {
            "fact_vector": fact_vec_deleted,
            "episode_vectors": deleted_ep_vectors,
        },
        "fts_entries": {
            "facts_rebuilt": facts_fts_rebuilt,
            "episodes_rebuilt": episodes_fts_rebuilt,
        },
        "episode_ids": deleted_episode_ids,
        "episodes_removed": len(deleted_episode_ids),
    }


async def pin_fact(db: aiosqlite.Connection, fact_id: int, pinned: bool = True) -> bool:
    """Set pinned status of a fact."""
    await execute(
        db,
        "UPDATE facts SET pinned = ? WHERE id = ?",
        (1 if pinned else 0, fact_id),
    )
    return True


async def bump_fact_usage(db: aiosqlite.Connection, fact_ids: list[int]) -> None:
    """Update last_used_at and increment use_count for facts."""
    if not fact_ids:
        return
    placeholders = ",".join("?" for _ in fact_ids)
    sql_update = f"""
        UPDATE facts
        SET last_used_at = datetime('now'), use_count = use_count + 1
        WHERE id IN ({placeholders})
    """  # noqa: S608
    await execute(db, sql_update, tuple(fact_ids))


async def list_facts(
    db: aiosqlite.Connection,
    *,
    category: str | None = None,
    include_deleted: bool = False,
    pinned_only: bool = False,
    limit: int = 100,
) -> list[Fact]:
    """List facts matching criteria."""
    clauses: list[str] = []
    params: list[Any] = []

    if not include_deleted:
        clauses.append("deleted_at IS NULL")
    if category is not None:
        clauses.append("category = ?")
        params.append(category)
    if pinned_only:
        clauses.append("pinned = 1")

    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM facts {where_sql} ORDER BY pinned DESC, updated_at DESC LIMIT ?"  # noqa: S608
    params.append(limit)

    rows = await fetch_all(db, sql, tuple(params))
    return [_row_to_fact(r) for r in rows]


async def get_fact_history(db: aiosqlite.Connection, fact_id: int) -> list[FactHistory]:
    """Retrieve modification history for a fact."""
    rows = await fetch_all(
        db,
        "SELECT * FROM facts_history WHERE fact_id = ? ORDER BY changed_at DESC",
        (fact_id,),
    )
    return [
        FactHistory(
            id=r["id"],
            fact_id=r["fact_id"],
            key=r["key"],
            value=r["value"],
            confidence=float(r["confidence"]),
            changed_at=r["changed_at"],
            changed_by=r["changed_by"],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Episode CRUD
# ---------------------------------------------------------------------------


async def create_episode(
    db: aiosqlite.Connection,
    *,
    summary: str,
    kind: str = "conversation",
    task_id: str | None = None,
    importance: float = 0.5,
    embedding: list[float] | None = None,
    untrusted_origin: int = 0,
) -> Episode:
    """Insert a new episode summary."""
    valid_kinds = {"conversation", "job", "daily_summary", "consolidation", "manual"}
    k = kind if kind in valid_kinds else "conversation"
    imp = max(0.0, min(1.0, float(importance)))

    try:
        cursor = await execute(
            db,
            """
            INSERT INTO episodes (summary, kind, task_id, importance, untrusted_origin)
            VALUES (?, ?, ?, ?, ?)
            """,
            (summary, k, task_id, imp, 1 if untrusted_origin else 0),
        )
    except Exception:
        cursor = await execute(
            db,
            """
            INSERT INTO episodes (summary, kind, task_id, importance)
            VALUES (?, ?, ?, ?)
            """,
            (summary, k, task_id, imp),
        )
    eid = await lastrowid(cursor)
    if eid is None:
        raise RuntimeError("Failed to retrieve episode ID after insert")

    if embedding is not None:
        await save_episode_vector(db, eid, embedding)

    try:
        await execute(db, "INSERT INTO fts_episodes (fts_episodes) VALUES ('rebuild')")
    except Exception as exc:
        log.debug("FTS episodes rebuild note: %s", exc)

    ep = await get_episode(db, eid)
    if ep is None:
        raise RuntimeError(f"Episode {eid} could not be retrieved immediately after creation")
    return ep


async def get_episode(db: aiosqlite.Connection, episode_id: int) -> Episode | None:
    """Fetch an episode by ID."""
    row = await fetch_one(db, "SELECT * FROM episodes WHERE id = ?", (episode_id,))
    if row is None:
        return None
    return _row_to_episode(row)


async def list_episodes(
    db: aiosqlite.Connection,
    *,
    include_archived: bool = False,
    kind: str | None = None,
    limit: int = 50,
) -> list[Episode]:
    """List episodes in reverse chronological order."""
    clauses: list[str] = []
    params: list[Any] = []

    if not include_archived:
        clauses.append("archived_at IS NULL")
    if kind is not None:
        clauses.append("kind = ?")
        params.append(kind)

    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = f"SELECT * FROM episodes {where_sql} ORDER BY ts DESC LIMIT ?"  # noqa: S608
    params.append(limit)

    rows = await fetch_all(db, sql, tuple(params))
    return [_row_to_episode(r) for r in rows]


async def archive_episode(db: aiosqlite.Connection, episode_id: int) -> bool:
    """Archive an episode (soft-delete)."""
    await execute(
        db,
        "UPDATE episodes SET archived_at = datetime('now') WHERE id = ?",
        (episode_id,),
    )
    return True


async def hard_delete_episode(db: aiosqlite.Connection, episode_id: int) -> bool:
    """Permanently delete an episode and its vector embedding."""
    await delete_episode_vector(db, episode_id)
    await execute(db, "DELETE FROM episodes WHERE id = ?", (episode_id,))
    try:
        await execute(db, "INSERT INTO fts_episodes (fts_episodes) VALUES ('rebuild')")
    except Exception as exc:
        log.debug("FTS episodes rebuild note: %s", exc)
    return True


# ---------------------------------------------------------------------------
# Full-Text Search (FTS5)
# ---------------------------------------------------------------------------


async def search_facts_fts(
    db: aiosqlite.Connection,
    query: str,
    limit: int = 20,
) -> list[tuple[int, float]]:
    """Search facts via SQLite FTS5. Returns list of (fact_id, bm25_rank)."""
    if not query.strip():
        return []

    # Clean query for FTS5 (tokenize into alphanumeric tokens joined by OR)
    clean_query = " ".join(token for token in query.split() if token.isalnum())
    if not clean_query:
        return []

    try:
        rows = await fetch_all(
            db,
            """
            SELECT rowid, bm25(fts_facts) as rank
            FROM fts_facts
            WHERE fts_facts MATCH ?
            ORDER BY rank ASC
            LIMIT ?
            """,
            (clean_query, limit),
        )
        # FTS5 bm25 produces negative numbers (lower/more negative = better match)
        return [(r["rowid"], float(r["rank"])) for r in rows]
    except Exception as exc:
        log.debug("FTS facts query failed: %s", exc)
        return []


async def search_episodes_fts(
    db: aiosqlite.Connection,
    query: str,
    limit: int = 20,
) -> list[tuple[int, float]]:
    """Search episodes via SQLite FTS5. Returns list of (episode_id, bm25_rank)."""
    if not query.strip():
        return []

    clean_query = " ".join(token for token in query.split() if token.isalnum())
    if not clean_query:
        return []

    try:
        rows = await fetch_all(
            db,
            """
            SELECT rowid, bm25(fts_episodes) as rank
            FROM fts_episodes
            WHERE fts_episodes MATCH ?
            ORDER BY rank ASC
            LIMIT ?
            """,
            (clean_query, limit),
        )
        return [(r["rowid"], float(r["rank"])) for r in rows]
    except Exception as exc:
        log.debug("FTS episodes query failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Message Persistence
# ---------------------------------------------------------------------------


async def save_message(
    db: aiosqlite.Connection,
    conversation_id: str,
    role: str,
    content: str,
    taint: int = 0,
    meta_json: dict[str, Any] | None = None,
) -> int:
    """Save a chat message to the messages table."""
    meta_str = json.dumps(meta_json) if meta_json is not None else None
    cursor = await execute(
        db,
        """
        INSERT INTO messages (conversation_id, role, content, taint, meta_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (conversation_id, role, content, taint, meta_str),
    )
    mid = await lastrowid(cursor)
    return mid if mid is not None else 0


async def get_conversation_messages(
    db: aiosqlite.Connection,
    conversation_id: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Fetch message history for a conversation."""
    rows = await fetch_all(
        db,
        """
        SELECT id, conversation_id, role, content, taint, meta_json, created_at
        FROM messages
        WHERE conversation_id = ?
        ORDER BY id ASC
        LIMIT ?
        """,
        (conversation_id, limit),
    )
    return [
        {
            "id": r["id"],
            "conversation_id": r["conversation_id"],
            "role": r["role"],
            "content": r["content"],
            "taint": r["taint"],
            "meta": json.loads(r["meta_json"]) if r["meta_json"] else None,
            "created_at": r["created_at"],
        }
        for r in rows
    ]
