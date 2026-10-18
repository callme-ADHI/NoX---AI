"""NVIDIA API provider with token-bucket rate limiter and exponential backoff."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from noxai.models.base import (
    ChatChunk,
    Message,
    Provider,
    ProviderHealth,
    ProviderUnavailable,
    RateLimitError,
    ToolCall,
    ToolSpec,
)

log = logging.getLogger(__name__)


class TokenBucket:
    """Token-bucket rate limiter for enforcing requests per minute."""

    def __init__(self, requests_per_minute: int = 30) -> None:
        self.capacity = float(max(1, requests_per_minute))
        self.rate = self.capacity / 60.0  # tokens added per second
        self.tokens = self.capacity
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()
        self.total_requests = 0

    async def acquire(self) -> None:
        """Wait until a request token is available, then consume it."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.last_update = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)

            if self.tokens < 1.0:
                needed = 1.0 - self.tokens
                wait_time = needed / self.rate
                await asyncio.sleep(wait_time)
                self.last_update = time.monotonic()
                self.tokens = 0.0
            else:
                self.tokens -= 1.0

            self.total_requests += 1

    def get_stats(self) -> dict[str, Any]:
        """Return rate limiter statistics."""
        now = time.monotonic()
        elapsed = now - self.last_update
        current_tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        return {
            "rpm_limit": int(self.capacity),
            "tokens_available": round(current_tokens, 2),
            "total_requests": self.total_requests,
        }


