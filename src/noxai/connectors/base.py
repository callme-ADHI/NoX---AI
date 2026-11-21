"""Base connector abstraction and connector registry for NoX.

All external connectors (Gmail, IMAP, Calendar, Telegram, Slack, GitHub, Feeds)
inherit from ``BaseConnector``. Enforces rate limiting, exponential backoff,
health tracking, privacy classification, and guarded write actions.
"""

from __future__ import annotations

import abc
import asyncio
import json
import logging
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, TypeVar

from noxai.db import get_db

log = logging.getLogger(__name__)

CONNECTORS_CONFIG_PATH = Path("~/.config/nox/connectors.toml").expanduser()

T = TypeVar("T")


class RateLimiter:
    """Simple asynchronous token-bucket / interval rate limiter."""

    def __init__(self, requests_per_minute: int = 60) -> None:
        self.rpm = max(1, requests_per_minute)
        self.interval = 60.0 / self.rpm
        self._last_request_time = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = asyncio.get_running_loop().time()
            elapsed = now - self._last_request_time
            if elapsed < self.interval:
                await asyncio.sleep(self.interval - elapsed)
            self._last_request_time = asyncio.get_running_loop().time()


class BaseConnector(abc.ABC):
    """Abstract base class for all external service connectors."""

    def __init__(
        self,
        name: str,
        connector_type: str,
        privacy_class: str = "local_only",
        config: dict[str, Any] | None = None,
        rate_limit_rpm: int = 60,
    ) -> None:
        self.name = name
        self.connector_type = connector_type
        self.privacy_class = privacy_class
        self.config = config or {}
        self.limiter = RateLimiter(rate_limit_rpm)

    @abc.abstractmethod
    async def auth(self) -> bool:
        """Authenticate or verify existing credentials for this connector."""
        ...

    @abc.abstractmethod
    async def health(self) -> dict[str, Any]:
        """Check connector health.

        Returns:
            dict containing:
                - status: 'ok' | 'degraded' | 'error'
                - reason: explanatory string
                - last_sync_at: ISO timestamp or None
        """
        ...

    @abc.abstractmethod
    async def sync(self) -> dict[str, Any]:
        """Perform a synchronization round with the remote service."""
        ...

    @abc.abstractmethod
    async def fetch(self, query: str | None = None, limit: int = 50) -> list[Any]:
        """Fetch items matching query from local store or remote."""
        ...

    @abc.abstractmethod
    async def search(self, query: str, limit: int = 20) -> list[Any]:
        """Search items in local store or remote service."""
        ...

    @abc.abstractmethod
    async def guarded_write(
        self,
        action: str,
        payload: dict[str, Any],
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a mutating write action subject to human approval.

        If approval_id is not given, creates a pending approval in ApprovalsCenter
        and returns status="pending_approval".
        """
        ...

    async def retry_with_backoff(
        self,
        func: Callable[..., Any],
        *args: Any,
        max_retries: int = 3,
        base_delay: float = 1.0,
        **kwargs: Any,
    ) -> Any:
        """Execute func with exponential backoff and rate limiting."""
        attempt = 0
        last_exc: Exception | None = None
        while attempt < max_retries:
            await self.limiter.acquire()
            try:
                return await func(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                attempt += 1
                if attempt >= max_retries:
                    break
                delay = base_delay * (2 ** (attempt - 1))
                log.warning(
                    "connector_retry connector=%s attempt=%d/%d delay=%.2fs exc=%s",
                    self.name,
                    attempt,
                    max_retries,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
        if last_exc:
            raise last_exc
        raise RuntimeError("retry_with_backoff exhausted")

    async def update_health(
        self,
        status: str,
        reason: str = "",
        error: str | None = None,
    ) -> None:
        """Persist health status update to database."""
        now = datetime.now(UTC).isoformat()
        async with get_db() as db:
            await db.execute(
                """
                UPDATE connectors
                SET health_status = ?, health_reason = ?, last_error = ?, updated_at = ?
                WHERE name = ?
                """,
                (status, reason, error, now, self.name),
            )
            await db.commit()

    async def mark_sync(self, status: str = "ok", reason: str = "") -> None:
        """Record successful sync timestamp and health."""
        now = datetime.now(UTC).isoformat()
        async with get_db() as db:
            await db.execute(
                """
                UPDATE connectors
                SET health_status = ?, health_reason = ?, last_sync_at = ?,
                    last_error = NULL, updated_at = ?
                WHERE name = ?
                """,
                (status, reason, now, now, self.name),
            )
            await db.commit()

    async def register_in_db(self) -> None:
        """Ensure this connector has a row in the connectors table."""
        now = datetime.now(UTC).isoformat()
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO connectors (
                    id, name, type, enabled, config_json, health_status,
                    health_reason, privacy_class, created_at, updated_at
                ) VALUES (?, ?, ?, 1, ?, 'ok', 'initialized', ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    type = excluded.type,
                    config_json = excluded.config_json,
                    privacy_class = excluded.privacy_class,
                    updated_at = excluded.updated_at
                """,
                (
                    f"conn_{self.name}",
                    self.name,
                    self.connector_type,
                    json.dumps(self.config),
                    self.privacy_class,
                    now,
                    now,
                ),
            )
            await db.commit()


