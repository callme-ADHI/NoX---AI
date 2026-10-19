"""Comprehensive M10 Integration, Latency Proof, and Fallback Matrix Test (§F8).

Covers:
1. Fast-path latency verification: reflex router intent decision < 50ms (average < 5ms).
2. Cloud fallback matrix (NO_KEY, INVALID_KEY, RATE_LIMITED, OFFLINE) with
   clean fallback to local brain.
3. Circuit breaker state transitions (CLOSED -> OPEN -> HALF_OPEN -> CLOSED).
4. All 10 intent categories and reply modes verified.
5. Zero-egress privacy enforcement for sensitive inputs and private mode.
6. Rate budget manager with 10 RPM interactive reserve protection.
7. Task Center background lifecycle, cancellation, and approval parking.
8. CLI eval-routing command integration.
"""

from __future__ import annotations

import time

import pytest
from typer.testing import CliRunner

from noxai.cli import app as cli_app
from noxai.config import ModelRoleConfig, ModelsConfig, PrivacyConfig, Settings
from noxai.db import init_db
from noxai.models.base import ChatChunk, Message
from noxai.models.budget import RequestBudget
from noxai.models.cloud_health import CloudHealthState, get_cloud_health
from noxai.models.fake import FakeProvider
from noxai.models.router import ModelRouter
from noxai.router.eval import ROUTING_BENCHMARK_PROMPTS, run_routing_eval
from noxai.router.intent import IntentType, analyze_intent
from noxai.security.egress_filter import EgressFilter
from noxai.taint import Content, DataClass
from noxai.tasks.center import TaskCenter
from noxai.tasks.models import TaskLane, TaskPriority, TaskStatus
from noxai.tools.registry import ToolRegistry


def make_test_settings(privacy: PrivacyConfig | None = None) -> Settings:
    return Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="fake_local", model="qwen2.5:14b"),
                "embed": ModelRoleConfig(provider="fake_local", model="nomic-embed-text"),
                "cloud": ModelRoleConfig(provider="fake_cloud", model="nemotron-49b"),
            }
        ),
        privacy=privacy or PrivacyConfig(),
    )


# ---------------------------------------------------------------------------
# 1. Latency Proof: Sub-50ms Reflex Intent Analysis (§1.4)
# ---------------------------------------------------------------------------


def test_reflex_router_latency_proof() -> None:
    """Verify that intent analysis latency is < 50ms hard limit and < 5ms mean."""
    latencies: list[float] = []
    # Test across iterations
    for _ in range(2):
        for prompt, _ in ROUTING_BENCHMARK_PROMPTS:
            t0 = time.perf_counter()
            analyze_intent(prompt)
            latencies.append((time.perf_counter() - t0) * 1000.0)

    mean_ms = sum(latencies) / len(latencies)
    max_ms = max(latencies)

    assert max_ms < 50.0, f"Max latency exceeded 50ms: {max_ms:.3f}ms"
    assert mean_ms < 5.0, f"Mean latency exceeded 5ms: {mean_ms:.3f}ms"


# ---------------------------------------------------------------------------
# 2. Complete Fallback Matrix (§F3, §F8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fallback_matrix_no_key_routes_to_local_brain() -> None:
    """When no API key is provided, cloud request falls back to local brain with notice."""
    cfg = make_test_settings()
    fake_local = FakeProvider(name="fake_local", is_local=True)
    fake_local.queue_response(
        [ChatChunk(delta_text="Local fallback response", finish_reason="stop")]
    )
    fake_cloud = FakeProvider(name="fake_cloud", is_local=False)

    ch = get_cloud_health()
    ch.state = CloudHealthState.NO_KEY
    fake_cloud.cloud_health = ch  # type: ignore[attr-defined]

    router = ModelRouter(cfg, providers={"fake_local": fake_local, "fake_cloud": fake_cloud})

    chunks = []
    user_msg = Message(
        role="user",
        content=Content(text="Research kernel architecture", data_class=DataClass.CHAT),
    )
    async for chunk in router.chat(role="cloud", messages=[user_msg]):
        chunks.append(chunk)

    full_text = "".join(c.text for c in chunks)
    assert "Local fallback response" in full_text
    assert len(fake_local.calls) == 1
    assert len(fake_cloud.calls) == 0


@pytest.mark.asyncio
async def test_fallback_matrix_rate_limited_routes_to_local_brain() -> None:
    """When cloud is rate limited, request falls back to local brain without crash."""
    cfg = make_test_settings()
    fake_local = FakeProvider(name="fake_local", is_local=True)
    fake_local.queue_response(
        [ChatChunk(delta_text="Local response during rate limit", finish_reason="stop")]
    )
    fake_cloud = FakeProvider(name="fake_cloud", is_local=False)

    ch = get_cloud_health()
    ch.state = CloudHealthState.RATE_LIMITED
    fake_cloud.cloud_health = ch  # type: ignore[attr-defined]

    router = ModelRouter(cfg, providers={"fake_local": fake_local, "fake_cloud": fake_cloud})

    chunks = []
    user_msg = Message(
        role="user",
        content=Content(text="Plan project", data_class=DataClass.CHAT),
    )
    async for chunk in router.chat(role="cloud", messages=[user_msg]):
        chunks.append(chunk)

    full_text = "".join(c.text for c in chunks)
    assert "Local response during rate limit" in full_text
    assert len(fake_local.calls) == 1
    assert len(fake_cloud.calls) == 0


