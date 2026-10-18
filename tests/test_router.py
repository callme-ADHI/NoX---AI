"""Tests for ModelRouter: role resolution, privacy enforcement, and cloud fallback."""

from __future__ import annotations

import pytest

from noxai.config import ModelRoleConfig, ModelsConfig, Settings
from noxai.models.base import Message, PrivacyViolation
from noxai.models.fake import FakeProvider, PrivacyAssertionError
from noxai.models.router import ModelRouter
from noxai.taint import Content


def make_test_router(
    local_provider: FakeProvider | None = None,
    cloud_provider: FakeProvider | None = None,
) -> tuple[ModelRouter, FakeProvider, FakeProvider]:
    local = local_provider or FakeProvider(name="ollama", is_local=True)
    cloud = cloud_provider or FakeProvider(name="nvidia", is_local=False, fail_on_private=True)

    cfg = Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="ollama", model="qwen2.5:14b"),
                "embed": ModelRoleConfig(provider="ollama", model="nomic-embed-text"),
                "heavy": ModelRoleConfig(provider="nvidia", model="llama-3.3-nemotron"),
            }
        )
    )
    router = ModelRouter(config=cfg, providers={"ollama": local, "nvidia": cloud})
    return router, local, cloud


class TestRouterPrivacy:
    async def test_private_message_to_cloud_raises_privacy_violation(self) -> None:
        """Routing private content to the heavy (cloud) role must raise PrivacyViolation."""
        router, _local, cloud = make_test_router()

        private_msg = Message(
            role="user",
            content=Content.private("This is confidential user chat history"),
        )

        with pytest.raises(PrivacyViolation, match="Privacy violation"):
            async for _ in router.chat(role="heavy", messages=[private_msg]):
                pass

        # Verify that cloud provider was never called
        assert len(cloud.calls) == 0

    async def test_fake_cloud_provider_sentinel_raises_if_passed_private(self) -> None:
        """If a cloud provider ever receives private content, its sentinel assertion fires."""
        cloud = FakeProvider(name="nvidia", is_local=False, fail_on_private=True)
        private_msg = Message(
            role="user",
            content=Content.private("Secret"),
        )
        with pytest.raises(PrivacyAssertionError):
            async for _ in cloud.chat(model="llama", messages=[private_msg]):
                pass

    async def test_public_message_reaches_cloud(self) -> None:
        """Clean public messages route to the cloud provider without error."""
        router, _local, cloud = make_test_router()
        cloud.queue_response("Cloud research synthesis")

        public_msg = Message(
            role="user",
            content=Content.public("Latest news in cybersecurity"),
        )

        chunks = []
        async for chunk in router.chat(role="heavy", messages=[public_msg]):
            chunks.append(chunk.delta_text)

        assert "".join(chunks) == "Cloud research synthesis"
        assert len(cloud.calls) == 1
        assert cloud.calls[0]["model"] == "llama-3.3-nemotron"

    async def test_untrusted_public_message_reaches_cloud(self) -> None:
        """Web/RSS content is UNTRUSTED (not PRIVATE) and is eligible for cloud reasoning."""
        router, _local, cloud = make_test_router()
        cloud.queue_response("Summary of web article")

        untrusted_web_msg = Message(
            role="user",
            content=Content.untrusted("Article text fetched from web", source="https://news.com"),
        )

        chunks = []
        async for chunk in router.chat(role="heavy", messages=[untrusted_web_msg]):
            chunks.append(chunk.delta_text)

        assert "".join(chunks) == "Summary of web article"
        assert len(cloud.calls) == 1


class TestRouterFallback:
    async def test_cloud_failure_falls_back_to_local_brain(self) -> None:
        """When cloud fails, automatic fallback to local brain occurs with notice."""
        router, local, cloud = make_test_router()

        # Cloud raises an exception
        cloud.queue_response(RuntimeError("Cloud API 503 Service Unavailable"))
        # Local brain succeeds
        local.queue_response("Local fallback answer")

        public_msg = Message(
            role="user",
            content=Content.public("Research topic"),
        )

        notices: list[str] = []
        models_used: list[dict] = []

        chunks = []
        async for chunk in router.chat(
            role="heavy",
            messages=[public_msg],
            on_notice=lambda n: notices.append(n),
            on_model_used=lambda m: models_used.append(m),
        ):
            chunks.append(chunk.delta_text)

        assert "".join(chunks) == "Local fallback answer"
        assert len(notices) == 1
        assert "Falling back to local" in notices[0]

        # Verify model used reporting recorded fallback
        assert any(m.get("fallback_from") == "heavy" for m in models_used)


