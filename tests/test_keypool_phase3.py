"""Phase 3 KeyPool unit tests, pure allocation property tests, and simulation tests."""

from __future__ import annotations

import httpx
import pytest

from noxai.config import get_config
from noxai.models.keypool import (
    KeyPool,
    KeyState,
    KeyStatus,
    PoolSnapshot,
    allocate,
)


def test_pure_allocate_interactive_always_gets_key():
    """Property test: interactive requests always get a key when healthy keys exist."""
    keys = [
        KeyState(key_id="k1", api_key="nvapi-key1", tokens_available=20.0),
        KeyState(key_id="k2", api_key="nvapi-key2", tokens_available=15.0),
        KeyState(key_id="k3", api_key="nvapi-key3", tokens_available=5.0),
    ]
    snapshot = PoolSnapshot(
        keys=keys,
        active_jobs={"job_background": 2},
        pinned_assignments={"job_background": "k2"},
        interactive_in_flight=0,
    )

    chosen = allocate(snapshot, request_job_id=None, priority=0)
    assert chosen in ("k1", "k2", "k3")
    # k1 has highest tokens, should be chosen
    assert chosen == "k1"


def test_pure_allocate_pinned_affinity():
    """Property test: a background job maintains sticky affinity to its pinned key."""
    keys = [
        KeyState(key_id="k1", api_key="nvapi-key1", tokens_available=20.0),
        KeyState(key_id="k2", api_key="nvapi-key2", tokens_available=15.0),
    ]
    snapshot = PoolSnapshot(
        keys=keys,
        active_jobs={"job_1": 2},
        pinned_assignments={"job_1": "k2"},
        wide_mode_enabled=False,
    )

    # When job_1 asks, it should get pinned key k2
    chosen = allocate(snapshot, request_job_id="job_1", priority=2)
    assert chosen == "k2"


def test_pure_allocate_wide_mode_fan_out():
    """Property test: single background job in idle pool uses ALL keys in wide mode."""
    keys = [
        KeyState(key_id="k1", api_key="nvapi-key1", tokens_available=20.0, ewma_latency_ms=100.0),
        KeyState(key_id="k2", api_key="nvapi-key2", tokens_available=10.0, ewma_latency_ms=100.0),
    ]
    snapshot = PoolSnapshot(
        keys=keys,
        active_jobs={"briefing_job": 2},
        pinned_assignments={},
        interactive_in_flight=0,
        wide_mode_enabled=True,
    )

    chosen = allocate(snapshot, request_job_id="briefing_job", priority=2)
    assert chosen in ("k1", "k2")


@pytest.mark.asyncio
async def test_keypool_lease_and_outcome_reporting(tmp_path, monkeypatch):
    """Test leasing context manager and outcome reporting."""
    monkeypatch.setattr("noxai.db.DB_PATH", tmp_path / "test_kp.db")
    monkeypatch.setattr("noxai.db.DATA_DIR", tmp_path)
    from noxai.db import init_db

    await init_db()

    cfg = get_config()
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"ok": True}))
    pool = KeyPool(
        config=cfg,
        keys=["nvapi-test1", "nvapi-test2", "nvapi-test3"],
        transport=transport,
    )

    assert pool.total_keys == 3
    assert pool.healthy_keys_count == 3

    # Lease interactive
    async with pool.lease(priority=0) as lease:
        assert lease.key_id in ("k1", "k2", "k3")
        assert lease.api_key.startswith("nvapi-")

    # Simulate 429 error on k1
    await pool.report_outcome(key_id="k1", status_code=429, retry_after=5.0)
    snap = pool.get_snapshot()
    k1 = next(k for k in snap.keys if k.key_id == "k1")
    assert k1.status == KeyStatus.COOLDOWN
    assert k1.total_429s == 1

    # Simulate 401 on k2
    await pool.report_outcome(key_id="k2", status_code=401)
    snap2 = pool.get_snapshot()
    k2 = next(k for k in snap2.keys if k.key_id == "k2")
    assert k2.status == KeyStatus.INVALID

    # k3 is only healthy key left, must be leased
    async with pool.lease(priority=0) as lease2:
        assert lease2.key_id == "k3"


