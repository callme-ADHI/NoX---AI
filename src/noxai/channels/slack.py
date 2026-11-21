"""Slack channel for NoX supporting message ingestion into social digest and guarded posting."""

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

SLACK_API_BASE = "https://slack.com/api"


class SlackChannel(BaseChannel):
    """Slack channel connector for team ingestion and guarded message posting."""

    def __init__(
        self,
        name: str = "slack",
        config: dict[str, Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(name=name, channel_type="slack", config=config or {})
        self.bot_token: str = self.config.get("bot_token", "")
        self.default_channel: str = self.config.get("channel", "general")
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
            return {"status": "degraded", "reason": "No Slack bot token"}
        try:
            headers = {"Authorization": f"Bearer {token}"}
            async with self._get_client() as client:
                resp = await client.post(f"{SLACK_API_BASE}/auth.test", headers=headers)
                data = resp.json()
                if data.get("ok"):
                    return {"status": "ok", "reason": f"Connected as {data.get('user')}"}
                return {"status": "error", "reason": data.get("error", "auth failed")}
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}

    async def ingest_message(
        self,
        channel_id: str,
        user_id: str,
        text: str,
        message_ts: str,
    ) -> None:
        """Scan and store incoming Slack message in social_items."""
        clean_text = self.sanitize_incoming_input(text)
        item_id = f"slack_{secrets.token_hex(6)}"
        now = datetime.now(UTC).isoformat()

        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO social_items (
                    id, source, channel_or_feed, author, title,
                    content_sanitized, url, published_at, created_at
                ) VALUES (?, 'slack', ?, ?, NULL, ?, NULL, ?, ?)
                """,
                (item_id, channel_id, user_id, clean_text, message_ts, now),
            )
            await db.commit()

    async def send_message(
        self,
        recipient_id: str,
        text: str,
        guarded: bool = True,
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        """Post message to Slack. Requires approval if guarded."""
        token = self.get_token()
        target_channel = recipient_id or self.default_channel

        if guarded:
            if not approval_id:
                appr = await ApprovalsCenter.request_approval(
                    action_type="slack_post",
                    payload={"channel": target_channel, "text": text},
                    preview=f"Post to Slack #{target_channel}: {text[:80]}",
                    scope="slack",
                )
                return {
                    "status": "pending_approval",
                    "approval_id": appr.id,
                    "confirmation_code": appr.confirmation_code,
                }

            consumed = await ApprovalsCenter.verify_and_consume(
                approval_id,
                "slack_post",
                {"channel": target_channel, "text": text},
            )
            if not consumed:
                raise PermissionError("Approval invalid or expired for Slack post.")

        _is_safe, sanitized_text, _ = self.check_outgoing_privacy(text)
        headers = {"Authorization": f"Bearer {token}"}
        async with self._get_client() as client:
            resp = await client.post(
                f"{SLACK_API_BASE}/chat.postMessage",
                headers=headers,
                json={"channel": target_channel, "text": sanitized_text},
            )
            data = resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Slack postMessage failed: {data.get('error')}")

            await log_audit_event(
                actor="user",
                action="slack_post",
                target=target_channel,
                decision="executed",
                summary_redacted=f"Posted to Slack #{target_channel}",
            )
            return {"status": "sent", "ts": data.get("ts")}

    async def start(self) -> None:
        await self.load_state_from_db()

    async def stop(self) -> None:
        pass
