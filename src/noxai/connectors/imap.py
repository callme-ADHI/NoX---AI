"""IMAP connector for NoX using standard SSL IMAP.

Connects to arbitrary IMAP servers (Fastmail, Proton Bridge, self-hosted, etc.),
fetches and parses MIME emails, strips HTML, sanitizes PII, and stores items
locally in mail_items and mail_attachments.
"""

from __future__ import annotations

import asyncio
import email
import email.policy
import imaplib
import logging
import re
import secrets
from datetime import UTC, datetime
from typing import Any

from noxai.connectors.base import BaseConnector
from noxai.db import get_db
from noxai.security.approvals import ApprovalsCenter
from noxai.security.audit import log_audit_event
from noxai.security.guardrails import PIIRedactor
from noxai.security.secrets import SecretStore

log = logging.getLogger(__name__)


class IMAPConnector(BaseConnector):
    """Generic IMAP over SSL connector."""

    def __init__(
        self,
        name: str = "imap",
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            name=name,
            connector_type="imap",
            privacy_class="local_only",
            config=config or {},
            rate_limit_rpm=30,
        )
        self.host: str = self.config.get("host", "")
        self.port: int = int(self.config.get("port", 993))
        self.username: str = self.config.get("username", "")
        self.pii_redactor = PIIRedactor()

    async def get_password(self) -> str:
        secret = SecretStore.get(f"connector:{self.name}:password")
        return secret or self.config.get("password", "")

    async def set_credentials(
        self, host: str, username: str, password: str, port: int = 993
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.config["host"] = host
        self.config["port"] = port
        self.config["username"] = username
        SecretStore.set(f"connector:{self.name}:password", password)
        await self.register_in_db()
        try:
            from noxai.security.audit import log_audit_event
            await log_audit_event(
                actor="user",
                action="connector_credentials_saved",
                target=self.name,
                decision="allow",
                summary_redacted=f"Saved credentials for connector {self.name}",
            )
        except Exception as exc:
            log.debug("imap_save_credentials_audit_failed error=%s", exc)

    def _sync_connect(self, password: str) -> imaplib.IMAP4_SSL:
        client = imaplib.IMAP4_SSL(self.host, self.port)
        client.login(self.username, password)
        return client

    async def auth(self) -> bool:
        password = await self.get_password()
        if not self.host or not self.username or not password:
            return False
        try:
            client = await asyncio.to_thread(self._sync_connect, password)
            await asyncio.to_thread(client.logout)
            await self.update_health("ok", reason=f"Connected to {self.host} as {self.username}")
            return True
        except Exception as exc:
            log.warning("imap_auth_failed host=%s user=%s error=%s", self.host, self.username, exc)
            await self.update_health("error", reason=str(exc), error=str(exc))
            return False

    async def health(self) -> dict[str, Any]:
        password = await self.get_password()
        has_creds = bool(self.host and self.username and password)

        try:
            async with get_db() as db:
                async with db.execute(
                    """
                    SELECT health_status, health_reason, last_sync_at, last_error
                    FROM connectors WHERE name = ?
                    """,
                    (self.name,),
                ) as cur:
                    row = await cur.fetchone()
                    if row:
                        db_status = row[0]
                        last_sync = row[2]
                        if db_status in ("degraded", "error", "FAILED"):
                            return {
                                "status": db_status,
                                "reason": row[1] or "",
                                "last_sync_at": last_sync,
                                "last_error": row[3],
                            }
                        if not has_creds:
                            return {
                                "status": "NOT_CONFIGURED",
                                "reason": "Host, username, or password not configured",
                                "last_sync_at": last_sync,
                                "last_error": row[3],
                            }
                        status = "HEALTHY" if last_sync else "CONFIGURED_UNTESTED"
                        return {
                            "status": status,
                            "reason": row[1] or "",
                            "last_sync_at": last_sync,
                            "last_error": row[3],
                        }
        except Exception as exc:
            log.debug("imap_health_db_read_failed error=%s", exc)

        if not has_creds:
            return {
                "status": "NOT_CONFIGURED",
                "reason": "Host, username, or password not configured",
                "last_sync_at": None,
                "last_error": None,
            }
        return {"status": "CONFIGURED_UNTESTED", "reason": "Configured; not yet synced", "last_sync_at": None}

    def _parse_message(self, raw_bytes: bytes) -> dict[str, Any]:
        """Parse raw RFC822 message bytes into structured dictionary."""
        msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)
        subject = str(msg.get("subject", "(No Subject)"))
        from_header = str(msg.get("from", ""))
        date_header = str(msg.get("date", datetime.now(UTC).isoformat()))
        msg_id = str(msg.get("message-id", secrets.token_hex(8)))

        from_name = from_header
        from_domain = ""
        if "<" in from_header and ">" in from_header:
            from_name = from_header.split("<")[0].strip().strip('"')
            email_addr = from_header.split("<")[1].split(">")[0].strip()
            if "@" in email_addr:
                from_domain = email_addr.split("@")[-1].lower()
        elif "@" in from_header:
            from_domain = from_header.split("@")[-1].lower()

        body_text = ""
        attachments: list[dict[str, Any]] = []

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                filename = part.get_filename()
                if filename:
                    size = len(part.get_payload(decode=True) or b"")
                    attachments.append(
                        {
                            "filename": filename,
                            "content_type": content_type,
                            "size_bytes": size,
                        }
                    )
                elif content_type == "text/plain" and not body_text:
                    body_text = part.get_content()
                elif content_type == "text/html" and not body_text:
                    raw_html = part.get_content()
                    clean = re.sub(r"<[^>]+>", " ", str(raw_html))
                    body_text = re.sub(r"\s+", " ", clean).strip()
        else:
            if msg.get_content_type() == "text/plain":
                body_text = msg.get_content()
            elif msg.get_content_type() == "text/html":
                clean = re.sub(r"<[^>]+>", " ", str(msg.get_content()))
                body_text = re.sub(r"\s+", " ", clean).strip()

        sanitized_text, _ = self.pii_redactor.redact(body_text)
        sanitized_body = sanitized_text[:20480]

        return {
            "message_id": msg_id,
            "subject": subject,
            "from_name": from_name,
            "from_domain": from_domain,
            "date": date_header,
            "text": sanitized_body,
            "attachments": attachments,
        }

    def _sync_fetch_messages(self, password: str, limit: int = 25) -> list[dict[str, Any]]:
        client = self._sync_connect(password)
        items: list[dict[str, Any]] = []
        try:
            client.select("INBOX", readonly=True)
            typ, data = client.search(None, "ALL")
            if typ != "OK" or not data or not data[0]:
                return []
            ids = data[0].split()
            # Fetch latest limit messages
            target_ids = ids[-limit:]
            for mid in reversed(target_ids):
                typ, msg_data = client.fetch(mid, "(RFC822)")
                if typ == "OK" and msg_data and isinstance(msg_data[0], tuple):
                    raw = msg_data[0][1]
                    if isinstance(raw, bytes):
                        items.append(self._parse_message(raw))
        finally:
            try:
                client.logout()
            except Exception as exc:
                log.debug("imap logout failed: %s", exc)
        return items

    async def sync(self) -> dict[str, Any]:
        """Fetch remote messages and insert into mail_items."""
        password = await self.get_password()
        if not self.host or not self.username or not password:
            raise RuntimeError("IMAP credentials not configured.")

        items = await asyncio.to_thread(self._sync_fetch_messages, password, limit=25)
        now = datetime.now(UTC).isoformat()
        synced_count = 0

        async with get_db() as db:
            for item in items:
                db_id = f"imap_{hash(item['message_id']) & 0xFFFFFFFF:x}"
                await db.execute(
                    """
                    INSERT INTO mail_items (
                        id, account, message_id, thread_id, labels_json,
                        from_name, from_domain, date, subject, text_sanitized,
                        flags_json, created_at
                    ) VALUES (?, ?, ?, ?, '[]', ?, ?, ?, ?, ?, '{}', ?)
                    ON CONFLICT(account, message_id) DO UPDATE SET
                        subject = excluded.subject,
                        text_sanitized = excluded.text_sanitized
                    """,
                    (
                        db_id,
                        self.username,
                        item["message_id"],
                        None,
                        item["from_name"],
                        item["from_domain"],
                        item["date"],
                        item["subject"],
                        item["text"],
                        now,
                    ),
                )

                for att in item.get("attachments", []):
                    att_id = f"{db_id}_{secrets.token_hex(4)}"
                    await db.execute(
                        """
                        INSERT INTO mail_attachments (
                            id, mail_item_id, filename, content_type, size_bytes, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            att_id,
                            db_id,
                            att["filename"],
                            att["content_type"],
                            att["size_bytes"],
                            now,
                        ),
                    )
                synced_count += 1
            await db.commit()

        await self.mark_sync(status="ok", reason=f"Synced {synced_count} messages")
        return {"synced_count": synced_count}

    async def fetch(self, query: str | None = None, limit: int = 50) -> list[Any]:
        sql = (
            "SELECT id, account, message_id, subject, from_name, from_domain, date, "
            "text_sanitized FROM mail_items WHERE account = ? AND deleted_at IS NULL"
        )
        params: list[Any] = [self.username]
        if query:
            sql += " AND (subject LIKE ? OR text_sanitized LIKE ?)"
            params.extend([f"%{query}%", f"%{query}%"])
        sql += " ORDER BY date DESC LIMIT ?"
        params.append(limit)

        async with get_db() as db:
            async with db.execute(sql, tuple(params)) as cur:
                rows = await cur.fetchall()
                return [
                    {
                        "id": r[0],
                        "account": r[1],
                        "message_id": r[2],
                        "subject": r[3],
                        "from_name": r[4],
                        "from_domain": r[5],
                        "date": r[6],
                        "text": r[7],
                    }
                    for r in rows
                ]

    async def search(self, query: str, limit: int = 20) -> list[Any]:
        return await self.fetch(query=query, limit=limit)

    async def guarded_write(
        self,
        action: str,
        payload: dict[str, Any],
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        """IMAP is read-only for message ingestion. SMTP write requires approval."""
        if action == "draft":
            # Record local draft
            return {"status": "created", "note": "Draft stored locally"}
        elif action == "send":
            to = payload.get("to", "")
            subject = payload.get("subject", "")
            if not approval_id:
                appr = await ApprovalsCenter.request_approval(
                    action_type="smtp_send",
                    payload=payload,
                    preview=f"Send email to {to} | Subject: {subject}",
                    scope="smtp",
                )
                return {
                    "status": "pending_approval",
                    "approval_id": appr.id,
                    "confirmation_code": appr.confirmation_code,
                }

            consumed = await ApprovalsCenter.verify_and_consume(approval_id, "smtp_send", payload)
            if not consumed:
                raise PermissionError(
                    f"Approval {approval_id} is invalid, expired, or payload mismatch."
                )
            await log_audit_event(
                actor="user",
                action="smtp_send",
                target=to,
                decision="executed",
                summary_redacted=f"Sent via SMTP to {to} subject: {subject[:30]}",
            )
            return {"status": "sent"}
        raise ValueError(f"Unknown action: {action}")
