"""Ollama provider implementation for local model execution."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from noxai.models.base import (
    ChatChunk,
    Message,
    ModelNotAvailable,
    Provider,
    ProviderHealth,
    ProviderUnavailable,
    ToolCall,
    ToolSpec,
)

log = logging.getLogger(__name__)


class OllamaProvider(Provider):
    """Local provider interacting with Ollama HTTP API."""

    name: str = "ollama"
    is_local: bool = True

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        keep_alive: str = "30m",
        num_ctx: int = 8192,
        connect_timeout: float = 10.0,
        request_timeout: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.keep_alive = keep_alive
        self.num_ctx = num_ctx
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {
            "base_url": self.base_url,
            "timeout": httpx.Timeout(self.request_timeout, connect=self.connect_timeout),
        }
        if self.transport is not None:
            kwargs["transport"] = self.transport
        return httpx.AsyncClient(**kwargs)

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
                        "function": {
                            "name": tc.name,
                            "arguments": tc.arguments,
                        }
                    }
                    for tc in msg.tool_calls
                ]
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

    async def chat(
        self,
        *,
        model: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        stream: bool = True,
        temperature: float | None = None,
        max_tokens: int | None = None,
        num_ctx: int | None = None,
        keep_alive: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatChunk]:
        """Execute chat completion with Ollama."""
        target_keep_alive = keep_alive if keep_alive is not None else self.keep_alive
        target_ctx = num_ctx if num_ctx is not None else self.num_ctx
        payload: dict[str, Any] = {
            "model": model,
            "messages": self._format_messages(messages),
            "stream": stream,
            "keep_alive": target_keep_alive,
        }

        tools_payload = self._format_tools(tools)
        if tools_payload:
            payload["tools"] = tools_payload

        options: dict[str, Any] = {"num_ctx": target_ctx}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        payload["options"] = options

        client = self._client()
        try:
            if stream:
                async with client.stream("POST", "/api/chat", json=payload) as response:
                    if response.status_code == 404:
                        raise ModelNotAvailable(
                            f"Model '{model}' not found in Ollama. Pull it: ollama pull {model}"
                        )
                    if response.is_error:
                        text = await response.aread()
                        decoded = text.decode("utf-8", errors="replace")
                        raise ProviderUnavailable(
                            f"Ollama error (status {response.status_code}): {decoded}"
                        )

                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            chunk_data = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        chunk_msg = chunk_data.get("message", {})
                        delta_text = chunk_msg.get("content", "")
                        tool_calls: list[ToolCall] = []

                        raw_tcs = chunk_msg.get("tool_calls")
                        if raw_tcs and isinstance(raw_tcs, list):
                            for tc in raw_tcs:
                                fn = tc.get("function", {})
                                fn_name = fn.get("name", "")
                                fn_args = fn.get("arguments", {})
                                if isinstance(fn_args, str):
                                    try:
                                        fn_args = json.loads(fn_args)
                                    except Exception:
                                        fn_args = {"raw": fn_args}
                                tool_calls.append(
                                    ToolCall(
                                        id=f"call_{uuid.uuid4().hex[:8]}",
                                        name=fn_name,
                                        arguments=fn_args if isinstance(fn_args, dict) else {},
                                    )
                                )

                        finish_reason = None
                        usage = None
                        if chunk_data.get("done"):
                            finish_reason = chunk_data.get("done_reason", "stop")
                            usage = {
                                "prompt_tokens": chunk_data.get("prompt_eval_count", 0),
                                "completion_tokens": chunk_data.get("eval_count", 0),
                            }

                        yield ChatChunk(
                            delta_text=delta_text,
                            delta_tool_calls=tool_calls,
                            finish_reason=finish_reason,
                            usage=usage,
                        )
            else:
                resp = await client.post("/api/chat", json=payload)
                if resp.status_code == 404:
                    raise ModelNotAvailable(
                        f"Model '{model}' not found in Ollama. Pull it: ollama pull {model}"
                    )
                if resp.is_error:
                    raise ProviderUnavailable(
                        f"Ollama error (status {resp.status_code}): {resp.text}"
                    )

                data = resp.json()
                msg = data.get("message", {})
                delta_text = msg.get("content", "")
                tool_calls = []
                raw_tcs = msg.get("tool_calls")
                if raw_tcs and isinstance(raw_tcs, list):
                    for tc in raw_tcs:
                        fn = tc.get("function", {})
                        fn_name = fn.get("name", "")
                        fn_args = fn.get("arguments", {})
                        if isinstance(fn_args, str):
                            try:
                                fn_args = json.loads(fn_args)
                            except Exception:
                                fn_args = {"raw": fn_args}
                        tool_calls.append(
                            ToolCall(
                                id=f"call_{uuid.uuid4().hex[:8]}",
                                name=fn_name,
                                arguments=fn_args if isinstance(fn_args, dict) else {},
                            )
                        )

                yield ChatChunk(
                    delta_text=delta_text,
                    delta_tool_calls=tool_calls,
                    finish_reason=data.get("done_reason", "stop"),
                    usage={
                        "prompt_tokens": data.get("prompt_eval_count", 0),
                        "completion_tokens": data.get("eval_count", 0),
                    },
                )
        except httpx.ConnectError as exc:
            raise ProviderUnavailable(
                f"Cannot connect to Ollama at {self.base_url}. "
                "Ensure it is running: sudo systemctl start ollama"
            ) from exc
        finally:
            await client.aclose()

    async def embed(
        self,
        *,
        model: str,
        texts: list[str],
        keep_alive: str | None = None,
    ) -> list[list[float]]:
        """Generate vector embeddings for texts."""
        if not texts:
            return []

        target_keep_alive = keep_alive if keep_alive is not None else "-1"
        client = self._client()
        try:
            # Modern Ollama standard: POST /api/embed
            resp = await client.post(
                "/api/embed",
                json={"model": model, "input": texts, "keep_alive": target_keep_alive},
            )
            if resp.status_code == 200:
                data = resp.json()
                embeddings = data.get("embeddings")
                if embeddings and isinstance(embeddings, list):
                    return embeddings

            # Fallback for older Ollama: loop over /api/embeddings
            results: list[list[float]] = []
            for text in texts:
                resp_single = await client.post(
                    "/api/embeddings",
                    json={"model": model, "prompt": text, "keep_alive": target_keep_alive},
                )
                if resp_single.status_code == 404:
                    raise ModelNotAvailable(
                        f"Embedding model '{model}' not found in Ollama. "
                        f"Pull it: ollama pull {model}"
                    )
                if resp_single.is_error:
                    raise ProviderUnavailable(f"Ollama embedding error: {resp_single.text}")
                results.append(resp_single.json().get("embedding", []))
            return results
        except httpx.ConnectError as exc:
            raise ProviderUnavailable(f"Cannot connect to Ollama at {self.base_url}.") from exc
        finally:
            await client.aclose()

    async def health(self) -> ProviderHealth:
        """Query Ollama health and installed models."""
        client = self._client()
        start = time.perf_counter()
        try:
            resp = await client.get("/api/tags")
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            if resp.status_code == 200:
                data = resp.json()
                models = [m.get("name", "") for m in data.get("models", [])]
                return ProviderHealth(
                    status="ok",
                    latency_ms=elapsed_ms,
                    details={"models": models, "count": len(models)},
                )
            return ProviderHealth(
                status="degraded",
                latency_ms=elapsed_ms,
                details={"status_code": resp.status_code},
            )
        except Exception as exc:
            return ProviderHealth(
                status="error",
                latency_ms=0.0,
                details={"error": str(exc)},
            )
        finally:
            await client.aclose()

    async def wait_until_ready(self, timeout_seconds: float = 60.0) -> bool:
        """Wait for Ollama to become reachable with backoff (e.g. at daemon boot)."""
        deadline = time.time() + timeout_seconds
        backoff = 0.5
        while time.time() < deadline:
            h = await self.health()
            if h.status == "ok":
                return True
            await asyncio.sleep(min(backoff, deadline - time.time()))
            backoff = min(backoff * 1.5, 5.0)
        return False
