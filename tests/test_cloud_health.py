"""Tests for cloud health state machine, circuit breaker, rate budget, and commands (§1.5)."""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from noxai.cli import app
from noxai.config import get_config
from noxai.models.base import Message, RateLimitError
from noxai.models.budget import RequestBudget
from noxai.models.cloud_health import (
    CircuitBreaker,
    CircuitState,
    CloudHealth,
    CloudHealthState,
    get_cloud_health,
    reset_cloud_health,
)
from noxai.models.fake import FakeProvider
from noxai.models.nvidia import NvidiaProvider
from noxai.models.router import ModelRouter

# ===========================================================================
# 1. RequestBudget Tests
# ===========================================================================


class TestRequestBudget:
    @pytest.mark.asyncio
    async def test_budget_interactive_and_background_reservation(self) -> None:
        """Interactive turns can consume all tokens; background turns respect reserve."""
        # 30 RPM capacity = 30 tokens, reserved 10 for interactive
        budget = RequestBudget(requests_per_minute=30, reserved_interactive=10)
        assert budget.capacity == 30.0
        assert budget.reserved_interactive == 10.0

        # Consume until background tokens are exhausted (should consume ~20 tokens)
        consumed_bg = 0
        while await budget.acquire(is_interactive=False, timeout=0.0):
            consumed_bg += 1

        assert 19 <= consumed_bg <= 21
        assert budget.total_background == consumed_bg

        # Next background request must fail to protect the 10 reserve
        ok = await budget.acquire(is_interactive=False, timeout=0.0)
        assert ok is False
        assert budget.total_rejected >= 1

        # Interactive requests can still consume remaining tokens down to 0 (~10 tokens)
        consumed_ia = 0
        while await budget.acquire(is_interactive=True, timeout=0.0):
            consumed_ia += 1

        assert 9 <= consumed_ia <= 11
        assert budget.total_interactive == consumed_ia

        # Now tokens are depleted (~0). Next interactive request fails without wait
        ok = await budget.acquire(is_interactive=True, timeout=0.0)
        assert ok is False

    @pytest.mark.asyncio
    async def test_budget_replenish(self) -> None:
        """Tokens replenish over time according to rate."""
        budget = RequestBudget(requests_per_minute=60, reserved_interactive=5)
        budget.tokens = 0.0
        budget.last_update = time.monotonic() - 2.0  # 2 seconds ago

        # At 60 RPM (1 token/sec), 2 seconds adds 2 tokens
        assert budget.remaining_tokens(is_interactive=True) >= 2.0

        stats = budget.to_dict()
        assert stats["capacity_rpm"] == 60
        assert stats["reserved_interactive_rpm"] == 5
        assert stats["tokens_current"] >= 2.0


# ===========================================================================
# 2. CircuitBreaker Tests
# ===========================================================================


class TestCircuitBreaker:
    def test_circuit_breaker_transitions(self) -> None:
        """Circuit trips to OPEN after threshold failures and recovers via HALF_OPEN."""
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
        assert cb.state == CircuitState.CLOSED
        assert cb.can_attempt() is True

        # 2 failures do not trip
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        assert cb.consecutive_failures == 2

        # 3rd failure trips OPEN
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.can_attempt() is False
        assert cb.cooldown_remaining() > 0.0

        # Simulate cooldown elapsed
        cb.last_failure_time = time.monotonic() - 15.0
        assert cb.can_attempt() is True
        assert cb.state == CircuitState.HALF_OPEN

        # While probe is in flight, second attempt must be rejected
        assert cb.can_attempt() is False

        # Probe failure sends it back to OPEN
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # Cooldown elapses again -> probe succeeds -> CLOSED
        cb.last_failure_time = time.monotonic() - 15.0
        assert cb.can_attempt() is True
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.consecutive_failures == 0


# ===========================================================================
# 3. CloudHealth State Machine Tests
# ===========================================================================


class TestCloudHealth:
    def test_initial_states(self) -> None:
        ch_no_key = CloudHealth(has_key=False)
        assert ch_no_key.state == CloudHealthState.NO_KEY
        assert ch_no_key.circuit_breaker.state == CircuitState.OPEN
        assert ch_no_key.is_active() is False

        ch_key = CloudHealth(has_key=True)
        assert ch_key.state == CloudHealthState.ACTIVE
        assert ch_key.circuit_breaker.state == CircuitState.CLOSED
        assert ch_key.is_active() is True

    def test_auth_failure_invalid_key(self) -> None:
        ch = CloudHealth(has_key=True)
        ch.record_failure(error="Unauthorized", status_code=401)
        assert ch.state == CloudHealthState.INVALID_KEY
        assert ch.circuit_breaker.state == CircuitState.OPEN
        assert ch.is_active() is False

    def test_rate_limit_state(self) -> None:
        ch = CloudHealth(has_key=True)
        ch.record_failure(error="Too Many Requests", status_code=429, retry_after=30.0)
        assert ch.state == CloudHealthState.RATE_LIMITED
        assert ch.circuit_breaker.state == CircuitState.OPEN
        assert ch.circuit_breaker.cooldown_seconds == 30.0

    def test_network_offline_state(self) -> None:
        ch = CloudHealth(has_key=True)
        ch.record_failure(error="Connection refused (getaddrinfo failed DNS)")
        assert ch.state == CloudHealthState.OFFLINE
        assert ch.is_active() is False

    def test_timeout_and_degraded_state(self) -> None:
        ch = CloudHealth(has_key=True, ttft_threshold_seconds=5.0)
        ch.record_failure(error="Request timed out")
        assert ch.state == CloudHealthState.DEGRADED

        # High TTFT causes degraded warning
        ch.record_success(latency_seconds=8.0, ttft_seconds=6.5)
        assert ch.state == CloudHealthState.DEGRADED

        # Normal TTFT restores ACTIVE
        ch.record_success(latency_seconds=1.2, ttft_seconds=0.8)
        assert ch.state == CloudHealthState.ACTIVE
        assert ch.is_active() is True

    def test_global_singleton(self) -> None:
        reset_cloud_health(has_key=True)
        ch1 = get_cloud_health()
        ch2 = get_cloud_health()
        assert ch1 is ch2
        assert ch1.state == CloudHealthState.ACTIVE


