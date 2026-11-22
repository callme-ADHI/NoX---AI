# Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
# commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
# Modified for NoX: Typed EngineHealth with specific reason codes, dual concrete engines (NvidiaEngine, OllamaEngine), and KeyPool integration.

"""Unified Engine abstraction, typed health monitoring, model validation, and fallback policy."""

from __future__ import annotations

import asyncio
import logging
import shutil
import socket
import subprocess
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import httpx

from noxai.config import Settings, get_config
from noxai.models.base import ChatChunk, Message, ProviderUnavailable, RateLimitError, ToolSpec

log = logging.getLogger(__name__)

EngineStatus = Literal["ok", "degraded", "down"]
EngineReason = Literal["auth", "model_gone", "rate_limited", "network", "timeout", "unknown"]


@dataclass
class EngineHealth:
    """Typed health check outcome for an inference engine."""

    status: EngineStatus
    reason: EngineReason | None
    message: str
    checked_at: str  # ISO timestamp
    latency_ms: float = 0.0
    models: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "message": self.message,
            "checked_at": self.checked_at,
            "latency_ms": round(self.latency_ms, 2),
            "models_count": len(self.models),
        }


# ---------------------------------------------------------------------------
# Notification state tracking (rate-limited to 1 per engine per hour)
# ---------------------------------------------------------------------------

_engine_previous_status: dict[str, EngineStatus] = {}
_engine_last_alert_ts: dict[str, float] = {}


def send_engine_desktop_notification(title: str, body: str) -> None:
    """Send a desktop notification via notify-send if available."""
    binary = shutil.which("notify-send")
    if not binary:
        return
    try:
        subprocess.run(
            [binary, "-a", "NoX", title[:120], body[:300]],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
        )
    except Exception as exc:
        log.debug("Engine desktop notification failed: %s", exc)


def check_and_notify_status_change(engine_name: str, health: EngineHealth) -> None:
    """Trigger desktop alert when engine transitions from ok to down/degraded, or recovers."""
    now = time.monotonic()
    prev = _engine_previous_status.get(engine_name, "ok")
    curr = health.status

    if prev == "ok" and curr in ("down", "degraded"):
        last_alert = _engine_last_alert_ts.get(engine_name, 0.0)
        if now - last_alert >= 3600.0:  # 1 hour throttle
            _engine_last_alert_ts[engine_name] = now
            _engine_previous_status[engine_name] = curr
            title = f"NoX Engine Alert: {engine_name.capitalize()} {curr.upper()}"
            body = f"{health.message} (Reason: {health.reason or 'unknown'})"
            send_engine_desktop_notification(title, body)
            log.warning("Engine %s transitioned to %s: %s", engine_name, curr, health.message)
    elif prev in ("down", "degraded") and curr == "ok":
        _engine_previous_status[engine_name] = "ok"
        _engine_last_alert_ts[engine_name] = now
        title = f"NoX Engine Recovered: {engine_name.capitalize()}"
        body = f"Engine {engine_name} is back online and operational ({health.latency_ms:.0f}ms)."
        send_engine_desktop_notification(title, body)
        log.info("Engine %s recovered to ok.", engine_name)
    else:
        _engine_previous_status[engine_name] = curr


# ---------------------------------------------------------------------------
# Engine Base Class
# ---------------------------------------------------------------------------


