"""VectorIndex protocol — abstraction over the SQLite blob vector store."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class VectorIndex(Protocol):
    """Minimal protocol every vector backend must satisfy."""

    async def upsert(self, row_id: int, embedding: list[float]) -> None:
        """Insert or replace an embedding."""
        ...

    async def query(
        self,
        embedding: list[float],
        k: int = 10,
    ) -> list[tuple[int, float]]:
        """Return (row_id, cosine_score) pairs, highest score first."""
        ...

    async def delete(self, row_id: int) -> None:
        """Remove an embedding."""
        ...
