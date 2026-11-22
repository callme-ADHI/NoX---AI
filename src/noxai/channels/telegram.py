"""Telegram bot channel for NoX using long-polling with strict owner pairing.

Enforces:
- One-time owner pairing code flow.
- Allow-list for trusted chat IDs; default rejection of unauthorized users.
- Commands: /status, /briefing, /approve, /panic.
- Privacy egress gate preventing PII and secret leaks over Telegram.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from noxai.channels.base import BaseChannel
from noxai.connectors.mail_profile import DigestGenerator
from noxai.security.approvals import ApprovalsCenter
from noxai.security.audit import log_audit_event
from noxai.security.kill_switch import KillSwitch
from noxai.security.secrets import SecretStore

log = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramChannel(BaseChannel):
    """Telegram bot channel with long-polling and owner authentication."""

    def __init__(
        self,
        name: str = "telegram",
        config: dict[str, Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(name=name, channel_type="telegram", config=config or {})
        self.bot_token: str = self.config.get("token", "")
        self._custom_transport = transport
        self._running = False
        self._stop_event = asyncio.Event()
        self._poll_task: asyncio.Task[None] | None = None

    def _get_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._custom_transport,
            timeout=35.0,
        )

    def get_token(self) -> str:
        secret = SecretStore.get(f"channel:{self.name}:token")
        return secret or self.bot_token

    def set_token(self, token: str) -> None:
        self.bot_token = token
        SecretStore.set(f"channel:{self.name}:token", token)

    async def _api_call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        token = self.get_token()
        if not token:
            raise RuntimeError("Telegram bot token not configured.")
        url = f"{TELEGRAM_API_BASE}/bot{token}/{method}"
        async with self._get_client() as client:
            resp = await client.post(url, json=params or {})
            if resp.status_code != 200:
                raise RuntimeError(f"Telegram API {method} failed: {resp.status_code} {resp.text}")
            res_json: dict[str, Any] = resp.json()
            return res_json

    async def health(self) -> dict[str, Any]:
        token = self.get_token()
        if not token:
            return {"status": "NOT_CONFIGURED", "reason": "No Telegram bot token configured"}
        try:
            res = await self._api_call("getMe")
            if res.get("ok"):
                username = res.get("result", {}).get("username", "")
                return {"status": "ok", "reason": f"Connected as @{username}"}
            return {"status": "FAILED", "reason": "getMe returned not ok"}
        except Exception as exc:
            return {"status": "FAILED", "reason": str(exc)}

    async def send_message(
        self,
        recipient_id: str,
        text: str,
        guarded: bool = True,
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        """Send message through Telegram bot with privacy egress filtering."""
        if not self.is_authorized(recipient_id):
            raise PermissionError(f"Recipient {recipient_id} is not authorized.")

        # Privacy Egress Gate
        is_safe, sanitized_text, reasons = self.check_outgoing_privacy(text)
        if not is_safe:
            log.warning("telegram_outgoing_privacy_blocked reasons=%s", reasons)
            sanitized_text = (
                "[Notice: Raw response contained sensitive credentials/PII and was redacted "
                "for privacy]\n\n" + sanitized_text
            )

        res = await self._api_call(
            "sendMessage",
            {"chat_id": recipient_id, "text": sanitized_text, "parse_mode": "Markdown"},
        )
        await log_audit_event(
            actor="user",
            action="telegram_send",
            target=recipient_id,
            decision="executed",
            summary_redacted=f"Telegram message sent to {recipient_id}",
        )
        return {"status": "sent", "message_id": res.get("result", {}).get("message_id")}

    async def handle_update(self, update: dict[str, Any]) -> None:
        """Process an incoming update from Telegram."""
        msg = update.get("message")
        if not msg:
            return

        chat_id = str(msg.get("chat", {}).get("id", ""))
        text = str(msg.get("text", "")).strip()
        if not chat_id or not text:
            return

        # Sanitize incoming text with InputScanner
        clean_text = self.sanitize_incoming_input(text)

        # 1. Pairing command
        if clean_text.startswith("/pair"):
            parts = clean_text.split()
            if len(parts) >= 2:
                entered_code = parts[1]
                paired = await self.verify_pairing_attempt(chat_id, entered_code)
                if paired:
                    await self.send_message(chat_id, "✓ Successfully paired with NoX as owner!")
                    return
                else:
                    await self._api_call(
                        "sendMessage",
                        {
                            "chat_id": chat_id,
                            "text": (
                                "✗ Invalid pairing code. Run `nx channel telegram --pair` "
                                "to see your code."
                            ),
                        },
                    )
                    return

        # 2. Authorization check
        if not self.is_authorized(chat_id):
            if not self.owner_id:
                reply = (
                    "🔒 NoX Assistant: Bot is not paired.\n"
                    "Run `nx channel telegram --pair` on your Arch host to view your 6-digit code, "
                    "then send `/pair <code>` here."
                )
            else:
                reply = "⛔ Unauthorized user. This NoX instance is locked to its owner."
                await log_audit_event(
                    actor="channel",
                    action="telegram_unauthorized_access",
                    target=chat_id,
                    decision="denied",
                    summary_redacted=f"Rejected message from unknown chat_id {chat_id}",
                )
            await self._api_call("sendMessage", {"chat_id": chat_id, "text": reply})
            return

        # 3. Authorized command routing
        if clean_text == "/status":
            h = await self.health()
            reply = (
                f"🤖 NoX System Status:\n"
                f"• Daemon: Online\n"
                f"• Telegram: {h.get('status')}\n"
                f"• Owner: {self.owner_id}"
            )
            await self.send_message(chat_id, reply)

        elif clean_text == "/briefing":
            generator = DigestGenerator()
            data = await generator.generate_digest(period="morning")
            lines = [
                f"📋 *Daily Briefing ({data['period'].title()})*",
                f"• Total emails (24h): {data['total_emails_24h']}",
                f"• Upcoming events: {data['calendar_events_count']}",
            ]
            for ev in data.get("calendar_events", [])[:3]:
                lines.append(f"  - {ev['title']} ({ev['start_time'][:16]})")
            for act in data.get("action_items", [])[:3]:
                lines.append(f"  ⚠️ Action: {act['item']}")
            await self.send_message(chat_id, "\n".join(lines))

        elif clean_text.startswith("/approve"):
            parts = clean_text.split()
            if len(parts) >= 2:
                appr_id = parts[1]
                conf_code = parts[2] if len(parts) >= 3 else None
                try:
                    await ApprovalsCenter.approve(
                        appr_id, actor="telegram", confirmation_code=conf_code
                    )
                    await self.send_message(chat_id, f"✓ Approved action {appr_id}")
                except Exception as exc:
                    await self.send_message(chat_id, f"✗ Approval failed: {exc}")
            else:
                await self.send_message(
                    chat_id, "Usage: `/approve <approval_id> [confirmation_code]`"
                )

        elif clean_text == "/panic":
            await KillSwitch.engage(actor="telegram", reason="Triggered via Telegram /panic")
            await self.send_message(
                chat_id,
                "🚨 *EMERGENCY KILL SWITCH ENGAGED*\n"
                "All autonomous agents, connectors, and approvals revoked.",
            )

        else:
            # Echo or simple reply with privacy check
            await self.send_message(
                chat_id,
                f"Received: {clean_text}\n(Type /status, /briefing, /approve, or /panic)",
            )

    async def _poll_loop(self) -> None:
        offset = 0
        while self._running and not self._stop_event.is_set():
            try:
                res = await self._api_call("getUpdates", {"offset": offset, "timeout": 20})
                updates = res.get("result", [])
                for upd in updates:
                    offset = max(offset, upd["update_id"] + 1)
                    await self.handle_update(upd)
            except Exception as exc:
                log.warning("telegram_poll_error error=%s", exc)
                await asyncio.sleep(5)

    async def start(self) -> None:
        await self.load_state_from_db()
        self._running = True
        self._stop_event.clear()
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
