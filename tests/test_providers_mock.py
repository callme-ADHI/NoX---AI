"""Mock-based unit tests for OllamaProvider and NvidiaProvider (Phase E7.3)."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from noxai.models.base import (
    ChatChunk,
    Message,
    ModelNotAvailable,
    ModelNotFound,
    ProviderUnavailable,
    RateLimitError,
    ToolCall,
    ToolSpec,
)
from noxai.models.nvidia import NvidiaProvider, TokenBucket
from noxai.models.ollama import OllamaProvider

# ============================================================================
# OLLAMA PROVIDER TESTS
# ============================================================================


@pytest.mark.asyncio
async def test_ollama_stream_parsing_text_and_tool_calls() -> None:
    """Ollama streaming parses text chunks and tool calls (arguments as dict and json string)."""
    lines = [
        json.dumps({"message": {"content": "Thinking..."}}),
        json.dumps(
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "lookup",
                                "arguments": {"query": "python"},
                            }
                        },
                        {
                            "function": {
                                "name": "calculate",
                                "arguments": '{"expr": "2+2"}',
                            }
                        },
                    ],
                }
            }
        ),
        json.dumps(
            {
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 12,
                "eval_count": 25,
            }
        ),
    ]
    raw_body = ("\n".join(lines) + "\n").encode("utf-8")

    captured_payload: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload
        captured_payload = json.loads(request.read())
        return httpx.Response(
            200,
            content=raw_body,
            headers={"Content-Type": "application/x-ndjson"},
        )

    transport = httpx.MockTransport(handler)
    provider = OllamaProvider(
        base_url="http://127.0.0.1:11434",
        keep_alive="15m",
        num_ctx=4096,
        transport=transport,
    )

    messages = [Message(role="user", content="Hello")]
    tools = [ToolSpec(name="lookup", description="Lookup tool", parameters={"type": "object"})]

    chunks: list[ChatChunk] = []
    async for chunk in provider.chat(
        model="qwen2.5:14b",
        messages=messages,
        tools=tools,
        stream=True,
        temperature=0.2,
        max_tokens=100,
    ):
        chunks.append(chunk)

    assert len(chunks) == 3
    assert chunks[0].delta_text == "Thinking..."
    assert len(chunks[1].delta_tool_calls) == 2
    assert chunks[1].delta_tool_calls[0].name == "lookup"
    assert chunks[1].delta_tool_calls[0].arguments == {"query": "python"}
    assert chunks[1].delta_tool_calls[1].name == "calculate"
    assert chunks[1].delta_tool_calls[1].arguments == {"expr": "2+2"}
    assert chunks[2].finish_reason == "stop"
    assert chunks[2].usage == {"prompt_tokens": 12, "completion_tokens": 25}

    # Verify keep_alive, num_ctx, options in captured payload
    assert captured_payload["keep_alive"] == "15m"
    assert captured_payload["options"]["num_ctx"] == 4096
    assert captured_payload["options"]["temperature"] == 0.2
    assert captured_payload["options"]["num_predict"] == 100


@pytest.mark.asyncio
async def test_ollama_non_streaming_chat() -> None:
    """Ollama non-streaming returns text and parsed tool calls."""
    response_data = {
        "message": {
            "content": "Final answer",
            "tool_calls": [
                {
                    "function": {
                        "name": "calc",
                        "arguments": '{"x": 1}',
                    }
                }
            ],
        },
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 8,
        "eval_count": 14,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_data)

    provider = OllamaProvider(transport=httpx.MockTransport(handler))
    chunks: list[ChatChunk] = []
    async for chunk in provider.chat(
        model="qwen2.5:14b",
        messages=[Message(role="user", content="Test")],
        stream=False,
    ):
        chunks.append(chunk)

    assert len(chunks) == 1
    assert chunks[0].delta_text == "Final answer"
    assert len(chunks[0].delta_tool_calls) == 1
    assert chunks[0].delta_tool_calls[0].arguments == {"x": 1}
    assert chunks[0].finish_reason == "stop"


@pytest.mark.asyncio
async def test_ollama_404_raises_model_not_found() -> None:
    """Ollama returning 404 raises ModelNotAvailable (and ModelNotFound alias) with pull hint."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model 'nonexistent:latest' not found"})

    provider = OllamaProvider(transport=httpx.MockTransport(handler))

    with pytest.raises(ModelNotAvailable) as exc_info:
        async for _ in provider.chat(
            model="nonexistent:latest",
            messages=[Message(role="user", content="hi")],
            stream=True,
        ):
            pass

    assert "ollama pull nonexistent:latest" in str(exc_info.value)
    assert issubclass(ModelNotFound, ModelNotAvailable)

    # Also test non-streaming 404
    with pytest.raises(ModelNotAvailable) as exc_info_nonstream:
        async for _ in provider.chat(
            model="nonexistent:latest",
            messages=[Message(role="user", content="hi")],
            stream=False,
        ):
            pass
    assert "ollama pull nonexistent:latest" in str(exc_info_nonstream.value)


