"""Cloud health state machine and circuit breaker for NoX (§1.5).

Tracks health of external cloud models (NVIDIA API) through states:
- ACTIVE: Healthy, responsive, credentials valid.
- NO_KEY: Key missing or empty.
- INVALID_KEY: HTTP 401 or 403 authorization failure.
- RATE_LIMITED: HTTP 429 or budget exhaustion with active cooldown.
- DEGRADED: Repeated timeouts or TTFT above threshold.
- OFFLINE: DNS resolution or network transport failure.

A circuit breaker trips OPEN after consecutive failures, enters HALF_OPEN
after a cooldown period allowing a single probe, and recovers to CLOSED (ACTIVE).
Any state other than ACTIVE causes front-desk chat to execute locally.
"""

from __future__ import annotations

import datetime
import enum
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class CloudHealthState(enum.StrEnum):
    """Observable states of the cloud model provider."""

    ACTIVE = "active"
    NO_KEY = "no_key"
    INVALID_KEY = "invalid_key"
    RATE_LIMITED = "rate_limited"
    DEGRADED = "degraded"
    OFFLINE = "offline"


class CircuitState(enum.StrEnum):
    """Internal states of the circuit breaker."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing fast; requests blocked or diverted
    HALF_OPEN = "half_open"  # Testing recovery with single probe


class CircuitBreaker:
    """Circuit breaker with configurable failure threshold and cooldown."""

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
    ) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(1.0, cooldown_seconds)
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.consecutive_successes = 0
        self.last_failure_time: float = 0.0
        self.last_state_change: float = time.monotonic()
        self.probe_in_flight: bool = False

    def can_attempt(self) -> bool:
        """Return True if a request is allowed to attempt the cloud provider."""
        now = time.monotonic()

        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            elapsed = now - self.last_failure_time
            if elapsed >= self.cooldown_seconds:
                log.info(
                    "Circuit breaker cooldown elapsed (%.1fs); entering HALF_OPEN probe.",
                    elapsed,
                )
                self.state = CircuitState.HALF_OPEN
                self.last_state_change = now
                self.probe_in_flight = True
                return True
            return False

        if self.state == CircuitState.HALF_OPEN:
            # Allow single probe only
            if not self.probe_in_flight:
                self.probe_in_flight = True
                return True
            return False

        return False

    def record_success(self) -> None:
        """Record a successful response."""
        self.consecutive_failures = 0
        self.consecutive_successes += 1
        self.probe_in_flight = False

        if self.state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
            log.info("Circuit breaker probe succeeded; resetting to CLOSED.")
            self.state = CircuitState.CLOSED
            self.last_state_change = time.monotonic()

    def record_failure(self, custom_cooldown: float | None = None) -> None:
        """Record a failed response."""
        now = time.monotonic()
        self.consecutive_failures += 1
        self.consecutive_successes = 0
        self.last_failure_time = now
        self.probe_in_flight = False

        if custom_cooldown is not None and custom_cooldown > 0:
            self.cooldown_seconds = custom_cooldown

        tripped = (
            self.state == CircuitState.HALF_OPEN
            or self.consecutive_failures >= self.failure_threshold
        )
        if tripped:
            if self.state != CircuitState.OPEN:
                log.warning(
                    "Circuit breaker tripped OPEN (%d consecutive failures, cooldown=%.1fs)",
                    self.consecutive_failures,
                    self.cooldown_seconds,
                )
            self.state = CircuitState.OPEN
            self.last_state_change = now

    def force_open(self, custom_cooldown: float | None = None) -> None:
        """Immediately trip the circuit breaker to OPEN."""
        now = time.monotonic()
        if custom_cooldown is not None and custom_cooldown > 0:
            self.cooldown_seconds = custom_cooldown
        self.state = CircuitState.OPEN
        self.last_failure_time = now
        self.last_state_change = now
        self.probe_in_flight = False

    def cooldown_remaining(self) -> float:
        """Seconds remaining before circuit breaker half-opens."""
        if self.state != CircuitState.OPEN:
            return 0.0
        elapsed = time.monotonic() - self.last_failure_time
        return max(0.0, round(self.cooldown_seconds - elapsed, 1))


class CloudHealth:
    """Manages cloud availability status, transitions, and circuit breaking."""

    def __init__(
        self,
        has_key: bool = False,
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
        ttft_threshold_seconds: float = 8.0,
        secrets_path: Path | None = None,
        probe_interval_seconds: float = 60.0,
    ) -> None:
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=failure_threshold,
            cooldown_seconds=cooldown_seconds,
        )
        self.ttft_threshold_seconds = ttft_threshold_seconds
        self.secrets_path = secrets_path
        self.probe_interval_seconds = probe_interval_seconds
        self.last_probe_time: float = 0.0
        self._last_secrets_mtime: float | None = None
        self._current_key: str | None = None
        self.state = CloudHealthState.ACTIVE if has_key else CloudHealthState.NO_KEY
        self.reason = "Ready" if has_key else "NVIDIA API key not configured"
        self.since_timestamp = datetime.datetime.now(datetime.UTC).isoformat()
        self.last_ttft: float | None = None
        self.last_latency: float | None = None

        if not has_key:
            self.circuit_breaker.state = CircuitState.OPEN

    def _transition(self, new_state: CloudHealthState, reason: str) -> None:
        """Transition health state and record timestamp."""
        if self.state != new_state or self.reason != reason:
            log.info("CloudHealth transition: %s -> %s (%s)", self.state, new_state, reason)
            self.state = new_state
            self.reason = reason
            self.since_timestamp = datetime.datetime.now(datetime.UTC).isoformat()

    def can_attempt(self) -> bool:
        """Return True if a request is allowed to attempt the cloud provider."""
        if self.state in (CloudHealthState.NO_KEY, CloudHealthState.INVALID_KEY):
            return False
        return self.circuit_breaker.can_attempt()

    def is_active(self) -> bool:
        """Return True if cloud model is healthy and ready for interactive chat."""
        if self.state in (
            CloudHealthState.NO_KEY,
            CloudHealthState.INVALID_KEY,
            CloudHealthState.RATE_LIMITED,
            CloudHealthState.OFFLINE,
        ):
            return False
        return self.circuit_breaker.can_attempt()

    def record_success(
        self,
        latency_seconds: float = 0.0,
        ttft_seconds: float | None = None,
    ) -> None:
        """Record successful completion of a cloud turn."""
        self.last_latency = latency_seconds
        self.last_ttft = ttft_seconds
        self.circuit_breaker.record_success()

        # Check TTFT threshold for degraded warning
        if ttft_seconds is not None and ttft_seconds > self.ttft_threshold_seconds:
            self._transition(
                CloudHealthState.DEGRADED,
                f"High TTFT latency ({ttft_seconds:.2f}s > {self.ttft_threshold_seconds:.1f}s)",
            )
        else:
            self._transition(CloudHealthState.ACTIVE, "Endpoint healthy and responsive")

    def record_failure(
        self,
        error: Exception | str,
        status_code: int | None = None,
        latency_seconds: float | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Record an error or exception during a cloud turn."""
        self.last_latency = latency_seconds
        err_str = str(error)

        if status_code in (401, 403):
            self.circuit_breaker.force_open(custom_cooldown=300.0)
            self._transition(
                CloudHealthState.INVALID_KEY,
                f"HTTP {status_code}: Invalid or unauthorized API key ({err_str[:80]})",
            )
        elif status_code == 429:
            cooldown = retry_after if retry_after is not None else 60.0
            self.circuit_breaker.force_open(custom_cooldown=cooldown)
            self._transition(
                CloudHealthState.RATE_LIMITED,
                f"HTTP 429: Rate limited by provider (cooldown {cooldown:.0f}s)",
            )
        elif any(
            k in err_str.lower()
            for k in (
                "dns",
                "connect",
                "connection",
                "nameresolution",
                "network",
                "unreachable",
                "offline",
            )
        ):
            self.circuit_breaker.record_failure()
            self._transition(
                CloudHealthState.OFFLINE,
                f"Network unreachable: {err_str[:80]}",
            )
        elif any(k in err_str.lower() for k in ("timeout", "timed out", "readtimeout")):
            self.circuit_breaker.record_failure()
            self._transition(
                CloudHealthState.DEGRADED,
                f"Provider timeout: {err_str[:80]}",
            )
        else:
            self.circuit_breaker.record_failure()
            if self.circuit_breaker.state == CircuitState.OPEN:
                self._transition(
                    CloudHealthState.DEGRADED,
                    f"Repeated failures: {err_str[:80]}",
                )

    def set_no_key(self, reason: str = "NVIDIA API key not configured") -> None:
        """Declare cloud unavailable due to missing API key."""
        self.circuit_breaker.state = CircuitState.OPEN
        self._transition(CloudHealthState.NO_KEY, reason)

    def check_secrets_file(self, force: bool = False) -> bool:
        """Inspect secrets.env for API key without requiring daemon restart (§G3.4).

        Re-reads the secrets file on mtime change (or when force=True) and transitions
        from NO_KEY/INVALID_KEY to ACTIVE within one probe tick when a valid key appears.
        """
        import os

        from noxai.config import SECRETS_FILE, get_config

        path = self.secrets_path or SECRETS_FILE
        if not path.exists():
            if not os.environ.get("NOX_NVIDIA_API_KEY") and not self._current_key:
                if self.state != CloudHealthState.NO_KEY:
                    self.set_no_key("NVIDIA API key not configured (secrets.env missing)")
            return self.is_active()

        try:
            mtime = path.stat().st_mtime
        except OSError:
            return self.is_active()

        if not force and self._last_secrets_mtime is not None and mtime <= self._last_secrets_mtime:
            return self.is_active()

        self._last_secrets_mtime = mtime
        key: str | None = None
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'\"")
                    if (k.startswith("NVIDIA_API_KEY") or k == "NOX_NVIDIA_API_KEY") and v:
                        key = v.split(",")[0].strip() if "," in v else v
                        break
        except Exception as exc:
            log.warning("Could not read secrets file %s: %s", path, exc)
            return self.is_active()

        if key:
            self._current_key = key
            os.environ["NOX_NVIDIA_API_KEY"] = key
            try:
                get_config.cache_clear()
            except Exception as exc:
                log.debug("Failed to clear get_config cache: %s", exc)
            if self.state in (CloudHealthState.NO_KEY, CloudHealthState.INVALID_KEY) or force:
                self.circuit_breaker.state = CircuitState.CLOSED
                self.circuit_breaker.consecutive_failures = 0
                self._transition(CloudHealthState.ACTIVE, "Ready")
            return True
        else:
            self._current_key = None
            if self.state != CloudHealthState.NO_KEY:
                self.set_no_key("NVIDIA API key not configured")
            return False

    def tick(self, force: bool = False, now: float | None = None) -> None:
        """Periodic health probe tick to check secrets mtime and circuit breaker recovery."""
        current_time = now if now is not None else time.monotonic()
        if force or (current_time - self.last_probe_time >= self.probe_interval_seconds):
            self.last_probe_time = current_time
            self.check_secrets_file(force=force)
            if self.circuit_breaker.state == CircuitState.OPEN:
                self.circuit_breaker.can_attempt()

    def to_dict(self) -> dict[str, Any]:
        """Return diagnostic health status."""
        return {
            "state": self.state.value,
            "circuit": self.circuit_breaker.state.value,
            "is_active": self.is_active(),
            "reason": self.reason,
            "since": self.since_timestamp,
            "consecutive_failures": self.circuit_breaker.consecutive_failures,
            "cooldown_remaining_sec": self.circuit_breaker.cooldown_remaining(),
            "last_latency_sec": (
                round(self.last_latency, 3) if self.last_latency is not None else None
            ),
            "last_ttft_sec": (round(self.last_ttft, 3) if self.last_ttft is not None else None),
        }


# Global singleton tracker
_GLOBAL_CLOUD_HEALTH: CloudHealth | None = None


def get_cloud_health(has_key: bool = False, secrets_path: Path | None = None) -> CloudHealth:
    """Return or initialize the shared CloudHealth tracker."""
    global _GLOBAL_CLOUD_HEALTH
    if _GLOBAL_CLOUD_HEALTH is None:
        _GLOBAL_CLOUD_HEALTH = CloudHealth(has_key=has_key, secrets_path=secrets_path)
    elif secrets_path is not None and _GLOBAL_CLOUD_HEALTH.secrets_path != secrets_path:
        _GLOBAL_CLOUD_HEALTH.secrets_path = secrets_path
    return _GLOBAL_CLOUD_HEALTH


def reset_cloud_health(has_key: bool = False, secrets_path: Path | None = None) -> CloudHealth:
    """Reset the global CloudHealth tracker (primarily for testing)."""
    global _GLOBAL_CLOUD_HEALTH
    _GLOBAL_CLOUD_HEALTH = CloudHealth(has_key=has_key, secrets_path=secrets_path)
    return _GLOBAL_CLOUD_HEALTH
