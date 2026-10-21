"""NVIDIA API provider with token-bucket rate limiter and exponential backoff."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC
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
from noxai.models.budget import RequestBudget
from noxai.models.cloud_health import CloudHealth
from noxai.models.key_pool import NvidiaKeyPool

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


def _parse_retry_after(header_val: str | None, default: float) -> float:
    if not header_val:
        return default
    try:
        return max(0.0, float(header_val))
    except ValueError:
        pass
    try:
        import email.utils
        from datetime import datetime

        dt = email.utils.parsedate_to_datetime(header_val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        diff = (dt - datetime.now(UTC)).total_seconds()
        return max(0.0, diff)
    except Exception:
        return default


class NvidiaProvider(Provider):
    """NVIDIA hosted model provider using OpenAI-compatible endpoints."""

    name: str = "nvidia"
    is_local: bool = False

    def __init__(
        self,
        api_key: str | None = None,
        api_keys: list[str] | None = None,
        key_pool: NvidiaKeyPool | None = None,
        base_url: str = "https://integrate.api.nvidia.com/v1",
        requests_per_minute: int = 30,
        max_retries: int = 4,
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        cloud_health: CloudHealth | None = None,
        budget: RequestBudget | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.timeout = timeout
        self.transport = transport
        self.rate_limiter = TokenBucket(requests_per_minute=requests_per_minute)
        self.budget = budget or RequestBudget(requests_per_minute=requests_per_minute)

        # Initialize key pool
        if key_pool is not None:
            self.key_pool = key_pool
        else:
            init_keys: list[str] = []
            if api_keys:
                init_keys.extend(api_keys)
            if api_key and api_key not in init_keys:
                init_keys.append(api_key)
            self.key_pool = NvidiaKeyPool(keys=init_keys, default_rpm_per_key=requests_per_minute)

        self._fallback_key = (
            api_key
            or (self.key_pool.get_all_keys()[0] if not self.key_pool.is_empty() else None)
        )
        self.cloud_health = (
            cloud_health
            if cloud_health is not None
            else CloudHealth(has_key=self.is_enabled)
        )

    def __repr__(self) -> str:
        count = self.key_pool.total_keys
        masked = f"{self.api_key[:6]}...***" if self.api_key else "None"
        return f"NvidiaProvider(base_url={self.base_url!r}, keys={count}, primary={masked!r})"

    @property
    def api_key(self) -> str | None:
        """Return the primary or first active API key."""
        if not self.key_pool.is_empty():
            return self.key_pool.get_all_keys()[0]
        return self._fallback_key

    @api_key.setter
    def api_key(self, val: str | None) -> None:
        self._fallback_key = val
        if val:
            self.key_pool.add_key(val)

    @property
    def is_enabled(self) -> bool:
        """Return True if at least one API key is configured."""
        return not self.key_pool.is_empty()

    def _headers(self, key: str | None = None) -> dict[str, str]:
        chosen_key = key or self.api_key
        if not chosen_key:
            raise ProviderUnavailable(
                "NVIDIA provider is disabled: no API key configured. "
                "Add NVIDIA_API_KEY=nvapi-... to ~/.config/nox/secrets.env."
            )
        return {
            "Authorization": f"Bearer {chosen_key}",
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
        is_interactive: bool = True,
    ) -> httpx.Response:
        """Execute request with rate limiting and exponential backoff retry."""
        if not self.cloud_health.can_attempt():
            raise ProviderUnavailable(
                f"NVIDIA cloud circuit breaker is {self.cloud_health.circuit_breaker.state.value} "
                f"({self.cloud_health.reason})"
            )

        acquired = await self.budget.acquire(is_interactive=is_interactive, timeout=0.0)
        if not acquired:
            self.cloud_health.record_failure(
                error="Request budget exhausted",
                status_code=429,
                retry_after=2.0,
            )
            raise RateLimitError("NVIDIA request budget exhausted")

        attempt = 0
        backoff = 1.0

        while True:
            attempt += 1
            await self.rate_limiter.acquire()

            active_key_entry = self.key_pool.acquire_best_key()

            try:
                headers = self._headers(active_key_entry.api_key)
                req = client.build_request(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                resp = await client.send(req, stream=stream)

                if resp.status_code == 429:
                    retry_after_header = resp.headers.get("Retry-After")
                    wait_sec = _parse_retry_after(retry_after_header, backoff)
                    jitter = random.uniform(0.1, 0.5)  # noqa: S311
                    total_wait = wait_sec + jitter
                    self.key_pool.mark_rate_limited(
                        active_key_entry.api_key, cooldown_seconds=wait_sec
                    )
                    self.cloud_health.record_failure(
                        error=f"HTTP 429 on {active_key_entry.label}",
                        status_code=429,
                        retry_after=wait_sec,
                    )
                    await resp.aclose()

                    # Transparent failover: check if another key is available right now
                    now = time.monotonic()
                    alts = [
                        e for e in self.key_pool._entries
                        if e.api_key != active_key_entry.api_key and e.is_available(now)
                    ]
                    if alts and attempt <= self.max_retries:
                        log.info(
                            "NVIDIA %s hit 429; immediate failover to %s",
                            active_key_entry.label,
                            alts[0].label,
                        )
                        continue

                    log.warning(
                        "All available keys rate limited. Attempt %d/%d, waiting %.2fs",
                        attempt,
                        self.max_retries,
                        total_wait,
                    )
                    if attempt > self.max_retries:
                        raise RateLimitError(
                            "NVIDIA rate limit exceeded across key pool after "
                            f"{self.max_retries} retries."
                        )
                    await asyncio.sleep(total_wait)
                    backoff *= 2.0
                    continue

                if resp.status_code in (401, 403):
                    error_text = await resp.aread()
                    await resp.aclose()
                    decoded = error_text.decode("utf-8", errors="replace")
                    self.key_pool.mark_error(active_key_entry.api_key, cooldown_seconds=300.0)
                    self.cloud_health.record_failure(
                        error=decoded,
                        status_code=resp.status_code,
                    )
                    # Transparent failover if another key exists
                    now = time.monotonic()
                    alts = [
                        e for e in self.key_pool._entries
                        if e.api_key != active_key_entry.api_key and not e.is_cooling_down(now)
                    ]
                    if alts and attempt <= self.max_retries:
                        log.warning(
                            "NVIDIA %s auth failed (%d); switching to %s",
                            active_key_entry.label,
                            resp.status_code,
                            alts[0].label,
                        )
                        continue
                    raise ProviderUnavailable(
                        f"NVIDIA API authorization error ({resp.status_code}): {decoded}"
                    )

                if resp.status_code >= 500:
                    await resp.aclose()
                    self.cloud_health.record_failure(
                        error=f"Server error {resp.status_code}",
                        status_code=resp.status_code,
                    )
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
                    self.cloud_health.record_failure(
                        error=decoded,
                        status_code=resp.status_code,
                    )
                    raise ProviderUnavailable(f"NVIDIA API error ({resp.status_code}): {decoded}")

                self.key_pool.mark_success(active_key_entry.api_key)
                return resp

            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                self.cloud_health.record_failure(error=exc)
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
        **kwargs: Any,
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

        is_interactive = bool(kwargs.get("is_interactive", True))
        t0 = time.perf_counter()
        ttft_sec: float | None = None

        client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            transport=self.transport,
        )
        try:
            resp = await self._request_with_retry(
                client, payload, stream=stream, is_interactive=is_interactive
            )

            if stream:
                accumulated_tool_calls: dict[int, dict[str, Any]] = {}
                tool_calls_emitted = False
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
                        raw_tcs = delta.get("tool_calls")

                        if (delta_text or raw_tcs) and ttft_sec is None:
                            ttft_sec = time.perf_counter() - t0

                        delta_tool_calls: list[ToolCall] = []
                        if raw_tcs and isinstance(raw_tcs, list):
                            for tc in raw_tcs:
                                idx = tc.get("index", 0)
                                if idx not in accumulated_tool_calls:
                                    accumulated_tool_calls[idx] = {
                                        "id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                                        "name": "",
                                        "arguments_str": "",
                                    }
                                entry = accumulated_tool_calls[idx]
                                if tc.get("id"):
                                    entry["id"] = tc["id"]
                                fn = tc.get("function", {})
                                if fn.get("name"):
                                    entry["name"] = fn["name"]
                                if fn.get("arguments"):
                                    entry["arguments_str"] += fn["arguments"]

                                args_str = entry["arguments_str"].strip()
                                try:
                                    parsed_args = json.loads(args_str) if args_str else {}
                                except Exception:
                                    parsed_args = {"raw": args_str}

                                delta_tool_calls.append(
                                    ToolCall(
                                        id=entry["id"],
                                        name=entry["name"],
                                        arguments=parsed_args,
                                    )
                                )

                        usage = chunk_data.get("usage")
                        yield ChatChunk(
                            delta_text=delta_text,
                            delta_tool_calls=delta_tool_calls,
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

            latency_sec = time.perf_counter() - t0
            self.cloud_health.record_success(
                latency_seconds=latency_sec,
                ttft_seconds=ttft_sec,
            )
        finally:
            await client.aclose()

    async def embed(
        self,
        *,
        model: str,
        texts: list[str],
        keep_alive: str | None = None,
    ) -> list[list[float]]:
        """NVIDIA embedding is not used in Phase 1 (local nomic-embed-text only)."""
        raise NotImplementedError("Embeddings via NVIDIA provider are not used in Phase 1.")

    async def health(self) -> ProviderHealth:
        """Check NVIDIA provider connectivity and rate limiter status."""
        if not self.is_enabled:
            return ProviderHealth(
                status="degraded",
                latency_ms=0.0,
                details={
                    "enabled": False,
                    "reason": "No API key configured",
                    "cloud_health": self.cloud_health.to_dict(),
                    "budget": self.budget.to_dict(),
                },
            )

        start = time.perf_counter()
        try:
            kwargs: dict[str, Any] = {"timeout": 5.0}
            if self.transport is not None:
                kwargs["transport"] = self.transport
            async with httpx.AsyncClient(**kwargs) as client:
                resp = await client.get(
                    f"{self.base_url}/models",
                    headers=self._headers(),
                )
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                stats = self.rate_limiter.get_stats()
                if resp.status_code == 200:
                    self.cloud_health.record_success(latency_seconds=elapsed_ms / 1000.0)
                    return ProviderHealth(
                        status="ok",
                        latency_ms=elapsed_ms,
                        details={
                            "enabled": True,
                            "rate_limiter": stats,
                            "cloud_health": self.cloud_health.to_dict(),
                            "budget": self.budget.to_dict(),
                        },
                    )
                self.cloud_health.record_failure(
                    f"HTTP {resp.status_code}", status_code=resp.status_code
                )
                return ProviderHealth(
                    status="degraded",
                    latency_ms=elapsed_ms,
                    details={
                        "status_code": resp.status_code,
                        "rate_limiter": stats,
                        "cloud_health": self.cloud_health.to_dict(),
                        "budget": self.budget.to_dict(),
                    },
                )
        except Exception as exc:
            self.cloud_health.record_failure(exc)
            return ProviderHealth(
                status="error",
                latency_ms=0.0,
                details={
                    "error": str(exc),
                    "rate_limiter": self.rate_limiter.get_stats(),
                    "cloud_health": self.cloud_health.to_dict(),
                    "budget": self.budget.to_dict(),
                },
            )