# ===========================================================================
# 4. NvidiaProvider Integration Tests
# ===========================================================================


class TestNvidiaProviderCloudHealth:
    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks_when_open(self) -> None:
        """When circuit breaker is OPEN, provider fails fast with ProviderUnavailable."""
        health = CloudHealth(has_key=True)
        health.record_failure(error="401 Unauthorized", status_code=401)

        provider = NvidiaProvider(
            api_key="nvapi-test12345",
            cloud_health=health,
        )

        with pytest.raises(Exception) as exc:
            async for _ in provider.chat(
                model="test",
                messages=[Message(role="user", content="hello")],
            ):
                pass

        assert "circuit breaker" in str(exc.value).lower()

    @pytest.mark.asyncio
    async def test_budget_exhaustion_raises_rate_limit(self) -> None:
        """When provider budget is depleted, acquire raises RateLimitError."""
        budget = RequestBudget(requests_per_minute=2, reserved_interactive=0)
        budget.tokens = 0.0

        provider = NvidiaProvider(
            api_key="nvapi-test12345",
            budget=budget,
        )

        with pytest.raises(RateLimitError):
            async for _ in provider.chat(
                model="test",
                messages=[Message(role="user", content="hello")],
            ):
                pass


# ===========================================================================
# 5. ModelRouter Proactive Cloud Fallback Tests
# ===========================================================================


class TestModelRouterCloudFallback:
    @pytest.mark.asyncio
    async def test_router_falls_back_when_cloud_inactive(self) -> None:
        """ModelRouter detects inactive cloud health and routes to local brain with zero calls."""
        fake_cloud = FakeProvider(name="fake_cloud", is_local=False)
        fake_local = FakeProvider(name="fake_local", is_local=True)
        fake_local.queue_response("Local fallback answer")

        health = CloudHealth(has_key=True)
        health.record_failure(error="Network unreachable", status_code=None)
        assert health.is_active() is False

        # Attach health to fake_cloud
        fake_cloud.cloud_health = health  # type: ignore[attr-defined]

        cfg = get_config()
        router = ModelRouter(
            config=cfg,
            providers={"ollama": fake_local, "nvidia": fake_cloud},
        )

        from noxai.taint import Content

        notices: list[str] = []
        chunks: list[str] = []
        msg = Message(role="user", content=Content.chat("What is quantum mechanics?"))

        async for chunk in router.chat(
            role="heavy",
            messages=[msg],
            on_notice=notices.append,
        ):
            chunks.append(chunk.text)

        # 0 calls to cloud!
        assert len(fake_cloud.calls) == 0
        assert len(fake_local.calls) == 1
        assert "".join(chunks) == "Local fallback answer"
        assert any("Cloud provider unavailable" in n for n in notices)


# ===========================================================================
# 6. CLI Commands (nx cloud status, nx cloud test, nx models bench-cloud)
# ===========================================================================


class TestCloudCLICommands:
    def test_nx_cloud_status_command(self) -> None:
        runner = CliRunner()
        res = runner.invoke(app, ["cloud", "status"])
        assert res.exit_code == 0
        assert "Cloud Provider Health" in res.output or "Request Rate Budget" in res.output

    def test_nx_cloud_status_json(self) -> None:
        runner = CliRunner()
        res = runner.invoke(app, ["cloud", "status", "--json"])
        assert res.exit_code == 0
        data = json.loads(res.output)
        assert "health" in data
        assert "budget" in data
        assert "state" in data["health"]

    def test_nx_cloud_test_no_key(self) -> None:
        runner = CliRunner()
        with patch("noxai.commands.cloud.get_config") as mock_cfg:
            mock_cfg.return_value.nvidia_api_key = None
            res = runner.invoke(app, ["cloud", "test"])
            assert res.exit_code == 1
            assert "Cannot test cloud" in res.output

    def test_nx_models_bench_cloud_no_key(self) -> None:
        runner = CliRunner()
        with patch("noxai.commands.models.get_config") as mock_cfg:
            mock_cfg.return_value.nvidia_api_key = None
            res = runner.invoke(app, ["models", "bench-cloud"])
            assert res.exit_code == 1
            assert "Cannot benchmark cloud" in res.output