class NvidiaProvider(Provider):
    """NVIDIA hosted model provider using OpenAI-compatible endpoints."""

    name: str = "nvidia"
    is_local: bool = False

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://integrate.api.nvidia.com/v1",
        requests_per_minute: int = 30,
        max_retries: int = 4,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.timeout = timeout
        self.rate_limiter = TokenBucket(requests_per_minute=requests_per_minute)

    @property
    def is_enabled(self) -> bool:
        """Return True if an API key is configured."""
        return bool(self.api_key and self.api_key.strip())

    def _headers(self) -> dict[str, str]:
        if not self.is_enabled:
            raise ProviderUnavailable(
                "NVIDIA provider is disabled: no API key configured. "
                "Add NVIDIA_API_KEY=nvapi-... to ~/.config/nox/secrets.env."
            )
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _format_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for msg in messages:
            item: dict[str, Any] = {
                "role": msg.role,
                "content": msg.get_model_text(),
            }
            if msg.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in msg.tool_calls
                ]
            if msg.tool_call_id:
                item["tool_call_id"] = msg.tool_call_id
            if msg.name:
                item["name"] = msg.name
            formatted.append(item)
        return formatted

    def _format_tools(self, tools: list[ToolSpec] | None) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

    async def _request_with_retry(
        self,
        client: httpx.AsyncClient,
        payload: dict[str, Any],
        stream: bool = True,
    ) -> httpx.Response:
        """Execute request with rate limiting and exponential backoff retry."""
        attempt = 0
        backoff = 1.0

        while True:
            attempt += 1
            await self.rate_limiter.acquire()

            try:
                headers = self._headers()
                req = client.build_request(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                resp = await client.send(req, stream=stream)

                if resp.status_code == 429:
                    retry_after_header = resp.headers.get("Retry-After")
                    wait_sec = float(retry_after_header) if retry_after_header else backoff
                    jitter = random.uniform(0.1, 0.5)  # noqa: S311
                    total_wait = wait_sec + jitter
                    log.warning(
                        "NVIDIA 429 rate limited. Attempt %d/%d, waiting %.2fs",
                        attempt,
                        self.max_retries,
                        total_wait,
                    )
                    await resp.aclose()
                    if attempt > self.max_retries:
                        raise RateLimitError(
                            f"NVIDIA rate limit exceeded after {self.max_retries} retries."
                        )
                    await asyncio.sleep(total_wait)
                    backoff *= 2.0
                    continue

                if resp.status_code >= 500:
                    await resp.aclose()
                    if attempt > self.max_retries:
                        raise ProviderUnavailable(
                            f"NVIDIA API server error ({resp.status_code}) "
                            f"after {self.max_retries} retries."
                        )
                    jitter = random.uniform(0.1, 0.4)  # noqa: S311
                    await asyncio.sleep(backoff + jitter)
                    backoff *= 2.0
                    continue

                if resp.is_error:
                    error_text = await resp.aread()
                    await resp.aclose()
                    decoded = error_text.decode("utf-8", errors="replace")
                    raise ProviderUnavailable(f"NVIDIA API error ({resp.status_code}): {decoded}")

                return resp

            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                if attempt > self.max_retries:
                    raise ProviderUnavailable(
                        f"NVIDIA API request failed after {self.max_retries} retries: {exc}"
                    ) from exc
                jitter = random.uniform(0.1, 0.3)  # noqa: S311
                await asyncio.sleep(backoff + jitter)
                backoff *= 2.0

    async def chat(
        self,
        *,
        model: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        stream: bool = True,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ChatChunk]:
        """Execute chat completion via NVIDIA API."""
        if not self.is_enabled:
            raise ProviderUnavailable(
                "NVIDIA provider is not enabled (missing NVIDIA_API_KEY in secrets)."
            )

        payload: dict[str, Any] = {
            "model": model,
            "messages": self._format_messages(messages),
            "stream": stream,
        }
        tools_payload = self._format_tools(tools)
        if tools_payload:
            payload["tools"] = tools_payload

        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        client = httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=10.0))
        try:
            resp = await self._request_with_retry(client, payload, stream=stream)

            if stream:
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk_data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        choices = chunk_data.get("choices", [])
                        if not choices:
                            continue
                        choice = choices[0]
                        delta = choice.get("delta", {})
                        delta_text = delta.get("content") or ""
                        finish_reason = choice.get("finish_reason")

                        tool_calls: list[ToolCall] = []
                        raw_tcs = delta.get("tool_calls")
                        if raw_tcs and isinstance(raw_tcs, list):
                            for tc in raw_tcs:
                                fn = tc.get("function", {})
                                fn_name = fn.get("name", "")
                                fn_args_str = fn.get("arguments", "")
                                try:
                                    fn_args = json.loads(fn_args_str) if fn_args_str else {}
                                except Exception:
                                    fn_args = {"raw": fn_args_str}
                                tool_calls.append(
                                    ToolCall(
                                        id=tc.get("id", ""),
                                        name=fn_name,
                                        arguments=fn_args,
                                    )
                                )

                        usage = chunk_data.get("usage")
                        yield ChatChunk(
                            delta_text=delta_text,
                            delta_tool_calls=tool_calls,
                            finish_reason=finish_reason,
                            usage=usage,
                        )
                await resp.aclose()
            else:
                data = resp.json()
                choices = data.get("choices", [])
                delta_text = ""
                tool_calls = []
                finish_reason = None
                if choices:
                    c = choices[0]
                    finish_reason = c.get("finish_reason")
                    msg = c.get("message", {})
                    delta_text = msg.get("content") or ""
                    raw_tcs = msg.get("tool_calls")
                    if raw_tcs and isinstance(raw_tcs, list):
                        for tc in raw_tcs:
                            fn = tc.get("function", {})
                            try:
                                fn_args = json.loads(fn.get("arguments", "{}"))
                            except Exception:
                                fn_args = {"raw": fn.get("arguments", "")}
                            tool_calls.append(
                                ToolCall(
                                    id=tc.get("id", ""),
                                    name=fn.get("name", ""),
                                    arguments=fn_args,
                                )
                            )

                await resp.aclose()
                yield ChatChunk(
                    delta_text=delta_text,
                    delta_tool_calls=tool_calls,
                    finish_reason=finish_reason,
                    usage=data.get("usage"),
                )
        finally:
            await client.aclose()

    async def embed(
        self,
        *,
        model: str,
        texts: list[str],
    ) -> list[list[float]]:
        """NVIDIA embedding is not used in Phase 1 (local nomic-embed-text only)."""
        raise NotImplementedError("Embeddings via NVIDIA provider are not used in Phase 1.")

    async def health(self) -> ProviderHealth:
        """Check NVIDIA provider connectivity and rate limiter status."""
        if not self.is_enabled:
            return ProviderHealth(
                status="degraded",
                latency_ms=0.0,
                details={"enabled": False, "reason": "No API key configured"},
            )

        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{self.base_url}/models",
                    headers=self._headers(),
                )
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                stats = self.rate_limiter.get_stats()
                if resp.status_code == 200:
                    return ProviderHealth(
                        status="ok",
                        latency_ms=elapsed_ms,
                        details={"enabled": True, "rate_limiter": stats},
                    )
                return ProviderHealth(
                    status="degraded",
                    latency_ms=elapsed_ms,
                    details={"status_code": resp.status_code, "rate_limiter": stats},
                )
        except Exception as exc:
            return ProviderHealth(
                status="error",
                latency_ms=0.0,
                details={"error": str(exc), "rate_limiter": self.rate_limiter.get_stats()},
            )