def test_continuous_refill():
    """Verify that tokens_available refills based on elapsed time."""
    key = KeyState(
        key_id="k1",
        api_key="nvapi-refill",
        rpm_limit=60,  # 1 token per second
        tokens_available=0.0,
    )
    t0 = key.last_refill_at

    # Check after 5 seconds
    key.refill_tokens(now=t0 + 5.0)
    assert 4.9 <= key.tokens_available <= 5.1

    # Check after 100 seconds (capped at rpm_limit = 60)
    key.refill_tokens(now=t0 + 100.0)
    assert key.tokens_available == 60.0


@pytest.mark.asyncio
async def test_release_job_cleans_pinned_and_active():
    """Verify release_job cleanly removes job tracking and pinned assignments."""
    cfg = get_config()
    pool = KeyPool(config=cfg, keys=["nvapi-k1", "nvapi-k2"])

    from noxai.models.keypool import AllocationMode

    # Lease with pinned mode
    async with pool.lease(job_id="job_sync_123", priority=2, mode=AllocationMode.PINNED) as lease:
        assert lease.key_id in ("k1", "k2")

    snap = pool.get_snapshot()
    assert "job_sync_123" in snap.active_jobs
    assert "job_sync_123" in snap.pinned_assignments

    # Release job
    await pool.release_job("job_sync_123")
    snap_after = pool.get_snapshot()
    assert "job_sync_123" not in snap_after.active_jobs
    assert "job_sync_123" not in snap_after.pinned_assignments


@pytest.mark.asyncio
async def test_404_model_alert_emitted(tmp_path, monkeypatch):
    """Verify that HTTP 404/410 emits a critical model_error event on event_bus."""
    monkeypatch.setattr("noxai.db.DB_PATH", tmp_path / "test_404.db")
    monkeypatch.setattr("noxai.db.DATA_DIR", tmp_path)
    from noxai.db import init_db
    await init_db()

    from noxai.events import EventBus

    bus = EventBus()
    received_events = []

    async def listener(evt):
        if evt.type == "model_error":
            received_events.append(evt)

    bus.subscribe(listener)

    cfg = get_config()
    pool = KeyPool(config=cfg, keys=["nvapi-k1"], event_bus=bus)

    await pool.report_outcome(key_id="k1", status_code=404, error_msg="Model retired")

    assert len(received_events) == 1
    assert received_events[0].type == "model_error"
    assert received_events[0].data["status_code"] == 404
    assert received_events[0].data["level"] == "critical"


@pytest.mark.asyncio
async def test_nvidia_provider_uses_unified_keypool_lease(tmp_path, monkeypatch):
    """Verify NvidiaProvider executes chat turn using KeyPool.lease()."""
    monkeypatch.setattr("noxai.db.DB_PATH", tmp_path / "test_nv_lease.db")
    monkeypatch.setattr("noxai.db.DATA_DIR", tmp_path)
    from noxai.db import init_db
    await init_db()

    cfg = get_config()

    def mock_handler(req: httpx.Request) -> httpx.Response:
        auth = req.headers.get("Authorization", "")
        assert auth == "Bearer nvapi-provider-test"
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1234567,
                "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "NvidiaProvider leased successfully."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
            },
        )

    transport = httpx.MockTransport(mock_handler)
    pool = KeyPool(config=cfg, keys=["nvapi-provider-test"], transport=transport)

    from noxai.models.base import Message
    from noxai.models.nvidia import NvidiaProvider

    prov = NvidiaProvider(key_pool=pool, transport=transport)

    messages = [Message(role="user", content="Hello")]
    chunks = []
    async for chunk in prov.chat(model="nvidia/nemotron-3.5-lightning-30b-a3b", messages=messages, stream=False):
        chunks.append(chunk.delta_text)

    full_resp = "".join(chunks)
    assert "NvidiaProvider leased successfully." in full_resp
    assert pool._keys["k1"].total_requests == 1