@pytest.mark.asyncio
async def test_ollama_connect_error_raises_provider_unavailable() -> None:
    """Connection refused raises ProviderUnavailable with systemctl start command."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    provider = OllamaProvider(transport=httpx.MockTransport(handler))

    with pytest.raises(ProviderUnavailable) as exc_info:
        async for _ in provider.chat(
            model="qwen2.5:14b",
            messages=[Message(role="user", content="hi")],
            stream=True,
        ):
            pass

    assert "sudo systemctl start ollama" in str(exc_info.value)


@pytest.mark.asyncio
async def test_ollama_http_error_raises_provider_unavailable() -> None:
    """Non-404 HTTP errors raise ProviderUnavailable."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Ollama Crash")

    provider = OllamaProvider(transport=httpx.MockTransport(handler))

    with pytest.raises(ProviderUnavailable) as exc_info:
        async for _ in provider.chat(
            model="qwen2.5:14b",
            messages=[Message(role="user", content="hi")],
            stream=True,
        ):
            pass
    assert "status 500" in str(exc_info.value)


@pytest.mark.asyncio
async def test_ollama_wait_until_ready_and_health() -> None:
    """Ollama health checks and wait_until_ready logic."""

    # 1. Successful health
    def ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"models": [{"name": "qwen2.5:14b"}, {"name": "nomic-embed-text"}]}
        )

    p_ok = OllamaProvider(transport=httpx.MockTransport(ok_handler))
    health = await p_ok.health()
    assert health.status == "ok"
    assert health.details["count"] == 2
    ready = await p_ok.wait_until_ready(timeout_seconds=1.0)
    assert ready is True

    # 2. Degraded health (non-200 status)
    def degraded_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service busy")

    p_degraded = OllamaProvider(transport=httpx.MockTransport(degraded_handler))
    health_deg = await p_degraded.health()
    assert health_deg.status == "degraded"

    # 3. Error health (exception)
    def err_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Ollama down")

    p_err = OllamaProvider(transport=httpx.MockTransport(err_handler))
    health_err = await p_err.health()
    assert health_err.status == "error"

    # 4. wait_until_ready times out
    ready_false = await p_err.wait_until_ready(timeout_seconds=0.2)
    assert ready_false is False


@pytest.mark.asyncio
async def test_ollama_consumer_cancellation_closes_stream() -> None:
    """Cancelling the consumer closes the HTTP stream and releases resources."""
    closed_called = False

    class TrackingResponse(httpx.Response):
        async def aclose(self) -> None:
            nonlocal closed_called
            closed_called = True
            await super().aclose()

    def handler(request: httpx.Request) -> httpx.Response:
        async def stream_content():
            for i in range(100):
                yield (json.dumps({"message": {"content": f"token_{i}"}}) + "\n").encode()
                await asyncio.sleep(0.01)

        return TrackingResponse(
            200,
            content=stream_content(),
            headers={"Content-Type": "application/x-ndjson"},
        )

    provider = OllamaProvider(transport=httpx.MockTransport(handler))
    chat_gen = provider.chat(
        model="qwen2.5:14b",
        messages=[Message(role="user", content="Count")],
        stream=True,
    )

    tokens = []
    async for chunk in chat_gen:
        tokens.append(chunk.delta_text)
        if len(tokens) == 3:
            # Simulate consumer breaking / cancelling early
            await chat_gen.aclose()
            break

    assert len(tokens) == 3
    assert closed_called is True


