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


class TestNvidiaReasoningModels:
    async def test_streaming_reasoning_and_content(self) -> None:
        """When streaming delta has reasoning_content then content, TTFT is recorded on first chunk and content is streamed."""
        import httpx
        from noxai.models.cloud_health import CloudHealth

        chunks_to_send = [
            b'data: {"id": "c1", "choices": [{"index": 0, "delta": {"role": "assistant", "reasoning_content": "Analyzing user greeting..."}, "finish_reason": null}]}\n\n',
            b'data: {"id": "c1", "choices": [{"index": 0, "delta": {"content": "Hello"}, "finish_reason": null}]}\n\n',
            b'data: {"id": "c1", "choices": [{"index": 0, "delta": {"content": " world!"}, "finish_reason": "stop"}], "usage": {"completion_tokens": 10}}\n\n',
            b"data: [DONE]\n\n",
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"".join(chunks_to_send))

        transport = httpx.MockTransport(handler)
        ch = CloudHealth(has_key=True)
        provider = NvidiaProvider(
            api_key="nvapi-test1234567890",
            transport=transport,
            cloud_health=ch,
        )

        texts = []
        async for chunk in provider.chat(
            model="nvidia/nemotron-3.5-lightning-30b-a3b",
            messages=[Message(role="user", content="hi")],
            stream=True,
        ):
            if chunk.text:
                texts.append(chunk.text)

        assert "".join(texts) == "Hello world!"
        # Check cloud health recorded success and TTFT was captured
        assert ch.state.value == "active"
        assert ch.last_ttft is not None

    async def test_streaming_reasoning_fallback_when_content_empty(self) -> None:
        """When model hits token limit during reasoning with zero content, reasoning text is yielded as fallback."""
        import httpx
        from noxai.models.cloud_health import CloudHealth

        chunks_to_send = [
            b'data: {"id": "c2", "choices": [{"index": 0, "delta": {"role": "assistant", "reasoning_content": "Step 1: Calculate sum... "}, "finish_reason": null}]}\n\n',
            b'data: {"id": "c2", "choices": [{"index": 0, "delta": {"reasoning_content": "Result is 42."}, "finish_reason": "length"}]}\n\n',
            b"data: [DONE]\n\n",
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"".join(chunks_to_send))

        transport = httpx.MockTransport(handler)
        ch = CloudHealth(has_key=True)
        provider = NvidiaProvider(
            api_key="nvapi-test1234567890",
            transport=transport,
            cloud_health=ch,
        )

        texts = []
        async for chunk in provider.chat(
            model="nvidia/nemotron-3.5-lightning-30b-a3b",
            messages=[Message(role="user", content="compute")],
            stream=True,
        ):
            if chunk.text:
                texts.append(chunk.text)

        # Fallback emitted reasoning rather than empty void
        full_text = "".join(texts)
        assert "Result is 42." in full_text

    async def test_non_streaming_reasoning_fallback(self) -> None:
        """Non-streaming completions fall back to reasoning_content if content is None."""
        import json
        import httpx
        from noxai.models.cloud_health import CloudHealth

        body = {
            "id": "c3",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "reasoning_content": "Calculated answer is 100.",
                    },
                    "finish_reason": "length",
                }
            ],
            "usage": {"completion_tokens": 50},
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=body)

        transport = httpx.MockTransport(handler)
        provider = NvidiaProvider(
            api_key="nvapi-test1234567890",
            transport=transport,
            cloud_health=CloudHealth(has_key=True),
        )

        texts = []
        async for chunk in provider.chat(
            model="nvidia/nemotron-3.5-lightning-30b-a3b",
            messages=[Message(role="user", content="compute")],
            stream=False,
        ):
            if chunk.text:
                texts.append(chunk.text)

        assert "".join(texts) == "Calculated answer is 100."

