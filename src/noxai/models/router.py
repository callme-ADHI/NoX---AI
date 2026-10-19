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
from noxai.security.egress_filter import EgressFilter, record_egress_decision

log = logging.getLogger(__name__)


class ModelRouter:
    """Resolves model roles, enforces privacy routing, and handles automatic fallback."""

    def __init__(
        self,
        config: Settings,
        providers: dict[str, Provider] | None = None,
    ) -> None:
        self.config = config
        self.egress_filter = EgressFilter(config=self.config)

        if providers is not None:
            self.providers = providers
        else:
            from noxai.models.cloud_health import get_cloud_health

            has_nv_key = bool(
                config.nvidia_api_key and config.nvidia_api_key.get_secret_value()
            )
            ch = get_cloud_health(has_key=has_nv_key)
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
                    cloud_health=ch,
                ),
            }

    def resolve_role(self, role: str) -> tuple[Provider, str]:
        """Resolve a logical role to (Provider, model_name)."""
        if role not in self.config.models.roles:
            # Fallback for resident role: if not explicitly configured, fall back to brain
            if role == "resident" and "brain" in self.config.models.roles:
                return self.resolve_role("brain")
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
        cloud_health = getattr(provider, "cloud_health", None)
        if cloud_health is not None and not cloud_health.is_active():
            return False
        return True

    def check_privacy(self, role: str, provider: Provider, messages: list[Message]) -> None:
        """Enforce that PRIVATE content never routes to a non-local/cloud provider.

        Raises:
            PrivacyViolation: If any message in the request carries PRIVATE taint,
                              SECRET/LOCAL_ONLY data class, or violates egress policy.
        """
        if provider.is_local:
            return  # Local processing is always permitted

        for msg in messages:
            if msg.is_private():
                raise PrivacyViolation(
                    f"Privacy violation: Attempted to route content with PRIVATE taint "
                    f"to non-local provider '{provider.name}' for role '{role}'."
                )

        decision = self.egress_filter.evaluate(
            messages,
            provider_name=provider.name,
            model="",
            is_local=False,
        )
        if not decision.allowed:
            raise PrivacyViolation(
                f"Privacy violation: {decision.reason} for role '{role}'."
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
        num_ctx: int | None = None,
        keep_alive: str | None = None,
        on_notice: Callable[[str], None] | None = None,
        on_model_used: Callable[[dict[str, Any]], None] | None = None,
    ) -> AsyncIterator[ChatChunk]:
        """Execute chat completion for the specified role with privacy check and cloud fallback."""
        provider, model = self.resolve_role(role)

        # Resident model tuning & tool constraints (Phase F6.2, F6.3)
        if role == "resident":
            if tools and not getattr(
                self.config.models.ollama, "resident_allow_tool_loops", False
            ):
                raise ValueError(
                    "Resident model is reserved for fast non-tool operations and cannot "
                    "execute tool loops without resident_allow_tool_loops=True."
                )
            if keep_alive is None:
                keep_alive = "-1"
            if num_ctx is None:
                num_ctx = getattr(self.config.models.ollama, "resident_num_ctx", 4096)

        # 1. Local execution path
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
                num_ctx=num_ctx,
                keep_alive=keep_alive,
            ):
                yield chunk
            return

        # 2. Cloud execution path — evaluate through egress filter
        egress_decision = self.egress_filter.evaluate(
            messages,
            provider_name=provider.name,
            model=model,
            is_local=False,
        )

        try:
            await record_egress_decision(
                decision=egress_decision,
                provider=provider.name,
                model=model,
            )
        except Exception as exc:
            log.debug("record_egress_decision note: %s", exc)

        if not egress_decision.allowed:
            if egress_decision.decision == "rerouted_local":
                notice = f"[Privacy notice] {egress_decision.reason} — routing to local model."
                if on_notice:
                    on_notice(notice)
                log.info(notice)
                local_provider, local_model = self.resolve_role("brain")
                if on_model_used:
                    on_model_used(
                        {
                            "role": role,
                            "provider": local_provider.name,
                            "model": local_model,
                            "is_local": True,
                            "rerouted_reason": egress_decision.reason,
                        }
                    )
                async for chunk in local_provider.chat(
                    model=local_model,
                    messages=messages,
                    tools=tools,
                    stream=stream,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    num_ctx=num_ctx,
                    keep_alive=keep_alive,
                ):
                    yield chunk
                return
            else:
                raise PrivacyViolation(f"Privacy violation: {egress_decision.reason}")

        cloud_messages = (
            egress_decision.sanitized_messages
            if egress_decision.sanitized_messages is not None
            else messages
        )

        # 3. Check proactive cloud health status
        cloud_health = getattr(provider, "cloud_health", None)
        if cloud_health is not None and not cloud_health.is_active():
            notice = (
                f"[Cloud notice] Cloud provider unavailable ({cloud_health.reason}) — "
                "routing to local model."
            )
            if on_notice:
                on_notice(notice)
            log.info(notice)
            local_provider, local_model = self.resolve_role("brain")
            if on_model_used:
                on_model_used(
                    {
                        "role": role,
                        "provider": local_provider.name,
                        "model": local_model,
                        "is_local": True,
                        "fallback_from": role,
                        "fallback_reason": cloud_health.reason,
                    }
                )
            async for chunk in local_provider.chat(
                model=local_model,
                messages=messages,
                tools=tools,
                stream=stream,
                temperature=temperature,
                max_tokens=max_tokens,
                num_ctx=num_ctx,
                keep_alive=keep_alive,
            ):
                yield chunk
            return

        # 4. Cloud execution path with automatic fallback
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
                messages=cloud_messages,
                tools=tools,
                stream=stream,
                temperature=temperature,
                max_tokens=max_tokens,
                num_ctx=num_ctx,
                keep_alive=keep_alive,
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
                num_ctx=num_ctx,
                keep_alive=keep_alive,
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


_DEFAULT_ROUTER: ModelRouter | None = None


def set_default_router(router: ModelRouter | None) -> None:
    """Set global router override (useful for testing with FakeProvider)."""
    global _DEFAULT_ROUTER
    _DEFAULT_ROUTER = router


def get_default_router(config: Settings | None = None) -> ModelRouter:
    """Return a ModelRouter instance with the provided or default settings."""
    if _DEFAULT_ROUTER is not None:
        return _DEFAULT_ROUTER
    cfg = config or get_config()
    return ModelRouter(config=cfg)
