"""Tests for NVIDIA provider: rate limiting, token bucket, and key gating."""

from __future__ import annotations

import time

import pytest

from noxai.models.base import Message, ProviderUnavailable
from noxai.models.nvidia import NvidiaProvider, TokenBucket


class TestTokenBucket:
    async def test_immediate_acquire_when_tokens_available(self) -> None:
        """Acquiring when capacity is available proceeds without delay."""
        bucket = TokenBucket(requests_per_minute=60)
        start = time.monotonic()
        await bucket.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1
        stats = bucket.get_stats()
        assert stats["total_requests"] == 1
        assert stats["tokens_available"] <= 60.0

    async def test_wait_when_depleted(self) -> None:
        """When bucket is depleted, acquire waits for the refill period."""
        # 120 RPM = 2 tokens/sec = 0.5 sec per token
        bucket = TokenBucket(requests_per_minute=120)
        # Drain bucket
        bucket.tokens = 0.0
        bucket.last_update = time.monotonic()

        start = time.monotonic()
        await bucket.acquire()
        elapsed = time.monotonic() - start

        # Should wait approximately 0.5s (1 token / 2 tokens/sec)
        assert elapsed >= 0.4
        stats = bucket.get_stats()
        assert stats["total_requests"] == 1


class TestNvidiaKeyGating:
    async def test_disabled_without_key(self) -> None:
        """Provider is disabled and chat raises ProviderUnavailable when key is missing."""
        provider = NvidiaProvider(api_key=None)
        assert not provider.is_enabled

        with pytest.raises(ProviderUnavailable, match="NVIDIA provider is not enabled"):
            async for _ in provider.chat(
                model="test",
                messages=[Message(role="user", content="hello")],
            ):
                pass

    async def test_health_reports_degraded_when_no_key(self) -> None:
        """Health check reports degraded status without throwing when key is omitted."""
        provider = NvidiaProvider(api_key="")
        h = await provider.health()
        assert h.status == "degraded"
        assert h.details.get("enabled") is False
