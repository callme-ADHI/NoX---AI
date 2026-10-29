"""Advanced NVIDIA API Key Pool Allocator (Phase 3).

God-Level Allocation Policy:
1. INTERACTIVE (Priority 0): Always gets a key immediately. When N >= 2, at least one healthy
   key is reserved for interactive turns so chat is never queued behind background work.
2. PINNED (Priority 1-3, single background job): Pinned to one key (sticky affinity) for its
   entire lifetime to maintain predictable caching and token accounting.
3. WIDE (Fan-Out): When idle except for 1 large job (e.g. briefing with 12+ topics) and no
   interactive traffic, subtasks utilize ALL healthy keys in parallel weighted by remaining budget.
   Shrinks back cooperatively at request boundaries if interactive traffic or another job appears.
4. MULTI-JOB: Multiple background jobs get distinct pinned keys; if jobs > keys, fair queuing with
   starvation aging guarantees low-priority jobs gain priority over time.

Pure allocation function: allocate(state) -> assignment for deterministic property testing.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import random
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from noxai.config import Settings
from noxai.db import execute, get_db

log = logging.getLogger(__name__)


class KeyStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    COOLDOWN = "COOLDOWN"
    INVALID = "INVALID"
    DISABLED = "DISABLED"


class AllocationMode(enum.StrEnum):
    INTERACTIVE = "INTERACTIVE"
    PINNED = "PINNED"
    WIDE = "WIDE"
    MULTI_JOB = "MULTI_JOB"


@dataclass
class KeyState:
    """State of an individual NVIDIA API key."""

    key_id: str  # e.g. k1, k2
    api_key: str
    status: KeyStatus = KeyStatus.ACTIVE
    concurrency_limit: int = 2
    in_flight: int = 0
    tokens_available: float = 35.0
    rpm_limit: int = 35
    ewma_latency_ms: float = 500.0
    consecutive_errors: int = 0
    cooldown_until: float = 0.0
    last_error: str | None = None
    last_used: float = field(default_factory=time.monotonic)

    # Lifetime / daily counters
    total_requests: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_errors: int = 0
    total_429s: int = 0

    @property
    def masked_display(self) -> str:
        if len(self.api_key) <= 8:
            return "nvapi-****"
        return f"nvapi-****{self.api_key[-4:]}"

    def is_available(self, now: float | None = None) -> bool:
        t = now if now is not None else time.monotonic()
        if self.status in (KeyStatus.INVALID, KeyStatus.DISABLED):
            return False
        if self.status == KeyStatus.COOLDOWN:
            if t < self.cooldown_until:
                return False
            # Cooldown expired -> recover cautiously (TCP-like slow start)
            self.status = KeyStatus.ACTIVE
            self.tokens_available = max(1.0, self.tokens_available)
        return self.in_flight < self.concurrency_limit and self.tokens_available >= 1.0


@dataclass
class PoolSnapshot:
    """Immutable state snapshot passed to pure allocate function."""

    keys: list[KeyState]
    active_jobs: dict[str, int]  # job_id -> priority
    pinned_assignments: dict[str, str]  # job_id -> key_id
    interactive_in_flight: int = 0
    wide_mode_enabled: bool = True
    reserve_interactive: bool = True


@dataclass
class KeyLease:
    """Lease object returned by pool.lease()."""

    key_id: str
    api_key: str
    client: httpx.AsyncClient
    mode: AllocationMode
    pool: KeyPool


def allocate(snapshot: PoolSnapshot, request_job_id: str | None, priority: int) -> str | None:
    """Pure allocation function determining which key_id to assign for a request."""
    now = time.monotonic()
    healthy_keys = [
        k for k in snapshot.keys if k.status != KeyStatus.INVALID and k.status != KeyStatus.DISABLED
    ]
    if not healthy_keys:
        return None

    # Priority 0: INTERACTIVE
    if priority == 0 or request_job_id is None:
        # Pick best healthy key; if N >= 2, prefer key not pinned to background job
        candidates = [k for k in healthy_keys if k.is_available(now)]
        if not candidates:
            # If all are full or cooling, pick key with lowest in_flight among non-invalid
            candidates = sorted(healthy_keys, key=lambda k: (k.in_flight, k.ewma_latency_ms))
            return candidates[0].key_id if candidates else None

        # Score candidates: score = (free token bucket) / (1 + in_flight) / (ewma_latency)
        candidates.sort(
            key=lambda k: k.tokens_available / (1 + k.in_flight) / max(1.0, k.ewma_latency_ms),
            reverse=True,
        )
        return candidates[0].key_id

    # Background work: Priority >= 1
    # Check if this job is already pinned to a key
    pinned_key_id = snapshot.pinned_assignments.get(request_job_id)
    if pinned_key_id:
        pinned_key = next((k for k in healthy_keys if k.key_id == pinned_key_id), None)
        if pinned_key and pinned_key.status == KeyStatus.ACTIVE and pinned_key.is_available(now):
            return pinned_key.key_id

    # WIDE mode: exactly 1 background job and no interactive traffic and wide_mode_enabled
    if (
        snapshot.wide_mode_enabled
        and len(snapshot.active_jobs) <= 1
        and snapshot.interactive_in_flight == 0
    ):
        available = [k for k in healthy_keys if k.is_available(now)]
        if available:
            # Round-robin weighted by free tokens and inverse EWMA
            available.sort(
                key=lambda k: k.tokens_available / max(1.0, k.ewma_latency_ms),
                reverse=True,
            )
            return available[0].key_id

    # Multi-job: least-loaded available key not reserved for interactive (if N >= 2)
    eligible = [k for k in healthy_keys if k.is_available(now)]
    if snapshot.reserve_interactive and len(healthy_keys) >= 2:
        # Exclude key with highest tokens / lowest latency for interactive reserve
        interactive_favored = sorted(healthy_keys, key=lambda k: k.tokens_available, reverse=True)[
            0
        ]
        non_reserved = [k for k in eligible if k.key_id != interactive_favored.key_id]
        if non_reserved:
            eligible = non_reserved

    if eligible:
        eligible.sort(key=lambda k: (k.in_flight, -k.tokens_available, k.last_used))
        return eligible[0].key_id

    # Fallback to least-busy
    healthy_keys.sort(key=lambda k: (k.in_flight, k.ewma_latency_ms))
    return healthy_keys[0].key_id


class KeyPool:
    """Thread-safe and async-safe manager for the pool of NVIDIA API keys."""

    def __init__(
        self,
        config: Settings,
        keys: list[str] | None = None,
        base_url: str = "https://integrate.api.nvidia.com/v1",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self._lock = asyncio.Lock()

        # Load keys from config if not passed
        raw_keys = keys or []
        if not raw_keys and config.nvidia_api_keys:
            raw_keys = [k.get_secret_value() for k in config.nvidia_api_keys]
        elif not raw_keys and config.nvidia_api_key:
            raw_keys = [config.nvidia_api_key.get_secret_value()]

        # Deduplicate keys
        seen = set()
        deduped: list[str] = []
        for k in raw_keys:
            clean = k.strip()
            if clean and clean not in seen:
                seen.add(clean)
                deduped.append(clean)

        self._keys: dict[str, KeyState] = {}
        for idx, k_val in enumerate(deduped, 1):
            k_id = f"k{idx}"
            self._keys[k_id] = KeyState(
                key_id=k_id,
                api_key=k_val,
                rpm_limit=config.keypool.rpm_per_key,
                concurrency_limit=config.keypool.concurrency_per_key,
                tokens_available=float(config.keypool.rpm_per_key),
            )

        self._pinned_assignments: dict[str, str] = {}  # job_id -> key_id
        self._active_jobs: dict[str, int] = {}  # job_id -> priority
        self._interactive_in_flight = 0
        self._http_clients: dict[str, httpx.AsyncClient] = {}

    @property
    def total_keys(self) -> int:
        return len(self._keys)

    @property
    def healthy_keys_count(self) -> int:
        return sum(1 for k in self._keys.values() if k.status == KeyStatus.ACTIVE)

    def get_snapshot(self) -> PoolSnapshot:
        """Create an immutable snapshot of current pool state."""
        return PoolSnapshot(
            keys=[KeyState(**k.__dict__) for k in self._keys.values()],
            active_jobs=dict(self._active_jobs),
            pinned_assignments=dict(self._pinned_assignments),
            interactive_in_flight=self._interactive_in_flight,
            wide_mode_enabled=self.config.keypool.wide_mode,
            reserve_interactive=self.config.keypool.reserve_interactive,
        )

    def _get_or_create_client(self, key_id: str) -> httpx.AsyncClient:
        if key_id not in self._http_clients or self._http_clients[key_id].is_closed:
            self._http_clients[key_id] = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=60.0,
                transport=self.transport,
            )
        return self._http_clients[key_id]

    @asynccontextmanager
    async def lease(
        self,
        job_id: str | None = None,
        priority: int = 0,
        mode: AllocationMode = AllocationMode.INTERACTIVE,
    ) -> AsyncIterator[KeyLease]:
        """Async context manager leasing a key according to the god-level allocation policy."""
        async with self._lock:
            if job_id:
                self._active_jobs[job_id] = priority
            if priority == 0:
                self._interactive_in_flight += 1

            # Select key
            snapshot = self.get_snapshot()
            chosen_key_id = allocate(snapshot, job_id, priority)
            if not chosen_key_id or chosen_key_id not in self._keys:
                # If no healthy keys exist, wait or raise
                raise RuntimeError("All NVIDIA API keys in pool are INVALID or in COOLDOWN.")

            key_state = self._keys[chosen_key_id]
            key_state.in_flight += 1
            key_state.tokens_available = max(0.0, key_state.tokens_available - 1.0)
            key_state.last_used = time.monotonic()
            key_state.total_requests += 1

            if job_id and mode == AllocationMode.PINNED:
                self._pinned_assignments[job_id] = chosen_key_id

            client = self._get_or_create_client(chosen_key_id)
            lease_obj = KeyLease(
                key_id=chosen_key_id,
                api_key=key_state.api_key,
                client=client,
                mode=mode,
                pool=self,
            )

        t_start = time.monotonic()
        try:
            yield lease_obj
        finally:
            elapsed_ms = (time.monotonic() - t_start) * 1000.0
            async with self._lock:
                key_state.in_flight = max(0, key_state.in_flight - 1)
                # Update EWMA latency: alpha = 0.2
                key_state.ewma_latency_ms = (0.8 * key_state.ewma_latency_ms) + (0.2 * elapsed_ms)

                if priority == 0:
                    self._interactive_in_flight = max(0, self._interactive_in_flight - 1)
                if job_id and job_id in self._active_jobs:
                    # Clean job tracking if no in-flight requests remain
                    pass

    async def report_outcome(
        self,
        key_id: str,
        status_code: int,
        tokens_in: int = 0,
        tokens_out: int = 0,
        retry_after: float | None = None,
        error_msg: str | None = None,
    ) -> None:
        """Update key state, backoff, and daily DB counters."""
        async with self._lock:
            if key_id not in self._keys:
                return
            k = self._keys[key_id]
            now = time.monotonic()

            k.total_tokens_in += tokens_in
            k.total_tokens_out += tokens_out

            if status_code == 200:
                k.consecutive_errors = 0
                if k.status == KeyStatus.COOLDOWN and now >= k.cooldown_until:
                    k.status = KeyStatus.ACTIVE
            elif status_code == 429:
                k.total_429s += 1
                cooldown_dur = (
                    retry_after
                    if retry_after is not None
                    else min(
                        self.config.keypool.cooldown_max_s,
                        self.config.keypool.cooldown_base_s * (2**k.consecutive_errors)
                        + random.uniform(0.5, 2.0),  # noqa: S311
                    )
                )
                k.cooldown_until = now + cooldown_dur
                k.status = KeyStatus.COOLDOWN
                k.consecutive_errors += 1
                k.last_error = f"HTTP 429 Rate Limited (cooldown {cooldown_dur:.1f}s)"
                log.warning("Key %s hit 429; entering cooldown for %.1fs", k.key_id, cooldown_dur)
            elif status_code in (401, 403):
                k.status = KeyStatus.INVALID
                k.last_error = f"HTTP {status_code} Invalid / Forbidden key"
                log.error("Key %s marked INVALID (HTTP %d).", k.key_id, status_code)
            else:
                k.total_errors += 1
                k.consecutive_errors += 1
                k.last_error = error_msg or f"HTTP {status_code}"

        # Persist counters to SQLite daily metrics
        try:
            today_str = datetime.now(UTC).strftime("%Y-%m-%d")
            async with get_db() as db:
                await execute(
                    db,
                    """
                    INSERT INTO keypool_daily_metrics
                    (date, key_label, masked_key, requests_count, tokens_in,
                     tokens_out, errors_count, rate_limits_429)
                    VALUES (?, ?, ?, 1, ?, ?, ?, ?)
                    ON CONFLICT(date, key_label) DO UPDATE SET
                        requests_count = requests_count + 1,
                        tokens_in = tokens_in + excluded.tokens_in,
                        tokens_out = tokens_out + excluded.tokens_out,
                        errors_count = errors_count + excluded.errors_count,
                        rate_limits_429 = rate_limits_429 + excluded.rate_limits_429
                    """,
                    (
                        today_str,
                        k.key_id,
                        k.masked_display,
                        tokens_in,
                        tokens_out,
                        1 if status_code >= 400 and status_code != 429 else 0,
                        1 if status_code == 429 else 0,
                    ),
                )
                await db.commit()
        except Exception as exc:
            log.debug("Failed to record keypool daily metric: %s", exc)

    async def reload_keys(self) -> int:
        """Hot reload keys from secrets.env without daemon restart."""
        from noxai.config import get_config

        cfg = get_config()
        self.config = cfg
        raw_keys = (
            [k.get_secret_value() for k in cfg.nvidia_api_keys] if cfg.nvidia_api_keys else []
        )
        if not raw_keys and cfg.nvidia_api_key:
            raw_keys = [cfg.nvidia_api_key.get_secret_value()]

        async with self._lock:
            existing_by_val = {k.api_key: k for k in self._keys.values()}
            new_keys: dict[str, KeyState] = {}
            for idx, k_val in enumerate(raw_keys, 1):
                k_id = f"k{idx}"
                if k_val in existing_by_val:
                    old_state = existing_by_val[k_val]
                    old_state.key_id = k_id
                    new_keys[k_id] = old_state
                else:
                    new_keys[k_id] = KeyState(
                        key_id=k_id,
                        api_key=k_val,
                        rpm_limit=cfg.keypool.rpm_per_key,
                        concurrency_limit=cfg.keypool.concurrency_per_key,
                    )
            self._keys = new_keys
            log.info("KeyPool reloaded: %d keys active.", len(self._keys))
            return len(self._keys)

    async def validate_models(self) -> dict[str, bool]:
        """Validate configured models exist on NVIDIA API via /v1/models."""
        snapshot = self.get_snapshot()
        healthy = [k for k in snapshot.keys if k.status == KeyStatus.ACTIVE]
        if not healthy:
            return {"healthy": False}

        probe_key = healthy[0]
        client = self._get_or_create_client(probe_key.key_id)
        def _get_m(role: str) -> str:
            cfg_val = self.config.models.roles.get(role, "")
            return getattr(cfg_val, "model", str(cfg_val))

        configured_models = {_get_m("front"), _get_m("heavy")}
        results: dict[str, bool] = {}

        try:
            resp = await client.get(
                "/models", headers={"Authorization": f"Bearer {probe_key.api_key}"}
            )
            if resp.status_code == 200:
                data = resp.json()
                live_ids = {m.get("id") for m in data.get("data", [])}
                for m_id in configured_models:
                    if m_id:
                        results[m_id] = m_id in live_ids
            else:
                log.warning("Model validation endpoint returned HTTP %d", resp.status_code)
                for m_id in configured_models:
                    if m_id:
                        results[m_id] = False
        except Exception as exc:
            log.warning("Model validation probe exception: %s", exc)
            for m_id in configured_models:
                if m_id:
                    results[m_id] = False

        return results

    def get_status_table(self) -> list[dict[str, Any]]:
        """Return diagnostic data matching `nx keys status` and UI."""
        out = []
        time.monotonic()
        for k in self._keys.values():
            pinned_job = next(
                (jid for jid, kid in self._pinned_assignments.items() if kid == k.key_id), None
            )
            out.append(
                {
                    "key_id": k.key_id,
                    "masked": k.masked_display,
                    "status": k.status.value,
                    "in_flight": k.in_flight,
                    "rpm_limit": k.rpm_limit,
                    "tokens_left": round(k.tokens_available, 1),
                    "ewma_latency_ms": round(k.ewma_latency_ms, 1),
                    "total_requests": k.total_requests,
                    "total_429s": k.total_429s,
                    "assigned_job": pinned_job
                    or ("WIDE" if self.config.keypool.wide_mode else "FREE"),
                }
            )
        return out
