"""Multi-key pool and dynamic load balancer for NVIDIA API keys.

Provides:
- Per-key sliding window request rate tracking against NVIDIA's RPM limits.
- Least-busy key rotation ensuring balanced request distribution.
- Fast, transparent failover if any key receives HTTP 429 (rate limit) or auth error.
- Aggregate pool capacity calculation and health telemetry.
"""

from __future__ import annotations

import asyncio
import collections
import logging
import time
from typing import Any

log = logging.getLogger(__name__)


class KeyEntry:
    """Represents a single NVIDIA API key with rate tracking and cooldown state."""

    def __init__(
        self,
        api_key: str,
        index: int = 1,
        rpm_limit: int = 35,
    ) -> None:
        self.api_key = api_key.strip()
        self.index = index
        self.label = f"key_{index} (...{self.api_key[-6:] if len(self.api_key) >= 6 else '***'})"
        self.rpm_limit = max(1, rpm_limit)
        self.request_timestamps: collections.deque[float] = collections.deque()
        self.cooldown_until: float = 0.0
        self.consecutive_errors: int = 0
        self.total_requests: int = 0

    def prune_window(self, now: float) -> None:
        """Remove timestamps older than 60 seconds from sliding window."""
        cutoff = now - 60.0
        while self.request_timestamps and self.request_timestamps[0] < cutoff:
            self.request_timestamps.popleft()

    def current_rpm(self, now: float | None = None) -> int:
        """Return the number of requests made in the last 60 seconds."""
        t = now if now is not None else time.monotonic()
        self.prune_window(t)
        return len(self.request_timestamps)

    def is_cooling_down(self, now: float | None = None) -> bool:
        """Return True if key is currently in cooldown from rate limit / error."""
        t = now if now is not None else time.monotonic()
        return t < self.cooldown_until

    def is_available(self, now: float | None = None) -> bool:
        """Return True if key is ready to accept a new request."""
        t = now if now is not None else time.monotonic()
        if self.is_cooling_down(t):
            return False
        return self.current_rpm(t) < self.rpm_limit

    def record_request(self, now: float | None = None) -> None:
        """Record that a request was sent using this key."""
        t = now if now is not None else time.monotonic()
        self.prune_window(t)
        self.request_timestamps.append(t)
        self.total_requests += 1

    def mark_rate_limited(self, cooldown_seconds: float = 60.0) -> None:
        """Place key in cooldown due to HTTP 429."""
        now = time.monotonic()
        self.cooldown_until = now + max(5.0, cooldown_seconds)
        self.consecutive_errors += 1
        log.warning(
            "NVIDIA API %s rate-limited; cooling down for %.1fs (consecutive=%d)",
            self.label,
            cooldown_seconds,
            self.consecutive_errors,
        )

    def mark_error(self, cooldown_seconds: float = 15.0) -> None:
        """Temporarily back off on error."""
        now = time.monotonic()
        self.cooldown_until = now + cooldown_seconds
        self.consecutive_errors += 1

    def mark_success(self) -> None:
        """Reset consecutive error counter on successful response."""
        self.consecutive_errors = 0

    def to_dict(self, now: float | None = None) -> dict[str, Any]:
        """Diagnostic representation of key state."""
        t = now if now is not None else time.monotonic()
        rpm = self.current_rpm(t)
        cooling = self.is_cooling_down(t)
        cooldown_remaining = max(0.0, round(self.cooldown_until - t, 1)) if cooling else 0.0
        return {
            "index": self.index,
            "label": self.label,
            "rpm": rpm,
            "rpm_limit": self.rpm_limit,
            "is_available": self.is_available(t),
            "is_cooling_down": cooling,
            "cooldown_remaining_sec": cooldown_remaining,
            "total_requests": self.total_requests,
        }


class NvidiaKeyPool:
    """Manages multiple NVIDIA API keys with balanced rotation and automatic failover."""

    def __init__(
        self,
        keys: list[str] | None = None,
        default_rpm_per_key: int = 35,
    ) -> None:
        self.default_rpm_per_key = default_rpm_per_key
        self._entries: list[KeyEntry] = []
        self._lock = asyncio.Lock()
        if keys:
            for k in keys:
                self.add_key(k)

    def add_key(self, api_key: str, rpm_limit: int | None = None) -> None:
        """Add an API key to the pool if not already present."""
        clean = api_key.strip()
        if not clean:
            return
        for e in self._entries:
            if e.api_key == clean:
                return
        idx = len(self._entries) + 1
        limit = rpm_limit or self.default_rpm_per_key
        self._entries.append(KeyEntry(clean, index=idx, rpm_limit=limit))

    @property
    def total_keys(self) -> int:
        return len(self._entries)

    def is_empty(self) -> bool:
        return len(self._entries) == 0

    def get_all_keys(self) -> list[str]:
        return [e.api_key for e in self._entries]

    def acquire_best_key(self) -> KeyEntry:
        """Select the best key: not cooling down, with lowest active RPM.

        If all keys are cooling down or at limit, returns the key closest to becoming available.
        Raises ValueError if the pool is empty.
        """
        if not self._entries:
            raise ValueError("No NVIDIA API keys configured in pool.")

        now = time.monotonic()

        # 1. Filter keys that are fully available (not cooling down, under limit)
        available = [e for e in self._entries if e.is_available(now)]
        if available:
            # Sort by lowest active RPM, then lowest total requests
            best = min(available, key=lambda e: (e.current_rpm(now), e.total_requests))
            best.record_request(now)
            return best

        # 2. Filter keys that are NOT cooling down (even if near limit)
        not_cooling = [e for e in self._entries if not e.is_cooling_down(now)]
        if not_cooling:
            best = min(not_cooling, key=lambda e: (e.current_rpm(now), e.total_requests))
            best.record_request(now)
            return best

        # 3. All keys cooling down: pick key whose cooldown expires earliest
        best = min(self._entries, key=lambda e: e.cooldown_until)
        best.record_request(now)
        return best

    def mark_rate_limited(self, api_key: str, cooldown_seconds: float = 60.0) -> None:
        """Mark a specific key as rate limited."""
        clean = api_key.strip()
        for e in self._entries:
            if e.api_key == clean:
                e.mark_rate_limited(cooldown_seconds)
                break

    def mark_error(self, api_key: str, cooldown_seconds: float = 15.0) -> None:
        """Mark a specific key as encountering a temporary error."""
        clean = api_key.strip()
        for e in self._entries:
            if e.api_key == clean:
                e.mark_error(cooldown_seconds)
                break

    def mark_success(self, api_key: str) -> None:
        """Mark a specific key as succeeding."""
        clean = api_key.strip()
        for e in self._entries:
            if e.api_key == clean:
                e.mark_success()
                break

    def get_stats(self) -> dict[str, Any]:
        """Return diagnostic metrics for the entire key pool."""
        now = time.monotonic()
        total = len(self._entries)
        available = sum(1 for e in self._entries if e.is_available(now))
        cooling = sum(1 for e in self._entries if e.is_cooling_down(now))
        total_rpm = sum(e.current_rpm(now) for e in self._entries)
        max_rpm = sum(e.rpm_limit for e in self._entries)
        total_requests = sum(e.total_requests for e in self._entries)

        return {
            "total_keys": total,
            "available_keys": available,
            "cooling_keys": cooling,
            "active_pool_rpm": total_rpm,
            "max_pool_rpm": max_rpm,
            "total_requests": total_requests,
            "keys": [e.to_dict(now) for e in self._entries],
        }
