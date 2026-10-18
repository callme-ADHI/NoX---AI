"""Model router enforcing role resolution, taint-based privacy rules, and cloud fallback."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

from noxai.config import Settings, get_config
from noxai.models.base import (
    ChatChunk,
    Message,
    PrivacyViolation,
    Provider,
    ProviderError,
    ProviderUnavailable,
    RateLimitError,
    ToolSpec,
)
from noxai.models.nvidia import NvidiaProvider
from noxai.models.ollama import OllamaProvider

log = logging.getLogger(__name__)


class ModelRouter:
    """Resolves model roles, enforces privacy routing, and handles automatic fallback."""

    def __init__(
        self,
        config: Settings,
        providers: dict[str, Provider] | None = None,
    ) -> None:
        self.config = config

        if providers is not None:
            self.providers = providers
        else:
            self.providers = {
                "ollama": OllamaProvider(
                    base_url=config.models.ollama.base_url,
                    keep_alive=config.models.ollama.keep_alive,
                    num_ctx=config.models.ollama.num_ctx,
                ),
                "nvidia": NvidiaProvider(
                    api_key=config.nvidia_api_key.get_secret_value()
                    if config.nvidia_api_key
                    else None,
                    base_url=config.models.nvidia.base_url,
                    requests_per_minute=config.models.nvidia.requests_per_minute,
                    max_retries=config.models.nvidia.max_retries,
                ),
            }

    def resolve_role(self, role: str) -> tuple[Provider, str]:
        """Resolve a logical role to (Provider, model_name)."""
        if role not in self.config.models.roles:
            raise KeyError(
                f"Role '{role}' is not configured in models.roles. "
                f"Configured roles: {list(self.config.models.roles.keys())}"
            )

        role_cfg = self.config.models.roles[role]
        provider_name = role_cfg.provider
        model_name = role_cfg.model

        if provider_name not in self.providers:
            raise ProviderUnavailable(
                f"Provider '{provider_name}' configured for role '{role}' is not registered."
            )

        return self.providers[provider_name], model_name

    def is_cloud_available(self) -> bool:
        """Check if cloud execution is configured and available."""
        if "heavy" not in self.config.models.roles:
            return False
        role_cfg = self.config.models.roles["heavy"]
        provider_name = role_cfg.provider
        if provider_name not in self.providers:
            return False
        provider = self.providers[provider_name]
        if provider.is_local:
            return True
        api_key = getattr(provider, "api_key", None)
        if api_key is not None and not api_key:
            return False
        return True

    def check_privacy(self, role: str, provider: Provider, messages: list[Message]) -> None:
        """Enforce that PRIVATE content never routes to a non-local/cloud provider.

        Raises:
            PrivacyViolation: If any message in the request carries PRIVATE taint
                              and the target provider is non-local.
        """
        if provider.is_local:
            return  # Local processing is always permitted

        for msg in messages:
            if msg.is_private():
                raise PrivacyViolation(
                    f"Privacy violation: Attempted to route content with PRIVATE taint "
                    f"to non-local provider '{provider.name}' for role '{role}'."
                )

    async def chat(
        self,
        *,
        role: str,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        stream: bool = True,
        temperature: float | None = None,
        max_tokens: int | None = None,
        on_notice: Callable[[str], None] | None = None,
        on_model_used: Callable[[dict[str, Any]], None] | None = None,
    ) -> AsyncIterator[ChatChunk]:
        """Execute chat completion for the specified role with privacy check and cloud fallback."""
        provider, model = self.resolve_role(role)

        # 1. Enforce privacy rule
        self.check_privacy(role, provider, messages)

        # 2. Local execution path
        if provider.is_local:
            if on_model_used:
                on_model_used(
                    {
                        "role": role,
                        "provider": provider.name,
                        "model": model,
                        "is_local": True,
                    }
                )
            async for chunk in provider.chat(
                model=model,
                messages=messages,
                tools=tools,
                stream=stream,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                yield chunk
            return

        # 3. Cloud execution path with automatic fallback
        try:
            if on_model_used:
                on_model_used(
                    {
                        "role": role,
                        "provider": provider.name,
                        "model": model,
                        "is_local": False,
                    }
                )

            async for chunk in provider.chat(
                model=model,
                messages=messages,
                tools=tools,
                stream=stream,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                yield chunk

        except (ProviderUnavailable, RateLimitError, ProviderError, Exception) as exc:
            # Fall back to local brain role
            log.warning("Cloud role '%s' failed (%s). Falling back to local brain role.", role, exc)
            fallback_msg = f"Cloud role '{role}' failed ({exc}). Falling back to local model."
            if on_notice:
                on_notice(fallback_msg)

            fallback_provider, fallback_model = self.resolve_role("brain")
            if on_model_used:
                on_model_used(
                    {
                        "role": "brain",
                        "provider": fallback_provider.name,
                        "model": fallback_model,
                        "is_local": True,
                        "fallback_from": role,
                    }
                )

            async for chunk in fallback_provider.chat(
                model=fallback_model,
                messages=messages,
                tools=tools,
                stream=stream,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                yield chunk

    async def embed(
        self,
        *,
        texts: list[str],
        role: str = "embed",
    ) -> list[list[float]]:
        """Generate embeddings using the configured embedding role."""
        provider, model = self.resolve_role(role)
        return await provider.embed(model=model, texts=texts)


def get_default_router(config: Settings | None = None) -> ModelRouter:
    """Return a ModelRouter instance with the provided or default settings."""
    cfg = config or get_config()
    return ModelRouter(config=cfg)

