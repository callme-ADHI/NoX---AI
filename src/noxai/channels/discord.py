"""Discord channel for NoX supporting channel ingestion into social digest and guarded posting."""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime
from typing import Any

import httpx

from noxai.channels.base import BaseChannel
from noxai.db import get_db
from noxai.security.approvals import ApprovalsCenter
from noxai.security.audit import log_audit_event
from noxai.security.secrets import SecretStore

log = logging.getLogger(__name__)

DISCORD_API_BASE = "https://discord.com/api/v10"


class DiscordChannel(BaseChannel):
    """Discord bot channel connector for server ingestion and guarded posting."""

    def __init__(
        self,
        name: str = "discord",
        config: dict[str, Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(name=name, channel_type="discord", config=config or {})
        self.bot_token: str = self.config.get("bot_token", "")
        self.default_channel_id: str = self.config.get("channel_id", "")
        self._custom_transport = transport

    def _get_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._custom_transport,
            timeout=30.0,
        )

    def get_token(self) -> str:
        secret = SecretStore.get(f"channel:{self.name}:bot_token")
        return secret or self.bot_token

    def set_token(self, token: str) -> None:
        self.bot_token = token
        SecretStore.set(f"channel:{self.name}:bot_token", token)

    async def health(self) -> dict[str, Any]:
        token = self.get_token()
        if not token:
            return {"status": "NOT_CONFIGURED", "reason": "No Discord bot token configured"}
        try:
            headers = {"Authorization": f"Bot {token}"}
            async with self._get_client() as client:
                resp = await client.get(f"{DISCORD_API_BASE}/users/@me", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    return {"status": "ok", "reason": f"Connected as {data.get('username')}"}
                return {"status": "FAILED", "reason": f"Discord error {resp.status_code}"}
        except Exception as exc:
            return {"status": "FAILED", "reason": str(exc)}

    async def ingest_message(
        self,
        channel_id: str,
        author_name: str,
        content: str,
        timestamp: str,
    ) -> None:
        """Scan and store incoming Discord message in social_items."""
        clean_text = self.sanitize_incoming_input(content)
        item_id = f"discord_{secrets.token_hex(6)}"
        now = datetime.now(UTC).isoformat()

        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO social_items (
                    id, source, channel_or_feed, author, title,
                    content_sanitized, url, published_at, created_at
                ) VALUES (?, 'discord', ?, ?, NULL, ?, NULL, ?, ?)
                """,
                (item_id, channel_id, author_name, clean_text, timestamp, now),
            )
            await db.commit()

    async def send_message(
        self,
        recipient_id: str,
        text: str,
        guarded: bool = True,
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        """Post message to Discord channel with guarded approval check."""
        token = self.get_token()
        target_channel = recipient_id or self.default_channel_id

        if guarded:
            if not approval_id:
                appr = await ApprovalsCenter.request_approval(
                    action_type="discord_post",
                    payload={"channel_id": target_channel, "content": text},
                    preview=f"Post to Discord channel {target_channel}: {text[:80]}",
                    scope="discord",
                )
                return {
                    "status": "pending_approval",
                    "approval_id": appr.id,
                    "confirmation_code": appr.confirmation_code,
                }

            consumed = await ApprovalsCenter.verify_and_consume(
                approval_id,
                "discord_post",
                {"channel_id": target_channel, "content": text},
            )
            if not consumed:
                raise PermissionError("Approval invalid or expired for Discord post.")

        _is_safe, sanitized_text, _ = self.check_outgoing_privacy(text)
        headers = {"Authorization": f"Bot {token}"}
        async with self._get_client() as client:
            resp = await client.post(
                f"{DISCORD_API_BASE}/channels/{target_channel}/messages",
                headers=headers,
                json={"content": sanitized_text},
            )
            if resp.status_code not in (200, 201):
                raise RuntimeError(f"Discord post failed: {resp.status_code} {resp.text}")

            data = resp.json()
            await log_audit_event(
                actor="user",
                action="discord_post",
                target=target_channel,
                decision="executed",
                summary_redacted=f"Posted to Discord {target_channel}",
            )
            return {"status": "sent", "id": data.get("id")}

    async def start(self) -> None:
        await self.load_state_from_db()

    async def stop(self) -> None:
        pass