class TestRouterTransparency:
    async def test_model_used_reporting_for_local_call(self) -> None:
        """Model used callback receives role and is_local=True for brain role."""
        router, local, _cloud = make_test_router()
        local.queue_response("Local brain response")

        models_used = []
        async for _ in router.chat(
            role="brain",
            messages=[Message(role="user", content="hello")],
            on_model_used=lambda m: models_used.append(m),
        ):
            pass

        assert len(models_used) == 1
        assert models_used[0]["role"] == "brain"
        assert models_used[0]["is_local"] is True
        assert models_used[0]["model"] == "qwen2.5:14b"


class TestRouterEdgeCases:
    def test_default_provider_initialization(self) -> None:
        """Router initializes default providers when none are passed."""
        cfg = Settings(
            models=ModelsConfig(
                roles={
                    "brain": ModelRoleConfig(provider="ollama", model="qwen2.5:14b"),
                    "embed": ModelRoleConfig(provider="ollama", model="nomic-embed-text"),
                }
            )
        )
        router = ModelRouter(config=cfg)
        assert "ollama" in router.providers
        assert "nvidia" in router.providers

    def test_unconfigured_role_raises_key_error(self) -> None:
        """Attempting to resolve an unknown role raises KeyError."""
        router, _local, _cloud = make_test_router()
        with pytest.raises(KeyError, match="not configured"):
            router.resolve_role("nonexistent_role")

    def test_unregistered_provider_raises_provider_unavailable(self) -> None:
        """If a role points to a provider name not in providers dict, raises ProviderUnavailable."""
        from noxai.models.base import ProviderUnavailable

        cfg = Settings(
            models=ModelsConfig(
                roles={
                    "brain": ModelRoleConfig(provider="unknown_prov", model="model_x"),
                    "embed": ModelRoleConfig(provider="ollama", model="nomic-embed-text"),
                }
            )
        )
        router = ModelRouter(config=cfg, providers={"ollama": FakeProvider()})
        with pytest.raises(ProviderUnavailable, match="not registered"):
            router.resolve_role("brain")

    async def test_embed_routing(self) -> None:
        """Embedding requests are forwarded to the configured embed provider."""
        router, local, _cloud = make_test_router()
        local.queue_embed([[0.5, 0.5, 0.5]])

        vectors = await router.embed(texts=["test embedding text"])
        assert len(vectors) == 1
        assert vectors[0] == [0.5, 0.5, 0.5]
        assert len(local.calls) == 1
        assert local.calls[0]["action"] == "embed"

    def test_is_cloud_available_scenarios(self) -> None:
        """Test is_cloud_available with various configurations."""
        router, _local, cloud = make_test_router()
        assert router.is_cloud_available() is True

        # When heavy role is missing
        cfg_no_heavy = Settings(
            models=ModelsConfig(
                roles={
                    "brain": ModelRoleConfig(provider="ollama", model="qwen"),
                    "embed": ModelRoleConfig(provider="ollama", model="embed"),
                }
            )
        )
        r_no_heavy = ModelRouter(config=cfg_no_heavy, providers={"ollama": FakeProvider()})
        assert r_no_heavy.is_cloud_available() is False

        # When heavy role provider is not in providers
        cfg_missing_prov = Settings(
            models=ModelsConfig(
                roles={
                    "brain": ModelRoleConfig(provider="ollama", model="qwen"),
                    "heavy": ModelRoleConfig(provider="nvidia", model="nemotron"),
                    "embed": ModelRoleConfig(provider="ollama", model="embed"),
                }
            )
        )
        r_missing_prov = ModelRouter(config=cfg_missing_prov, providers={"ollama": FakeProvider()})
        assert r_missing_prov.is_cloud_available() is False

        # When cloud provider has empty api_key attribute
        cloud.api_key = ""
        assert router.is_cloud_available() is False

    def test_get_default_router_helper(self) -> None:
        """Test get_default_router returns a ModelRouter."""
        from noxai.models.router import get_default_router

        router = get_default_router()
        assert isinstance(router, ModelRouter)