@pytest.mark.asyncio
async def test_ollama_embed_batching_and_fallback() -> None:
    """Ollama embed uses /api/embed, falls back to /api/embeddings, and handles errors."""

    # 1. Modern /api/embed success
    def modern_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]})
        return httpx.Response(404)

    p1 = OllamaProvider(transport=httpx.MockTransport(modern_handler))
    vecs = await p1.embed(model="nomic-embed-text", texts=["a", "b"])
    assert vecs == [[0.1, 0.2], [0.3, 0.4]]

    # 2. Empty texts returns empty
    assert await p1.embed(model="nomic-embed-text", texts=[]) == []

    # 3. Fallback to /api/embeddings if /api/embed returns 404
    call_count = 0

    def fallback_handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        if request.url.path == "/api/embed":
            return httpx.Response(404, text="Not found")
        if request.url.path == "/api/embeddings":
            call_count += 1
            return httpx.Response(200, json={"embedding": [0.5, 0.6]})
        return httpx.Response(500)

    p2 = OllamaProvider(transport=httpx.MockTransport(fallback_handler))
    vecs_fallback = await p2.embed(model="nomic-embed-text", texts=["t1", "t2"])
    assert len(vecs_fallback) == 2
    assert call_count == 2

    # 4. Model not found on embedding endpoint raises ModelNotAvailable
    def not_found_embed_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model not found"})

    p3 = OllamaProvider(transport=httpx.MockTransport(not_found_embed_handler))
    with pytest.raises(ModelNotAvailable) as exc:
        await p3.embed(model="missing-embed", texts=["test"])
    assert "ollama pull missing-embed" in str(exc.value)


# ============================================================================
# NVIDIA PROVIDER TESTS
# ============================================================================


def test_nvidia_is_enabled_and_repr_masking() -> None:
    """NvidiaProvider is enabled only with key, and repr() never leaks the full key."""
    p_none = NvidiaProvider(api_key=None)
    assert p_none.is_enabled is False
    assert "None" in repr(p_none)

    p_empty = NvidiaProvider(api_key="   ")
    assert p_empty.is_enabled is False

    secret_key = "nvapi-secret1234567890abcdefghijklmnopqrstuvwxyz"
    p_key = NvidiaProvider(api_key=secret_key)
    assert p_key.is_enabled is True

    rep = repr(p_key)
    assert secret_key not in rep
    assert "nvapi-...***" in rep


@pytest.mark.asyncio
async def test_nvidia_disabled_raises_provider_unavailable() -> None:
    """Calling chat without an API key raises ProviderUnavailable without contacting network."""
    provider = NvidiaProvider(api_key=None)

    with pytest.raises(ProviderUnavailable) as exc:
        async for _ in provider.chat(
            model="meta/llama-3.1-70b-instruct",
            messages=[Message(role="user", content="hello")],
        ):
            pass
    assert "missing NVIDIA_API_KEY" in str(exc.value)

    # Health indicates disabled
    health = await provider.health()
    assert health.status == "degraded"
    assert health.details["enabled"] is False


@pytest.mark.asyncio
async def test_token_bucket_acquisition_and_stats() -> None:
    """TokenBucket rate limiter acquires tokens and computes accurate statistics."""
    bucket = TokenBucket(requests_per_minute=60)  # 1 token/sec
    assert bucket.tokens == 60.0

    await bucket.acquire()
    stats = bucket.get_stats()
    assert stats["rpm_limit"] == 60
    assert stats["total_requests"] == 1
    assert stats["tokens_available"] <= 60.0


@pytest.mark.asyncio
async def test_nvidia_retry_after_numeric_and_http_date() -> None:
    """NvidiaProvider handles 429 with Retry-After as integer seconds and as HTTP-date."""
    attempts = 0

    # Test 1: Numeric Retry-After
    def handler_numeric(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0.01"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]},
        )

    p_num = NvidiaProvider(
        api_key="nvapi-testkey123456789",
        max_retries=2,
        transport=httpx.MockTransport(handler_numeric),
    )
    chunks = [
        c
        async for c in p_num.chat(
            model="m", messages=[Message(role="user", content="hi")], stream=False
        )
    ]
    assert len(chunks) == 1
    assert chunks[0].delta_text == "OK"
    assert attempts == 2

    # Test 2: HTTP-date Retry-After
    future_date = (datetime.now(UTC) + timedelta(seconds=0.01)).strftime(
        "%a, %d %b %Y %H:%M:%S GMT"
    )
    attempts_date = 0

    def handler_date(request: httpx.Request) -> httpx.Response:
        nonlocal attempts_date
        attempts_date += 1
        if attempts_date == 1:
            return httpx.Response(429, headers={"Retry-After": future_date})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "DateOK"}, "finish_reason": "stop"}]},
        )

    p_date = NvidiaProvider(
        api_key="nvapi-testkey123456789",
        max_retries=2,
        transport=httpx.MockTransport(handler_date),
    )
    chunks_date = [
        c
        async for c in p_date.chat(
            model="m", messages=[Message(role="user", content="hi")], stream=False
        )
    ]
    assert chunks_date[0].delta_text == "DateOK"
    assert attempts_date == 2


