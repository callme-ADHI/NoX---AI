"""Unit tests for typed engine health, reason codes, fallback policies, and model validation."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from noxai.models.base import ChatChunk, ProviderUnavailable, RateLimitError
from noxai.models.engine import (
    EngineHealth,
    NvidiaEngine,
    OllamaEngine,
    evaluate_fallback_policy,
    validate_configured_models,
)
from noxai.models.keypool import AllocationMode, KeyPool, KeyState


@pytest.fixture
def mock_config():
    cfg = MagicMock()
    cfg.models.nvidia.base_url = "https://integrate.api.nvidia.com/v1"
    cfg.models.nvidia.enabled = True
    cfg.models.ollama.base_url = "http://127.0.0.1:11434"
    cfg.models.fallback_policy = "ask"
    cfg.models.roles = {
        "front": MagicMock(provider="nvidia", model="nvidia/nemotron-3.5-lightning-30b-a3b"),
        "resident": MagicMock(provider="ollama", model="qwen2.5:14b"),
        "brain": MagicMock(provider="ollama", model="qwen2.5:14b"),
        "embed": MagicMock(provider="ollama", model="nomic-embed-text"),
    }
    cfg.keypool.rpm_per_key = 30
    cfg.keypool.concurrency_per_key = 2
    cfg.keypool.reserve_interactive = True
    cfg.keypool.wide_mode_enabled = True
    cfg.nvidia_api_keys = []
    cfg.nvidia_api_key = None
    return cfg


# ---------------------------------------------------------------------------
# 1. Reason-code mappings
# ---------------------------------------------------------------------------


def test_nvidia_engine_classify_error(mock_config):
    eng = NvidiaEngine(config=mock_config)

    # 401 / 403 -> auth
    status, reason, msg = eng._classify_error(Exception("Unauthorized"), status_code=401)
    assert status == "down"
    assert reason == "auth"

    status, reason, msg = eng._classify_error(Exception("Forbidden"), status_code=403)
    assert status == "down"
    assert reason == "auth"

    # 404 / 410 -> model_gone
    status, reason, msg = eng._classify_error(Exception("Not Found"), status_code=404)
    assert status == "down"
    assert reason == "model_gone"

    status, reason, msg = eng._classify_error(Exception("Gone"), status_code=410)
    assert status == "down"
    assert reason == "model_gone"

    # 429 -> rate_limited
    status, reason, msg = eng._classify_error(RateLimitError("Rate limit exceeded"), status_code=429)
    assert status == "degraded"
    assert reason == "rate_limited"

    # ConnectError -> network
    status, reason, msg = eng._classify_error(httpx.ConnectError("Failed to connect to host"))
    assert status == "down"
    assert reason == "network"

    # Timeout -> timeout
    status, reason, msg = eng._classify_error(httpx.ReadTimeout("Read timed out"))
    assert status == "down"
    assert reason == "timeout"

    # Unknown -> unknown
    status, reason, msg = eng._classify_error(RuntimeError("Weird crash"))
    assert status == "down"
    assert reason == "unknown"


def test_ollama_engine_classify_error(mock_config):
    eng = OllamaEngine(config=mock_config)

    # ConnectError -> network
    status, reason, msg = eng._classify_error(httpx.ConnectError("Connection refused"))
    assert status == "down"
    assert reason == "network"

    # 404 -> model_gone
    status, reason, msg = eng._classify_error(Exception("Not found"), status_code=404)
    assert status == "down"
    assert reason == "model_gone"

    # Timeout -> timeout
    status, reason, msg = eng._classify_error(asyncio.TimeoutError())
    assert status == "down"
    assert reason == "timeout"


# ---------------------------------------------------------------------------
# 2. Fallback Policy Evaluation
# ---------------------------------------------------------------------------


def test_fallback_policy_ask():
    decision = evaluate_fallback_policy(
        policy="ask",
        reason="model_gone",
        human_msg="Model decommissioned",
    )
    assert decision.action == "ask_user"
    assert decision.reason == "model_gone"
    assert decision.served_by == "system"
    assert "Would you like to use the local model instead?" in decision.message


def test_fallback_policy_auto_notify():
    decision = evaluate_fallback_policy(
        policy="auto_notify",
        reason="network",
        human_msg="Connection refused",
    )
    assert decision.action == "execute_local"
    assert decision.reason == "network"
    assert decision.served_by == "ollama"
    assert "falling back to local model" in decision.message


def test_fallback_policy_never():
    decision = evaluate_fallback_policy(
        policy="never",
        reason="rate_limited",
        human_msg="Rate limit exceeded",
    )
    assert decision.action == "abort"
    assert decision.reason == "rate_limited"
    assert decision.served_by == "none"
    assert "Fallback policy is 'never'" in decision.message


# ---------------------------------------------------------------------------
# 3. Model validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_configured_models_present(mock_config):
    eng = NvidiaEngine(config=mock_config)
    eng.health = AsyncMock(
        return_value=EngineHealth(
            status="ok",
            reason=None,
            message="healthy",
            checked_at="2026-11-16T12:00:00Z",
            latency_ms=150.0,
        )
    )
    eng.list_models = AsyncMock(
        return_value=["nvidia/nemotron-3.5-lightning-30b-a3b", "meta/llama-3.1-8b-instruct"]
    )

    valid, missing, health = await validate_configured_models(
        eng, ["nvidia/nemotron-3.5-lightning-30b-a3b"]
    )
    assert valid is True
    assert missing is None
    assert health.status == "ok"


@pytest.mark.asyncio
async def test_validate_configured_models_missing(mock_config):
    eng = NvidiaEngine(config=mock_config)
    eng.health = AsyncMock(
        return_value=EngineHealth(
            status="ok",
            reason=None,
            message="healthy",
            checked_at="2026-11-16T12:00:00Z",
            latency_ms=150.0,
        )
    )
    eng.list_models = AsyncMock(return_value=["meta/llama-3.1-8b-instruct"])

    valid, missing, health = await validate_configured_models(
        eng, ["nvidia/nonexistent-model-decommissioned"]
    )
    assert valid is False
    assert missing == "nvidia/nonexistent-model-decommissioned"
    assert health.status == "down"
    assert health.reason == "model_gone"
    assert eng.last_health.reason == "model_gone"


# ---------------------------------------------------------------------------
# 4. KeyPool leasing non-interference
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keypool_health_check_leasing(mock_config):
    # Setup keypool with 2 keys, one pinned to a background job
    kp = KeyPool(mock_config, keys=["test-key-alpha", "test-key-beta"])
    assert kp.total_keys == 2

    # Pin k1 to job_1
    kp._pinned_assignments["job_1"] = "k1"
    kp._active_jobs["job_1"] = 1

    # Health check uses job_id=None, priority=0
    async with kp.lease(job_id=None, priority=0) as lease:
        # Must lease k2, not k1 (which is pinned to job_1)
        assert lease.key_id == "k2"
        assert lease.mode == AllocationMode.INTERACTIVE

    assert kp._pinned_assignments["job_1"] == "k1"