class Engine(ABC):
    """Abstract base class for inference engines in NoX."""

    engine_id: str
    is_local: bool
    last_health: EngineHealth | None = None

    @abstractmethod
    async def health(self) -> EngineHealth:
        """Perform a lightweight health check and return typed EngineHealth."""

    @abstractmethod
    async def list_models(self) -> list[str]:
        """Return available model IDs supported or served by this engine."""

    @abstractmethod
    async def generate(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Synchronous/non-streaming completion."""

    @abstractmethod
    def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatChunk]:
        """Streaming completion yielding ChatChunk tokens."""


# ---------------------------------------------------------------------------
# Concrete NvidiaEngine
# ---------------------------------------------------------------------------


class NvidiaEngine(Engine):
    """NVIDIA cloud API engine using OpenAI-compatible endpoints and KeyPool leases."""

    engine_id = "nvidia"
    is_local = False

    def __init__(
        self,
        config: Settings | None = None,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config or get_config()
        self.base_url = (base_url or self.config.models.nvidia.base_url).rstrip("/")
        self.transport = transport
        from noxai.models.keypool import KeyPool, get_default_keypool

        self.key_pool: KeyPool = get_default_keypool()

    def _classify_error(self, exc: Exception, status_code: int | None = None) -> tuple[EngineStatus, EngineReason, str]:
        """Map exception or HTTP status code to typed status, reason code, and human sentence."""
        err_msg = str(exc)

        # Check explicit status code or status code in string
        code = status_code
        if code is None:
            if hasattr(exc, "status_code"):
                code = getattr(exc, "status_code")
            elif "401" in err_msg:
                code = 401
            elif "403" in err_msg:
                code = 403
            elif "404" in err_msg:
                code = 404
            elif "410" in err_msg:
                code = 410
            elif "429" in err_msg:
                code = 429

        if code in (401, 403):
            return "down", "auth", "NVIDIA API key unauthorized, invalid, or expired."
        if code in (404, 410):
            return "down", "model_gone", "Configured cloud model not found or decommissioned (HTTP 404/410)."
        if code == 429 or isinstance(exc, RateLimitError):
            return "degraded", "rate_limited", "NVIDIA API rate limit exceeded (HTTP 429)."
        if isinstance(exc, (httpx.ConnectError, httpx.NetworkError, socket.gaierror)) or any(
            k in err_msg.lower() for k in ("connection refused", "dns", "resolve", "network", "getaddrinfo")
        ):
            return "down", "network", f"Network connection failed: {err_msg}."
        if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)) or "timeout" in err_msg.lower():
            return "down", "timeout", "NVIDIA API request timed out."

        return "down", "unknown", f"NVIDIA API error: {err_msg}."

    async def list_models(self) -> list[str]:
        """List models available on the NVIDIA endpoint."""
        if self.key_pool.is_empty():
            return []

        # Lease an interactive key without pinning to any job
        async with self.key_pool.lease(job_id=None, priority=0) as lease:
            headers = {
                "Authorization": f"Bearer {lease.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
            kwargs: dict[str, Any] = {"timeout": 10.0}
            if self.transport:
                kwargs["transport"] = self.transport
            async with httpx.AsyncClient(**kwargs) as client:
                resp = await client.get(f"{self.base_url}/models", headers=headers)
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    return [m.get("id", "") for m in data if isinstance(m, dict) and "id" in m]
                raise httpx.HTTPStatusError(
                    f"HTTP {resp.status_code}", request=resp.request, response=resp
                )

    async def health(self) -> EngineHealth:
        """Perform a lightweight health check on the NVIDIA endpoint."""
        now_iso = datetime.now(UTC).isoformat()
        if self.key_pool.is_empty():
            h = EngineHealth(
                status="down",
                reason="auth",
                message="No NVIDIA API keys configured in secrets.env or key pool.",
                checked_at=now_iso,
                latency_ms=0.0,
            )
            self.last_health = h
            check_and_notify_status_change("nvidia", h)
            return h

        start = time.perf_counter()
        try:
            async with self.key_pool.lease(job_id=None, priority=0) as lease:
                headers = {
                    "Authorization": f"Bearer {lease.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
                kwargs: dict[str, Any] = {"timeout": 10.0}
                if self.transport:
                    kwargs["transport"] = self.transport
                async with httpx.AsyncClient(**kwargs) as client:
                    resp = await client.get(f"{self.base_url}/models", headers=headers)
                    elapsed_ms = (time.perf_counter() - start) * 1000.0

                    if resp.status_code == 200:
                        data = resp.json().get("data", [])
                        models = [m.get("id", "") for m in data if isinstance(m, dict) and "id" in m]
                        await self.key_pool.report_outcome(lease.key_id, status_code=200)
                        h = EngineHealth(
                            status="ok",
                            reason=None,
                            message="NVIDIA engine is operational and responsive.",
                            checked_at=now_iso,
                            latency_ms=elapsed_ms,
                            models=models,
                        )
                        self.last_health = h
                        check_and_notify_status_change("nvidia", h)
                        return h

                    # Handle non-200 responses
                    await self.key_pool.report_outcome(lease.key_id, status_code=resp.status_code)
                    status, reason, msg = self._classify_error(
                        httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp),
                        status_code=resp.status_code,
                    )
                    h = EngineHealth(
                        status=status,
                        reason=reason,
                        message=msg,
                        checked_at=now_iso,
                        latency_ms=elapsed_ms,
                    )
                    self.last_health = h
                    check_and_notify_status_change("nvidia", h)
                    return h

        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            status, reason, msg = self._classify_error(exc)
            h = EngineHealth(
                status=status,
                reason=reason,
                message=msg,
                checked_at=now_iso,
                latency_ms=elapsed_ms,
            )
            self.last_health = h
            check_and_notify_status_change("nvidia", h)
            return h

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Synchronous chat completion."""
        from noxai.models.nvidia import NvidiaProvider

        provider = NvidiaProvider(
            base_url=self.base_url,
            transport=self.transport,
            key_pool=self.key_pool,
        )
        chunks: list[ChatChunk] = []
        async for chunk in provider.chat(
            model=model,
            messages=list(messages),
            tools=tools,
            stream=False,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        ):
            chunks.append(chunk)

        full_text = "".join(c.delta_text or c.text for c in chunks)
        tool_calls = [tc for c in chunks for tc in (c.delta_tool_calls or c.tool_calls)]
        return {
            "content": full_text,
            "tool_calls": tool_calls,
            "usage": chunks[-1].usage if chunks else None,
        }

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatChunk]:
        """Streaming chat completion."""
        from noxai.models.nvidia import NvidiaProvider

        provider = NvidiaProvider(
            base_url=self.base_url,
            transport=self.transport,
            key_pool=self.key_pool,
        )
        async for chunk in provider.chat(
            model=model,
            messages=list(messages),
            tools=tools,
            stream=True,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        ):
            yield chunk