@pytest.mark.asyncio
async def test_nvidia_429_exhausted_raises_rate_limit_error() -> None:
    """When 429 persists beyond max_retries, RateLimitError is raised."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "0.001"})

    provider = NvidiaProvider(
        api_key="nvapi-testkey123456789",
        max_retries=2,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(RateLimitError) as exc:
        async for _ in provider.chat(
            model="m", messages=[Message(role="user", content="hi")], stream=False
        ):
            pass
    assert "rate limit exceeded" in str(exc.value)


@pytest.mark.asyncio
async def test_nvidia_5xx_and_timeout_backoff() -> None:
    """NvidiaProvider retries 5xx server errors and raises ProviderUnavailable on exhaustion."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, text="Service Unavailable")

    provider = NvidiaProvider(
        api_key="nvapi-testkey123456789",
        max_retries=2,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProviderUnavailable) as exc:
        async for _ in provider.chat(
            model="m", messages=[Message(role="user", content="hi")], stream=False
        ):
            pass

    assert attempts == 3  # initial + 2 retries
    assert "NVIDIA API server error (503)" in str(exc.value)


@pytest.mark.asyncio
async def test_nvidia_sse_stream_parsing_and_done() -> None:
    """NvidiaProvider correctly parses SSE streams, tool call deltas, skips malformed lines, and stops at [DONE]."""
    sse_lines = [
        "",  # empty line skipped
        ": keep-alive comment",  # comment line skipped
        "data: {malformed json",  # malformed JSON line skipped
        'data: {"choices": [{"delta": {"content": "Hello "}}]}',
        'data: {"choices": [{"delta": {"tool_calls": [{"id": "call_1", "function": {"name": "search", "arguments": "{\\"q\\": "}}]}}]}',
        'data: {"choices": [{"delta": {"tool_calls": [{"id": "call_1", "function": {"name": "search", "arguments": "\\"ai\\"}"}}]}}]}',
        'data: {"choices": [{"delta": {"content": "world!"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}',
        "data: [DONE]",
        'data: {"choices": [{"delta": {"content": "should not be reached"}}]}',
    ]
    raw_sse = ("\n\n".join(sse_lines) + "\n\n").encode("utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw_sse, headers={"Content-Type": "text/event-stream"})

    provider = NvidiaProvider(
        api_key="nvapi-testkey123456789",
        transport=httpx.MockTransport(handler),
    )

    chunks: list[ChatChunk] = []
    async for chunk in provider.chat(
        model="meta/llama-3.1-70b-instruct",
        messages=[Message(role="user", content="Hi")],
        stream=True,
    ):
        chunks.append(chunk)

    assert any(c.delta_text == "Hello " for c in chunks)
    assert any(c.delta_text == "world!" for c in chunks)
    assert not any(c.delta_text == "should not be reached" for c in chunks)

    # Check tool call chunks
    tc_chunks = [c for c in chunks if c.delta_tool_calls]
    assert len(tc_chunks) == 2
    assert tc_chunks[0].delta_tool_calls[0].name == "search"


@pytest.mark.asyncio
async def test_nvidia_embed_and_health() -> None:
    """NvidiaProvider embed raises NotImplementedError, and health checks status."""
    provider = NvidiaProvider(api_key="nvapi-testkey123456789")
    with pytest.raises(NotImplementedError):
        await provider.embed(model="m", texts=["a"])

    # Health OK
    def ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "meta/llama-3.1-70b-instruct"}]})

    p_ok = NvidiaProvider(
        api_key="nvapi-testkey123456789", transport=httpx.MockTransport(ok_handler)
    )
    h_ok = await p_ok.health()
    assert h_ok.status == "ok"

    # Health error
    def err_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Network fail")

    p_err = NvidiaProvider(
        api_key="nvapi-testkey123456789", transport=httpx.MockTransport(err_handler)
    )
    h_err = await p_err.health()
    assert h_err.status == "error"