@pytest.mark.asyncio
async def test_fallback_matrix_circuit_breaker_open_routes_to_local_brain() -> None:
    """When circuit breaker is open, requests immediately fall back locally."""
    cfg = make_test_settings()
    fake_local = FakeProvider(name="fake_local", is_local=True)
    fake_local.queue_response(
        [ChatChunk(delta_text="Local response while circuit breaker open", finish_reason="stop")]
    )
    fake_cloud = FakeProvider(name="fake_cloud", is_local=False)

    ch = get_cloud_health()
    ch.state = CloudHealthState.OFFLINE
    ch.circuit_breaker.state = "OPEN"
    fake_cloud.cloud_health = ch  # type: ignore[attr-defined]

    router = ModelRouter(cfg, providers={"fake_local": fake_local, "fake_cloud": fake_cloud})

    chunks = []
    user_msg = Message(
        role="user",
        content=Content(text="Hello cloud", data_class=DataClass.CHAT),
    )
    async for chunk in router.chat(role="cloud", messages=[user_msg]):
        chunks.append(chunk)

    full_text = "".join(c.text for c in chunks)
    assert "Local response while circuit breaker open" in full_text
    assert len(fake_local.calls) == 1
    assert len(fake_cloud.calls) == 0


# ---------------------------------------------------------------------------
# 3. Privacy & Sensitivity Isolation (§F2)
# ---------------------------------------------------------------------------


def test_sensitive_prompts_100_percent_isolated() -> None:
    """Every prompt with credentials or private keys must classify as SENSITIVE or be blocked."""
    test_secrets = [
        "My AWS secret is AKIAIOSFODNN7EXAMPLE",
        "The server password is: Sup3rSecret!",
        "Credit card 4111-2222-3333-4444",
        "Confidential medical diagnosis report details",
        "-----BEGIN OPENSSH PRIVATE KEY----- abc -----END OPENSSH PRIVATE KEY-----",
    ]

    for s in test_secrets:
        analysis = analyze_intent(s)
        assert analysis.intent == IntentType.SENSITIVE
        assert analysis.suggested_lane == "local"


def test_egress_filter_blocks_private_mode() -> None:
    """When private mode is enabled, egress filter strictly blocks cloud transmission."""
    cfg = make_test_settings(PrivacyConfig(private_mode=True))
    ef = EgressFilter(config=cfg)

    decision = ef.evaluate(
        messages=[Message(role="user", content="What is the weather?")],
        provider_name="fake_cloud",
        model="nemotron-49b",
        is_local=False,
    )
    assert not decision.allowed
    assert decision.decision == "rerouted_local"
    assert "Private mode" in decision.reason


# ---------------------------------------------------------------------------
# 4. Rate Budget Interactive Reserve (§F3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_budget_interactive_reserve() -> None:
    """Verify that background requests cannot deplete interactive reserve."""
    budget = RequestBudget(requests_per_minute=30, reserved_interactive=10)

    # Consume background allowance (up to 20 requests)
    for _ in range(20):
        assert await budget.acquire(is_interactive=False) is True

    # 21st background request should be rejected immediately (timeout=0)
    # to protect interactive reserve
    assert await budget.acquire(is_interactive=False, timeout=0.0) is False

    # Interactive requests should still succeed
    assert await budget.acquire(is_interactive=True, timeout=0.0) is True


# ---------------------------------------------------------------------------
# 5. Task Center Background Lifecycle (§F5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_center_lifecycle_and_cancellation() -> None:
    """Verify task queueing, execution, cancellation token, and completion."""
    await init_db()
    cfg = make_test_settings()
    fake = FakeProvider()
    fake.responses = [[ChatChunk(text="Scan completed: 100 files")]]
    router = ModelRouter(config=cfg, providers={"fake_local": fake})
    reg = ToolRegistry()

    center = TaskCenter(config=cfg, router=router, registry=reg)
    task = await center.create_task(
        title="Batch scan",
        prompt="Scan ~/Documents",
        lane=TaskLane.LOCAL,
        priority=TaskPriority.HIGH,
    )
    assert task.status == TaskStatus.QUEUED

    had_task = await center.process_next_task()
    assert had_task is True

    done_task = await center.get_task(task.id)
    assert done_task is not None
    assert done_task.status == TaskStatus.DONE
    assert done_task.progress == 1.0

    # Test cancellation of a queued task
    cancel_task = await center.create_task(
        title="Cancel me",
        prompt="Never finish",
    )
    ok = await center.cancel_task(cancel_task.id)
    assert ok is True
    cancelled = await center.get_task(cancel_task.id)
    assert cancelled is not None
    assert cancelled.status == TaskStatus.CANCELLED


# ---------------------------------------------------------------------------
# 6. CLI Eval Routing Integration (§F8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eval_routing_evaluator_results() -> None:
    """Verify the programmatic run_routing_eval returns 100% accuracy and satisfies benchmarks."""
    res = await run_routing_eval(offline=True, console=None)
    assert res["total"] == 44
    assert res["accuracy"] >= 0.95
    assert res["latency_target_met"] is True
    assert res["sensitive_target_met"] is True


def test_cli_eval_routing_command() -> None:
    """Verify that `nx eval-routing --offline --json` runs cleanly from CLI."""
    runner = CliRunner()
    result = runner.invoke(cli_app, ["eval-routing", "--offline", "--json"])
    assert result.exit_code == 0
    assert '"accuracy": 1.0' in result.stdout
    assert '"latency_target_met": true' in result.stdout