# ---------------------------------------------------------------------------
# Concrete OllamaEngine
# ---------------------------------------------------------------------------


class OllamaEngine(Engine):
    """Local Ollama inference engine."""

    engine_id = "ollama"
    is_local = True

    def __init__(
        self,
        config: Settings | None = None,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config or get_config()
        self.base_url = (base_url or self.config.models.ollama.base_url).rstrip("/")
        self.transport = transport

    def _classify_error(self, exc: Exception, status_code: int | None = None) -> tuple[EngineStatus, EngineReason, str]:
        err_msg = str(exc)
        code = status_code or getattr(exc, "status_code", None)

        if code in (404, 410):
            return "down", "model_gone", "Configured model not found in Ollama library."
        if isinstance(exc, (httpx.ConnectError, socket.gaierror)) or any(
            k in err_msg.lower() for k in ("connection refused", "network", "connect")
        ):
            return "down", "network", f"Cannot connect to Ollama daemon at {self.base_url}."
        if isinstance(exc, (httpx.TimeoutException, asyncio.TimeoutError)) or "timeout" in err_msg.lower():
            return "down", "timeout", "Ollama request timed out."

        return "down", "unknown", f"Ollama error: {err_msg}."

    async def list_models(self) -> list[str]:
        """List local models available in Ollama."""
        kwargs: dict[str, Any] = {"base_url": self.base_url, "timeout": 5.0}
        if self.transport:
            kwargs["transport"] = self.transport
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.get("/api/tags")
            if resp.status_code == 200:
                data = resp.json().get("models", [])
                names: list[str] = []
                for m in data:
                    name = m.get("name", "")
                    if name:
                        names.append(name)
                        if name.endswith(":latest"):
                            names.append(name[:-7])
                return sorted(set(names))
            raise httpx.HTTPStatusError("Ollama /api/tags failed", request=resp.request, response=resp)

    async def health(self) -> EngineHealth:
        """Perform a lightweight health check on Ollama."""
        now_iso = datetime.now(UTC).isoformat()
        start = time.perf_counter()
        try:
            models = await self.list_models()
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            h = EngineHealth(
                status="ok",
                reason=None,
                message=f"Ollama is healthy with {len(models)} local models.",
                checked_at=now_iso,
                latency_ms=elapsed_ms,
                models=models,
            )
            self.last_health = h
            check_and_notify_status_change("ollama", h)
            return h
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            status, reason, msg = self._classify_error(exc)
            h = EngineHealth(
                status=status,
                reason=reason,
                message=msg,
                checked_at=now_iso,
                latency_ms=elapsed_ms,
            )
            self.last_health = h
            check_and_notify_status_change("ollama", h)
            return h

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Synchronous chat completion."""
        from noxai.models.ollama import OllamaProvider

        provider = OllamaProvider(
            base_url=self.base_url,
            transport=self.transport,
        )
        chunks: list[ChatChunk] = []
        async for chunk in provider.chat(
            model=model,
            messages=list(messages),
            tools=tools,
            stream=False,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        ):
            chunks.append(chunk)

        full_text = "".join(c.delta_text or c.text for c in chunks)
        tool_calls = [tc for c in chunks for tc in (c.delta_tool_calls or c.tool_calls)]
        return {
            "content": full_text,
            "tool_calls": tool_calls,
            "usage": chunks[-1].usage if chunks else None,
        }

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatChunk]:
        """Streaming chat completion."""
        from noxai.models.ollama import OllamaProvider

        provider = OllamaProvider(
            base_url=self.base_url,
            transport=self.transport,
        )
        async for chunk in provider.chat(
            model=model,
            messages=list(messages),
            tools=tools,
            stream=True,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        ):
            yield chunk


# ---------------------------------------------------------------------------
# Global Registry & Validation
# ---------------------------------------------------------------------------

_engine_cache: dict[str, Engine] = {}


def get_engine(name: str, config: Settings | None = None) -> Engine:
    """Retrieve or create an Engine instance by name ('nvidia' or 'ollama')."""
    if name not in _engine_cache:
        cfg = config or get_config()
        if name == "nvidia":
            _engine_cache[name] = NvidiaEngine(config=cfg)
        elif name == "ollama":
            _engine_cache[name] = OllamaEngine(config=cfg)
        else:
            raise KeyError(f"Unknown engine: {name}")
    return _engine_cache[name]


async def validate_configured_models(
    engine: Engine,
    configured_models: list[str],
) -> tuple[bool, str | None, EngineHealth]:
    """Verify that each configured model appears in engine.list_models().

    If any configured model is missing, returns (False, missing_model, down_health).
    """
    health = await engine.health()
    if health.status != "ok":
        engine.last_health = health
        return False, None, health

    try:
        available = await engine.list_models()
        for cm in configured_models:
            # Check exact match or prefix tag match (e.g. qwen2.5:14b matches qwen2.5:14b:latest)
            matched = any(
                cm == am or am.startswith(f"{cm}:") or cm.startswith(f"{am}:")
                for am in available
            )
            if not matched:
                now_iso = datetime.now(UTC).isoformat()
                down_health = EngineHealth(
                    status="down",
                    reason="model_gone",
                    message=f"Configured model '{cm}' not found in {engine.engine_id} models list.",
                    checked_at=now_iso,
                    latency_ms=health.latency_ms,
                    models=available,
                )
                engine.last_health = down_health
                check_and_notify_status_change(engine.engine_id, down_health)
                return False, cm, down_health

        engine.last_health = health
        return True, None, health
    except Exception as exc:
        now_iso = datetime.now(UTC).isoformat()
        down_health = EngineHealth(
            status="down",
            reason="model_gone",
            message=f"Failed to validate models for {engine.engine_id}: {exc}",
            checked_at=now_iso,
            latency_ms=health.latency_ms,
        )
        engine.last_health = down_health
        check_and_notify_status_change(engine.engine_id, down_health)
        return False, None, down_health


# ---------------------------------------------------------------------------
# Fallback Policy Evaluation
# ---------------------------------------------------------------------------


@dataclass
class FallbackDecision:
    action: Literal["execute_local", "ask_user", "abort"]
    message: str
    reason: str
    served_by: str = "ollama"


def evaluate_fallback_policy(
    policy: str,  # "ask" | "auto_notify" | "never"
    reason: str,
    human_msg: str = "",
    configured_model: str = "",
) -> FallbackDecision:
    """Evaluate how to respond when cloud engine is down or unhealthy."""
    norm_policy = (policy or "ask").lower().strip()
    summary_reason = reason or "unknown"
    desc = f" ({human_msg})" if human_msg else ""

    if norm_policy == "never":
        return FallbackDecision(
            action="abort",
            message=f"Cloud model is unavailable [{summary_reason}]{desc}. Fallback policy is 'never'.",
            reason=summary_reason,
            served_by="none",
        )
    elif norm_policy == "auto_notify":
        return FallbackDecision(
            action="execute_local",
            message=f"Cloud model is down [{summary_reason}]{desc}; falling back to local model.",
            reason=summary_reason,
            served_by="ollama",
        )
    else:  # "ask" (default)
        return FallbackDecision(
            action="ask_user",
            message=(
                f"Cloud engine is currently unavailable [{summary_reason}]{desc}. "
                "Fallback policy is 'ask': Would you like to use the local model instead? "
                "(Pass --no-cloud to force local execution)."
            ),
            reason=summary_reason,
            served_by="system",
        )

