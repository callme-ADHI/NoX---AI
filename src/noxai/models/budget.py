"""Token-bucket request budget manager for cloud model providers (§1.3, §1.5).

Enforces requests-per-minute limits (default ~30 RPM for NVIDIA free tier)
with a reserved share for interactive front-desk turns to ensure background
tasks and batch queries never starve user chat turns.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

log = logging.getLogger(__name__)


class RequestBudget:
    """Token-bucket budget manager with interactive reservation.

    Args:
        requests_per_minute: Total RPM capacity (default 30).
        reserved_interactive: Number of tokens reserved exclusively for
                              interactive user turns (default 10).
    """

    def __init__(
        self,
        requests_per_minute: int = 30,
        reserved_interactive: int = 10,
    ) -> None:
        self.capacity = float(max(1, requests_per_minute))
        self.reserved_interactive = float(max(0, min(reserved_interactive, int(self.capacity - 1))))
        self.rate = self.capacity / 60.0  # tokens added per second
        self.tokens = self.capacity
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()
        self.total_interactive = 0
        self.total_background = 0
        self.total_rejected = 0

    def _replenish(self, now: float) -> None:
        """Add tokens accrued since last update, up to capacity."""
        elapsed = now - self.last_update
        self.last_update = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)

    async def acquire(self, is_interactive: bool = True, timeout: float = 0.0) -> bool:
        """Attempt to acquire a request token from the budget.

        Args:
            is_interactive: True for interactive front-desk chat; False for
                            background tasks or batch workflows.
            timeout: Maximum seconds to wait if tokens are currently depleted.

        Returns:
            True if a token was acquired; False if budget is exhausted or
            background request would violate the interactive reserve.
        """
        deadline = time.monotonic() + timeout

        while True:
            async with self._lock:
                now = time.monotonic()
                self._replenish(now)

                if is_interactive:
                    # Interactive turns can use all available tokens down to 0
                    if self.tokens >= 1.0:
                        self.tokens -= 1.0
                        self.total_interactive += 1
                        return True
                else:
                    # Background turns can only consume if tokens >= reserved_interactive + 1.0
                    if self.tokens >= (self.reserved_interactive + 1.0):
                        self.tokens -= 1.0
                        self.total_background += 1
                        return True

                # If timeout is 0 or expired, return False immediately
                remaining_wait = deadline - now
                if remaining_wait <= 0:
                    self.total_rejected += 1
                    return False

                # Calculate sleep duration until at least 1 token is ready
                if is_interactive:
                    needed = 1.0 - self.tokens
                else:
                    needed = (self.reserved_interactive + 1.0) - self.tokens
                sleep_sec = min(remaining_wait, max(0.05, needed / self.rate))

            await asyncio.sleep(sleep_sec)

    def remaining_tokens(self, is_interactive: bool = True) -> float:
        """Calculate currently available tokens without modifying state."""
        now = time.monotonic()
        elapsed = now - self.last_update
        current = min(self.capacity, self.tokens + elapsed * self.rate)
        if is_interactive:
            return round(current, 2)
        return round(max(0.0, current - self.reserved_interactive), 2)

    def to_dict(self) -> dict[str, Any]:
        """Return diagnostic statistics."""
        now = time.monotonic()
        elapsed = now - self.last_update
        current = min(self.capacity, self.tokens + elapsed * self.rate)
        return {
            "capacity_rpm": int(self.capacity),
            "reserved_interactive_rpm": int(self.reserved_interactive),
            "tokens_current": round(current, 2),
            "interactive_available": round(current, 2),
            "background_available": round(max(0.0, current - self.reserved_interactive), 2),
            "total_interactive": self.total_interactive,
            "total_background": self.total_background,
            "total_rejected": self.total_rejected,
        }
