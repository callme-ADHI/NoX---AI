"""Vector embedding storage, serialization, and similarity search for NoX memory.

Supports sqlite-vec if loaded, with pure NumPy float32 LE BLOB fallback.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import aiosqlite
import numpy as np

from noxai.db import VECTOR_BACKEND, execute, fetch_all

if TYPE_CHECKING:
    from noxai.models.router import ModelRouter

log = logging.getLogger(__name__)


def vector_to_bytes(vec: list[float] | np.ndarray) -> bytes:
    """Convert a float vector to little-endian float32 bytes for SQLite storage."""
    arr = np.asarray(vec, dtype=np.float32)
    return arr.tobytes()


def bytes_to_vector(blob: bytes) -> list[float]:
    """Convert float32 bytes back into a Python list of floats."""
    if not blob:
        return []
    return np.frombuffer(blob, dtype=np.float32).tolist()


def cosine_similarity(
    v1: list[float] | np.ndarray,
    v2: list[float] | np.ndarray,
) -> float:
    """Compute cosine similarity between two vectors in [-1.0, 1.0]."""
    a = np.asarray(v1, dtype=np.float32)
    b = np.asarray(v2, dtype=np.float32)
    if a.shape != b.shape:
        return 0.0
    norm_a = float(np.linalg.norm(a))
    norm_b = float(np.linalg.norm(b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    val = float(np.dot(a, b) / (norm_a * norm_b))
    # Clamp to valid [-1.0, 1.0] range to counter floating precision artifacts
    return max(-1.0, min(1.0, val))


async def embed_texts(
    texts: list[str],
    router: ModelRouter,
) -> list[list[float]]:
    """Generate vector embeddings for a list of strings using the embed role."""
    if not texts:
        return []
    try:
        return await router.embed(texts=texts)
    except Exception as exc:
        log.warning("Embedding generation failed: %s", exc)
        return [[0.0] * 8 for _ in texts]


async def embed_text(
    text: str,
    router: ModelRouter,
) -> list[float]:
    """Generate vector embedding for a single text."""
    results = await embed_texts([text], router)
    return results[0] if results else [0.0] * 8


async def save_fact_vector(
    db: aiosqlite.Connection,
    fact_id: int,
    embedding: list[float] | bytes,
) -> None:
    """Save or update vector embedding for a fact in vec_facts and facts."""
    raw_bytes = vector_to_bytes(embedding) if isinstance(embedding, list) else embedding
    await execute(
        db,
        "INSERT INTO vec_facts (fact_id, embedding) VALUES (?, ?) "
        "ON CONFLICT(fact_id) DO UPDATE SET embedding = excluded.embedding",
        (fact_id, raw_bytes),
    )
    await execute(
        db,
        "UPDATE facts SET embedding = ? WHERE id = ?",
        (raw_bytes, fact_id),
    )


async def save_episode_vector(
    db: aiosqlite.Connection,
    episode_id: int,
    embedding: list[float] | bytes,
) -> None:
    """Save or update vector embedding for an episode in vec_episodes and episodes."""
    raw_bytes = vector_to_bytes(embedding) if isinstance(embedding, list) else embedding
    await execute(
        db,
        "INSERT INTO vec_episodes (episode_id, embedding) VALUES (?, ?) "
        "ON CONFLICT(episode_id) DO UPDATE SET embedding = excluded.embedding",
        (episode_id, raw_bytes),
    )
    await execute(
        db,
        "UPDATE episodes SET embedding = ? WHERE id = ?",
        (raw_bytes, episode_id),
    )


async def delete_fact_vector(db: aiosqlite.Connection, fact_id: int) -> None:
    """Delete vector for a fact."""
    await execute(db, "DELETE FROM vec_facts WHERE fact_id = ?", (fact_id,))


async def delete_episode_vector(db: aiosqlite.Connection, episode_id: int) -> None:
    """Delete vector for an episode."""
    await execute(db, "DELETE FROM vec_episodes WHERE episode_id = ?", (episode_id,))


def _force_numpy() -> bool:
    import os

    return os.getenv("NOX_FORCE_NUMPY_VECTORS", "0") == "1"


async def search_fact_vectors(
    db: aiosqlite.Connection,
    query_vec: list[float],
    top_k: int = 20,
) -> list[tuple[int, float]]:
    """Search vec_facts for most similar facts using cosine similarity."""
    if not query_vec:
        return []

    # If sqlite-vec is active and not forced to numpy, try native query
    if VECTOR_BACKEND == "vec" and not _force_numpy():
        try:
            q_blob = vector_to_bytes(query_vec)
            rows = await fetch_all(
                db,
                "SELECT fact_id, vec_distance_cosine(embedding, ?) as distance "
                "FROM vec_facts ORDER BY distance ASC LIMIT ?",
                (q_blob, top_k),
            )
            # Distance in cosine is (1 - cosine_similarity)
            return [(r["fact_id"], max(0.0, 1.0 - float(r["distance"]))) for r in rows]
        except Exception as exc:
            log.warning("sqlite-vec search failed, falling back to NumPy: %s", exc)

    # NumPy brute force fallback
    rows = await fetch_all(db, "SELECT fact_id, embedding FROM vec_facts")
    if not rows:
        return []

    q_arr = np.asarray(query_vec, dtype=np.float32)
    scores: list[tuple[int, float]] = []

    for row in rows:
        blob: bytes | None = row["embedding"]
        if not blob:
            continue
        try:
            vec = np.frombuffer(blob, dtype=np.float32)
            sim = cosine_similarity(q_arr, vec)
            scores.append((row["fact_id"], sim))
        except Exception as exc:
            log.debug("Skipping malformed fact embedding: %s", exc)
            continue

    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:top_k]


async def search_episode_vectors(
    db: aiosqlite.Connection,
    query_vec: list[float],
    top_k: int = 20,
) -> list[tuple[int, float]]:
    """Search vec_episodes for most similar episodes using cosine similarity."""
    if not query_vec:
        return []

    if VECTOR_BACKEND == "vec" and not _force_numpy():
        try:
            q_blob = vector_to_bytes(query_vec)
            rows = await fetch_all(
                db,
                "SELECT episode_id, vec_distance_cosine(embedding, ?) as distance "
                "FROM vec_episodes ORDER BY distance ASC LIMIT ?",
                (q_blob, top_k),
            )
            return [(r["episode_id"], max(0.0, 1.0 - float(r["distance"]))) for r in rows]
        except Exception as exc:
            log.warning("sqlite-vec search failed, falling back to NumPy: %s", exc)

    rows = await fetch_all(db, "SELECT episode_id, embedding FROM vec_episodes")
    if not rows:
        return []

    q_arr = np.asarray(query_vec, dtype=np.float32)
    scores: list[tuple[int, float]] = []

    for row in rows:
        blob: bytes | None = row["embedding"]
        if not blob:
            continue
        try:
            vec = np.frombuffer(blob, dtype=np.float32)
            sim = cosine_similarity(q_arr, vec)
            scores.append((row["episode_id"], sim))
        except Exception as exc:
            log.debug("Skipping malformed episode embedding: %s", exc)
            continue

    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:top_k]