@pytest.mark.asyncio
async def test_token_bucket_wait_and_sleep() -> None:
    """TokenBucket waits and sleeps when tokens fall below 1.0."""
    bucket = TokenBucket(requests_per_minute=600)  # 10 tokens/sec
    bucket.tokens = 0.5  # Less than 1
    await bucket.acquire()
    assert bucket.total_requests == 1


@pytest.mark.asyncio
async def test_nvidia_format_messages_with_tool_call_id_and_name() -> None:
    """NvidiaProvider formats messages with tool_call_id, name, and tool calls."""
    tc = ToolCall(id="c1", name="tool_fn", arguments={"k": "v"})
    messages = [
        Message(role="assistant", content="", tool_calls=[tc]),
        Message(role="tool", content="tool output", tool_call_id="c1", name="tool_fn"),
    ]
    tools = [ToolSpec(name="tool_fn", description="a tool", parameters={"type": "object"})]

    captured_payload: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload
        captured_payload = json.loads(request.read())
        return httpx.Response(200, json={"choices": [{"message": {"content": "done"}}]})

    provider = NvidiaProvider(api_key="nvapi-key123", transport=httpx.MockTransport(handler))
    chunks = [
        c async for c in provider.chat(model="m", messages=messages, tools=tools, stream=False)
    ]
    assert len(chunks) == 1
    assert len(captured_payload["messages"]) == 2
    assert captured_payload["messages"][0]["tool_calls"][0]["id"] == "c1"
    assert captured_payload["messages"][1]["tool_call_id"] == "c1"
    assert captured_payload["messages"][1]["name"] == "tool_fn"
    assert len(captured_payload["tools"]) == 1


@pytest.mark.asyncio
async def test_nvidia_client_error_and_timeout() -> None:
    """NvidiaProvider raises on 400 Bad Request and retries on TimeoutException."""

    # 1. 400 Bad Request
    def bad_request_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="Bad Prompt")

    p1 = NvidiaProvider(api_key="nvapi-key123", transport=httpx.MockTransport(bad_request_handler))
    with pytest.raises(ProviderUnavailable) as exc1:
        async for _ in p1.chat(
            model="m", messages=[Message(role="user", content="hi")], stream=False
        ):
            pass
    assert "NVIDIA API error (400)" in str(exc1.value)

    # 2. TimeoutException
    timeout_attempts = 0

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        nonlocal timeout_attempts
        timeout_attempts += 1
        raise httpx.ReadTimeout("Read timed out")

    p2 = NvidiaProvider(
        api_key="nvapi-key123", max_retries=1, transport=httpx.MockTransport(timeout_handler)
    )
    with pytest.raises(ProviderUnavailable) as exc2:
        async for _ in p2.chat(
            model="m", messages=[Message(role="user", content="hi")], stream=False
        ):
            pass
    assert timeout_attempts == 2
    assert "failed after 1 retries" in str(exc2.value)


@pytest.mark.asyncio
async def test_nvidia_non_streaming_tool_calls_and_degraded_health() -> None:
    """NvidiaProvider non-streaming handles tool_calls, and health handles non-200."""
    response_data = {
        "choices": [
            {
                "message": {
                    "content": "Using tool",
                    "tool_calls": [
                        {
                            "id": "c10",
                            "function": {"name": "test_fn", "arguments": '{"a": 1}'},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"total_tokens": 15},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_data)

    p = NvidiaProvider(api_key="nvapi-key123", transport=httpx.MockTransport(handler))
    chunks = [
        c
        async for c in p.chat(
            model="m", messages=[Message(role="user", content="hi")], stream=False
        )
    ]
    assert len(chunks) == 1
    assert chunks[0].delta_tool_calls[0].name == "test_fn"
    assert chunks[0].finish_reason == "tool_calls"

    # Health degraded
    def degraded_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="Bad Gateway")

    p_deg = NvidiaProvider(api_key="nvapi-key123", transport=httpx.MockTransport(degraded_handler))
    h = await p_deg.health()
    assert h.status == "degraded"
    assert h.details["status_code"] == 502
