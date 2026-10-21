"""Unit tests for NvidiaKeyPool, per-key rate tracking, and automatic failover."""

from __future__ import annotations

import time

from noxai.models.key_pool import KeyEntry, NvidiaKeyPool

# Use keys explicitly in EXPLICIT_KEY_ALLOWLIST to ensure repo hygiene passes
KEY_1 = "nvapi-abcdef1234567890abcdef1234"
KEY_2 = "nvapi-secret1234567890abcdef1234"
KEY_3 = "nvapi-1234567890abcdef12345678"


def test_key_entry_rate_tracking() -> None:
    entry = KeyEntry(api_key=KEY_1, index=1, rpm_limit=3)
    now = time.monotonic()

    assert entry.is_available(now)
    assert entry.current_rpm(now) == 0

    entry.record_request(now)
    entry.record_request(now)
    assert entry.current_rpm(now) == 2
    assert entry.is_available(now)

    entry.record_request(now)
    assert entry.current_rpm(now) == 3
    assert not entry.is_available(now)  # hit rpm_limit

    # Advance 61 seconds
    future = now + 61.0
    assert entry.is_available(future)
    assert entry.current_rpm(future) == 0


def test_key_entry_cooldown() -> None:
    entry = KeyEntry(api_key=KEY_1, index=1, rpm_limit=10)
    now = time.monotonic()

    entry.mark_rate_limited(cooldown_seconds=30.0)
    assert entry.is_cooling_down(now)
    assert not entry.is_available(now)

    # After cooldown expires
    future = now + 31.0
    assert not entry.is_cooling_down(future)
    assert entry.is_available(future)


def test_key_pool_least_busy_rotation() -> None:
    pool = NvidiaKeyPool(keys=[KEY_1, KEY_2, KEY_3], default_rpm_per_key=10)
    assert pool.total_keys == 3

    # Acquire 1st key -> should be KEY_1
    k1 = pool.acquire_best_key()
    assert k1.api_key == KEY_1

    # Acquire 2nd key -> KEY_2 (since KEY_1 has 1 request)
    k2 = pool.acquire_best_key()
    assert k2.api_key == KEY_2

    # Acquire 3rd key -> KEY_3
    k3 = pool.acquire_best_key()
    assert k3.api_key == KEY_3

    # Acquire 4th key -> rounds back to lowest RPM
    k4 = pool.acquire_best_key()
    assert k4.api_key in (KEY_1, KEY_2, KEY_3)


def test_key_pool_failover_on_rate_limit() -> None:
    pool = NvidiaKeyPool(keys=[KEY_1, KEY_2], default_rpm_per_key=10)

    # Mark KEY_1 as rate limited
    pool.mark_rate_limited(KEY_1, cooldown_seconds=60.0)

    # Next acquire should pick KEY_2 without hesitation
    selected = pool.acquire_best_key()
    assert selected.api_key == KEY_2


def test_key_pool_stats() -> None:
    pool = NvidiaKeyPool(keys=[KEY_1, KEY_2, KEY_3], default_rpm_per_key=35)
    stats = pool.get_stats()

    assert stats["total_keys"] == 3
    assert stats["available_keys"] == 3
    assert stats["cooling_keys"] == 0
    assert stats["max_pool_rpm"] == 105
    assert len(stats["keys"]) == 3
