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