class ConnectorRegistry:
    """Registry managing available connectors and configuration."""

    _connectors: ClassVar[dict[str, BaseConnector]] = {}

    @classmethod
    def register(cls, connector: BaseConnector) -> None:
        cls._connectors[connector.name] = connector

    @classmethod
    def get(cls, name: str) -> BaseConnector | None:
        return cls._connectors.get(name)

    @classmethod
    def list_all(cls) -> list[BaseConnector]:
        return list(cls._connectors.values())

    @classmethod
    def load_config_file(cls, path: Path | None = None) -> dict[str, Any]:
        cfg_path = path or CONNECTORS_CONFIG_PATH
        if not cfg_path.exists():
            return {}
        try:
            with open(cfg_path, "rb") as f:
                return tomllib.load(f)
        except Exception as e:
            log.error("failed_to_load_connectors_toml path=%s error=%s", cfg_path, e)
            return {}

    @classmethod
    async def list_db_connectors(cls) -> list[dict[str, Any]]:
        async with get_db() as db:
            async with db.execute(
                """
                SELECT id, name, type, enabled, config_json, health_status,
                       health_reason, last_sync_at, last_error, privacy_class
                FROM connectors ORDER BY name
                """
            ) as cur:
                rows = await cur.fetchall()
                results = []
                for r in rows:
                    results.append(
                        {
                            "id": r[0],
                            "name": r[1],
                            "type": r[2],
                            "enabled": bool(r[3]),
                            "config": json.loads(r[4] or "{}"),
                            "health_status": r[5],
                            "health_reason": r[6],
                            "last_sync_at": r[7],
                            "last_error": r[8],
                            "privacy_class": r[9],
                        }
                    )
                return results

    @classmethod
    async def set_enabled(cls, name: str, enabled: bool) -> bool:
        async with get_db() as db:
            cur = await db.execute(
                "UPDATE connectors SET enabled = ?, updated_at = ? WHERE name = ?",
                (1 if enabled else 0, datetime.now(UTC).isoformat(), name),
            )
            await db.commit()
            return cur.rowcount > 0

    @classmethod
    async def sync_all_active(cls) -> dict[str, Any]:
        """Run a non-blocking background sync round across all enabled connectors."""
        results: dict[str, Any] = {}
        db_conns = await cls.list_db_connectors()
        for item in db_conns:
            if not item["enabled"]:
                continue
            name = item["name"]
            connector = cls.get(name)
            if not connector:
                continue
            try:
                res = await connector.sync()
                results[name] = {"status": "ok", "result": res}
            except Exception as exc:
                log.error("connector_sync_failed name=%s error=%s", name, exc)
                results[name] = {"status": "error", "error": str(exc)}
                await connector.update_health("error", reason=str(exc), error=str(exc))
        return results
