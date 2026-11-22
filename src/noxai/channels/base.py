"""Base channel abstraction and channel registry for NoX.

Handles channel pairing, allowlists, untrusted input scanning, output privacy
gates, and guarded message transmission for Telegram, Slack, and Discord.
"""

from __future__ import annotations

import abc
import json
import logging
import secrets
from datetime import UTC, datetime
from typing import Any, ClassVar

from noxai.db import get_db
from noxai.security.audit import log_audit_event
from noxai.security.guardrails import InputScanner, OutputScanner, PIIRedactor

log = logging.getLogger(__name__)


class BaseChannel(abc.ABC):
    """Abstract base class for external interactive messaging channels."""

    def __init__(
        self,
        name: str,
        channel_type: str,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.channel_type = channel_type
        self.config = config or {}
        self.owner_id: str | None = None
        self.allowlist: list[str] = []
        self.pairing_code: str | None = None
        self.pii_redactor = PIIRedactor()
        self.output_scanner = OutputScanner()

    @abc.abstractmethod
    async def start(self) -> None:
        """Start listening / polling for incoming messages."""
        ...

    @abc.abstractmethod
    async def stop(self) -> None:
        """Stop listening."""
        ...

    @abc.abstractmethod
    async def health(self) -> dict[str, Any]:
        """Check channel connection health."""
        ...

    @abc.abstractmethod
    async def send_message(
        self,
        recipient_id: str,
        text: str,
        guarded: bool = True,
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        """Send message out through channel subject to privacy and approval gates."""
        ...

    def generate_pairing_code(self) -> str:
        """Generate a cryptographically secure 6-digit numeric pairing code."""
        code = f"{secrets.randbelow(900000) + 100000}"
        self.pairing_code = code
        return code

    async def verify_pairing_attempt(self, sender_id: str, code: str) -> bool:
        """Verify pairing code and register sender as trusted owner."""
        if self.pairing_code and code.strip() == self.pairing_code:
            self.owner_id = sender_id
            if sender_id not in self.allowlist:
                self.allowlist.append(sender_id)
            self.pairing_code = None
            await self.save_state_to_db()
            await log_audit_event(
                actor="user",
                action="channel_paired",
                target=f"{self.name}:{sender_id}",
                decision="allowed",
                summary_redacted=f"Channel {self.name} paired with owner {sender_id}",
            )
            return True
        return False

    def is_authorized(self, sender_id: str) -> bool:
        """Check if sender is owner or on allowlist."""
        return sender_id == self.owner_id or sender_id in self.allowlist

    def sanitize_incoming_input(self, text: str) -> str:
        """Scan untrusted incoming message for prompt injection or hidden markers."""
        result = InputScanner.scan(text)
        if result.violations:
            log.warning(
                "channel_input_scanner_violations channel=%s violations=%s",
                self.name,
                result.violations,
            )
        return result.sanitized_text

    def check_outgoing_privacy(self, text: str) -> tuple[bool, str, list[str]]:
        """Verify text before transmitting over external channel.

        Returns: (is_safe, redacted_text, reasons)
        """
        # Redact any Indian PII or sensitive secrets
        redacted, _ = self.pii_redactor.redact(text)
        scan = self.output_scanner.scan(redacted)

        reasons = []
        if scan.violations:
            reasons.extend(scan.violations)
            return False, scan.sanitized_text, reasons

        return True, redacted, reasons

    async def save_state_to_db(self) -> None:
        """Persist channel state to channels table."""
        now = datetime.now(UTC).isoformat()
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO channels (
                    id, name, type, enabled, config_json, pairing_code,
                    owner_id, allowlist_json, health_status, health_reason, updated_at
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, 'ok', 'active', ?)
                ON CONFLICT(name) DO UPDATE SET
                    config_json = excluded.config_json,
                    pairing_code = excluded.pairing_code,
                    owner_id = excluded.owner_id,
                    allowlist_json = excluded.allowlist_json,
                    updated_at = excluded.updated_at
                """,
                (
                    f"chan_{self.name}",
                    self.name,
                    self.channel_type,
                    json.dumps(self.config),
                    self.pairing_code,
                    self.owner_id,
                    json.dumps(self.allowlist),
                    now,
                ),
            )
            await db.commit()

    async def load_state_from_db(self) -> None:
        """Load channel state from database if exists."""
        async with get_db() as db:
            async with db.execute(
                "SELECT pairing_code, owner_id, allowlist_json FROM channels WHERE name = ?",
                (self.name,),
            ) as cur:
                row = await cur.fetchone()
                if row:
                    self.pairing_code = row[0]
                    self.owner_id = row[1]
                    self.allowlist = json.loads(row[2] or "[]")


class ChannelRegistry:
    """Registry managing external communication channels."""

    _channels: ClassVar[dict[str, BaseChannel]] = {}

    @classmethod
    def register(cls, channel: BaseChannel) -> None:
        cls._channels[channel.name] = channel

    @classmethod
    def get(cls, name: str) -> BaseChannel | None:
        return cls._channels.get(name)

    @classmethod
    def list_all(cls) -> list[BaseChannel]:
        return list(cls._channels.values())

    @classmethod
    async def list_db_channels(cls) -> list[dict[str, Any]]:
        async with get_db() as db:
            async with db.execute(
                """
                SELECT id, name, type, enabled, owner_id, allowlist_json,
                       health_status, health_reason, last_seen_at
                FROM channels ORDER BY name
                """
            ) as cur:
                rows = await cur.fetchall()
                return [
                    {
                        "id": r[0],
                        "name": r[1],
                        "type": r[2],
                        "enabled": bool(r[3]),
                        "owner_id": r[4],
                        "allowlist": json.loads(r[5] or "[]"),
                        "health_status": r[6],
                        "health_reason": r[7],
                        "last_seen_at": r[8],
                    }
                    for r in rows
                ]
