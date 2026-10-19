"""Base definitions, protocol, and data structures for model providers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from noxai.taint import Content, TaintFlag

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ProviderError(Exception):
    """Base exception for model provider errors."""


class ProviderUnavailable(ProviderError):
    """Provider is unreachable, offline, or failed after maximum retries."""


class ModelNotAvailable(ProviderError):
    """Requested model is not pulled or does not exist."""


ModelNotFound = ModelNotAvailable


class RateLimitError(ProviderError):
    """Provider rate limit reached and could not be satisfied."""


class PrivacyViolation(ProviderError):
    """Raised when private data is routed to a non-local/cloud provider."""


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """Normalized tool call representation."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    taint: TaintFlag = TaintFlag.NONE


@dataclass
class ToolSpec:
    """Tool specification exposed to model."""

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class Message:
    """Unified message structure across providers."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: Content | str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    def get_text(self) -> str:
        """Return the raw string text content."""
        if isinstance(self.content, Content):
            return self.content.text
        return str(self.content)

    def get_model_text(self) -> str:
        """Return content formatted for the model (with untrusted wrapping if applicable)."""
        if isinstance(self.content, Content):
            return self.content.as_model_text()
        return str(self.content)

    def is_private(self) -> bool:
        """Check if message content or its tool calls/arguments carry PRIVATE taint."""
        if isinstance(self.content, Content) and self.content.is_private():
            return True
        for tc in self.tool_calls:
            if getattr(tc, "taint", TaintFlag.NONE) & TaintFlag.PRIVATE:
                return True
            if isinstance(tc.arguments, dict):
                for val in tc.arguments.values():
                    if isinstance(val, Content) and val.is_private():
                        return True
        return False

    def is_untrusted(self) -> bool:
        """Check if message content carries UNTRUSTED taint."""
        if isinstance(self.content, Content):
            return self.content.is_untrusted()
        return False

    def get_taint(self) -> TaintFlag:
        """Get taint flags for this message."""
        if isinstance(self.content, Content):
            return self.content.taint
        return TaintFlag.NONE


@dataclass
class ChatChunk:
    """Streamed chunk of model response."""

    delta_text: str = ""
    delta_tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, int] | None = None
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.text and not self.delta_text:
            self.delta_text = self.text
        elif self.delta_text and not self.text:
            self.text = self.delta_text

        if self.tool_calls and not self.delta_tool_calls:
            self.delta_tool_calls = self.tool_calls
        elif self.delta_tool_calls and not self.tool_calls:
            self.tool_calls = self.delta_tool_calls


@dataclass
class ProviderHealth:
    """Health check response for a provider."""

    status: str  # "ok" | "degraded" | "error"
    latency_ms: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Provider(Protocol):
    """Protocol implemented by all model providers."""

    name: str
    is_local: bool

    def chat(
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
        """Execute a chat completion, optionally streaming chunks."""
        ...

    async def embed(
        self,
        *,
        model: str,
        texts: list[str],
        keep_alive: str | None = None,
    ) -> list[list[float]]:
        """Generate vector embeddings for the provided texts."""
        ...

    async def health(self) -> ProviderHealth:
        """Check health status and connectivity of the provider."""
        ...
