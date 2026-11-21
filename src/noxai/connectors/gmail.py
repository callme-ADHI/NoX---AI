"""Gmail connector for NoX using Gmail REST API with OAuth PKCE flow.

Features:
- OAuth 2.0 PKCE installed-application authorization.
- Token refresh with secure storage in SecretStore.
- Read-only default scope, with opt-in compose and guarded send.
- PII redaction and secret scanning before storing into mail_items.
- Attachment metadata tracking in mail_attachments.
"""

from __future__ import annotations

import base64
import email
import hashlib
import json
import logging
import re
import secrets
import urllib.parse
from datetime import UTC, datetime
from typing import Any

import httpx

from noxai.connectors.base import BaseConnector
from noxai.db import get_db
from noxai.security.approvals import ApprovalsCenter
from noxai.security.audit import log_audit_event
from noxai.security.guardrails import OutputScanner, PIIRedactor
from noxai.security.secrets import SecretStore

log = logging.getLogger(__name__)

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105

SCOPE_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"
SCOPE_SEND = "https://www.googleapis.com/auth/gmail.send"


class GmailConnector(BaseConnector):
    """Connector for Google Gmail API."""

    def __init__(
        self,
        name: str = "gmail",
        config: dict[str, Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            name=name,
            connector_type="gmail",
            privacy_class="local_only",
            config=config or {},
            rate_limit_rpm=60,
        )
        self.account_email: str = self.config.get("account", "primary")
        self.client_id: str = self.config.get("client_id", "")
        self.client_secret: str = self.config.get("client_secret", "")
        self._custom_transport = transport
        self.pii_redactor = PIIRedactor()
        self.output_scanner = OutputScanner()

    def _get_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._custom_transport,
            timeout=30.0,
        )

    async def get_credentials(self) -> dict[str, str]:
        """Load OAuth tokens from SecretStore."""
        refresh_token = SecretStore.get(f"connector:{self.name}:refresh_token") or ""
        access_token = SecretStore.get(f"connector:{self.name}:access_token") or ""
        client_id = SecretStore.get(f"connector:{self.name}:client_id") or self.client_id
        client_secret = (
            SecretStore.get(f"connector:{self.name}:client_secret") or self.client_secret
        )
        return {
            "refresh_token": refresh_token,
            "access_token": access_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }

    async def save_credentials(
        self,
        access_token: str,
        refresh_token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        SecretStore.set(f"connector:{self.name}:access_token", access_token)
        if refresh_token:
            SecretStore.set(f"connector:{self.name}:refresh_token", refresh_token)
        if client_id:
            SecretStore.set(f"connector:{self.name}:client_id", client_id)
            self.client_id = client_id
        if client_secret:
            SecretStore.set(f"connector:{self.name}:client_secret", client_secret)
            self.client_secret = client_secret

    def generate_pkce_pair(self) -> tuple[str, str]:
        """Generate (code_verifier, code_challenge)."""
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode("utf-8")).digest()
        challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
        return verifier, challenge

    def get_auth_url(
        self,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        scopes: list[str] | None = None,
    ) -> str:
        """Build Google OAuth 2.0 authorization URL."""
        all_scopes = scopes or [SCOPE_READONLY]
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(all_scopes),
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"{OAUTH_AUTH_URL}?{urllib.parse.urlencode(params)}"

    async def exchange_code_for_tokens(
        self,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        client_id: str,
        client_secret: str,
    ) -> dict[str, Any]:
        """Exchange auth code for tokens via PKCE."""
        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
        async with self._get_client() as client:
            resp = await client.post(OAUTH_TOKEN_URL, data=data)
            if resp.status_code != 200:
                raise RuntimeError(f"OAuth exchange failed: {resp.status_code} {resp.text}")
            tokens_dict: dict[str, Any] = resp.json()
            await self.save_credentials(
                access_token=str(tokens_dict.get("access_token", "")),
                refresh_token=tokens_dict.get("refresh_token"),
                client_id=client_id,
                client_secret=client_secret,
            )
            return tokens_dict

    async def refresh_access_token(self) -> str:
        """Use refresh_token to acquire a fresh access_token."""
        creds = await self.get_credentials()
        refresh_token = creds.get("refresh_token")
        if not refresh_token:
            raise RuntimeError(
                "No refresh token available. Re-authenticate via `nx connect gmail`."
            )

        data = {
            "client_id": creds.get("client_id"),
            "client_secret": creds.get("client_secret"),
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        async with self._get_client() as client:
            resp = await client.post(OAUTH_TOKEN_URL, data=data)
            if resp.status_code != 200:
                raise RuntimeError(f"Token refresh failed: {resp.status_code} {resp.text}")
            tokens = resp.json()
            new_access = str(tokens.get("access_token", ""))
            await self.save_credentials(access_token=new_access)
            return new_access

    async def _authed_request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Execute request against Gmail API with token retry."""
        creds = await self.get_credentials()
        access_token = creds.get("access_token")
        if not access_token and creds.get("refresh_token"):
            access_token = await self.refresh_access_token()

        url = f"{GMAIL_API_BASE}/{endpoint.lstrip('/')}"
        headers = {"Authorization": f"Bearer {access_token}"}

        async with self._get_client() as client:
            resp = await client.request(method, url, headers=headers, params=params, json=json_data)
            if resp.status_code == 401 and creds.get("refresh_token"):
                # Retry once after refresh
                new_token = await self.refresh_access_token()
                headers["Authorization"] = f"Bearer {new_token}"
                resp = await client.request(
                    method, url, headers=headers, params=params, json=json_data
                )
            return resp

    async def auth(self) -> bool:
        """Verify authentication status."""
        creds = await self.get_credentials()
        if not creds.get("access_token") and not creds.get("refresh_token"):
            return False
        try:
            resp = await self._authed_request("GET", "profile")
            if resp.status_code == 200:
                data = resp.json()
                self.account_email = data.get("emailAddress", self.account_email)
                await self.update_health("ok", reason=f"Authenticated as {self.account_email}")
                return True
            return False
        except Exception as exc:
            log.warning("gmail_auth_check_failed error=%s", exc)
            return False

    async def health(self) -> dict[str, Any]:
        """Return connector health status."""
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
                    return {
                        "status": row[0],
                        "reason": row[1] or "",
                        "last_sync_at": row[2],
                        "last_error": row[3],
                    }
        return {"status": "degraded", "reason": "Not initialized", "last_sync_at": None}

    def _extract_body_and_attachments(
        self,
        payload: dict[str, Any],
    ) -> tuple[str, list[dict[str, Any]]]:
        """Extract plain text body and attachment metadata from Gmail message payload."""
        body_text = ""
        attachments: list[dict[str, Any]] = []

        def recurse_parts(part: dict[str, Any]) -> None:
            nonlocal body_text
            mime_type = part.get("mimeType", "")
            filename = part.get("filename", "")
            body = part.get("body", {})

            if filename:
                attachments.append(
                    {
                        "filename": filename,
                        "content_type": mime_type,
                        "size_bytes": body.get("size", 0),
                        "attachment_id": body.get("attachmentId"),
                    }
                )

            data = body.get("data")
            if data and mime_type.startswith("text/plain"):
                try:
                    decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                    body_text += decoded + "\n"
                except Exception as exc:
                    log.debug("failed to decode text part: %s", exc)
            elif data and mime_type.startswith("text/html") and not body_text:
                try:
                    raw_html = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                    clean = re.sub(r"<[^>]+>", " ", raw_html)
                    clean = re.sub(r"\s+", " ", clean).strip()
                    body_text += clean + "\n"
                except Exception as exc:
                    log.debug("failed to decode html part: %s", exc)

            for child in part.get("parts", []):
                recurse_parts(child)

        recurse_parts(payload)
        return body_text.strip(), attachments

    async def sync(self) -> dict[str, Any]:
        """Sync recent messages into mail_items and mail_attachments."""
        resp = await self._authed_request("GET", "messages", params={"maxResults": 25})
        if resp.status_code != 200:
            err = f"Failed to list messages: {resp.status_code} {resp.text}"
            await self.update_health("error", reason=err, error=err)
            raise RuntimeError(err)

        msg_list = resp.json().get("messages", [])
        synced_count = 0
        now = datetime.now(UTC).isoformat()

        async with get_db() as db:
            for item in msg_list:
                msg_id = item["id"]
                detail_resp = await self._authed_request(
                    "GET", f"messages/{msg_id}", params={"format": "full"}
                )
                if detail_resp.status_code != 200:
                    continue
                msg_data = detail_resp.json()
                thread_id = msg_data.get("threadId")
                label_ids = msg_data.get("labelIds", [])

                headers = {
                    h["name"].lower(): h["value"]
                    for h in msg_data.get("payload", {}).get("headers", [])
                }
                subject = headers.get("subject", "(No Subject)")
                from_header = headers.get("from", "")
                date_header = headers.get("date", now)

                from_name = from_header
                from_domain = ""
                if "<" in from_header and ">" in from_header:
                    from_name = from_header.split("<")[0].strip().strip('"')
                    email_addr = from_header.split("<")[1].split(">")[0].strip()
                    if "@" in email_addr:
                        from_domain = email_addr.split("@")[-1].lower()
                elif "@" in from_header:
                    from_domain = from_header.split("@")[-1].lower()

                raw_body, attachments = self._extract_body_and_attachments(
                    msg_data.get("payload", {})
                )

                # Sanitize body: redacts Indian PII and leaks
                sanitized_text, _ = self.pii_redactor.redact(raw_body)
                # Cap sanitized text at 20KB
                sanitized_body = sanitized_text[:20480]

                item_db_id = f"gmail_{msg_id}"
                await db.execute(
                    """
                    INSERT INTO mail_items (
                        id, account, message_id, thread_id, labels_json,
                        from_name, from_domain, date, subject, text_sanitized,
                        flags_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account, message_id) DO UPDATE SET
                        labels_json = excluded.labels_json,
                        flags_json = excluded.flags_json,
                        subject = excluded.subject,
                        text_sanitized = excluded.text_sanitized
                    """,
                    (
                        item_db_id,
                        self.account_email,
                        msg_id,
                        thread_id,
                        json.dumps(label_ids),
                        from_name,
                        from_domain,
                        date_header,
                        subject,
                        sanitized_body,
                        json.dumps({"is_unread": "UNREAD" in label_ids}),
                        now,
                    ),
                )

                # Store attachments
                for att in attachments:
                    att_id = f"{item_db_id}_{secrets.token_hex(4)}"
                    await db.execute(
                        """
                        INSERT INTO mail_attachments (
                            id, mail_item_id, filename, content_type, size_bytes, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            att_id,
                            item_db_id,
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
        """Fetch stored mail items."""
        sql = (
            "SELECT id, account, message_id, subject, from_name, from_domain, date, "
            "text_sanitized FROM mail_items WHERE account = ? AND deleted_at IS NULL"
        )
        params: list[Any] = [self.account_email]
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
        """Execute guarded write actions: draft or send."""
        if action == "draft":
            to = payload.get("to", "")
            subject = payload.get("subject", "")
            body = payload.get("body", "")

            msg = email.message.EmailMessage()
            msg["To"] = to
            msg["Subject"] = subject
            msg["From"] = self.account_email
            msg.set_content(body)

            raw_bytes = msg.as_bytes()
            encoded_msg = base64.urlsafe_b64encode(raw_bytes).decode("utf-8")

            resp = await self._authed_request(
                "POST",
                "drafts",
                json_data={"message": {"raw": encoded_msg}},
            )
            if resp.status_code not in (200, 201):
                raise RuntimeError(f"Failed to create draft: {resp.status_code} {resp.text}")

            draft_data = resp.json()
            await log_audit_event(
                actor="user",
                action="gmail_draft",
                target=to,
                decision="allowed",
                summary_redacted=f"Draft created to {to} subject: {subject[:30]}",
            )
            return {"status": "created", "draft_id": draft_data.get("id")}

        elif action == "send":
            to = payload.get("to", "")
            subject = payload.get("subject", "")
            body = payload.get("body", "")

            # Guarded action: requires human approval
            if not approval_id:
                appr = await ApprovalsCenter.request_approval(
                    action_type="gmail_send",
                    payload=payload,
                    preview=f"Send email to {to} | Subject: {subject}\n{body[:100]}...",
                    scope="gmail",
                )
                return {
                    "status": "pending_approval",
                    "approval_id": appr.id,
                    "confirmation_code": appr.confirmation_code,
                    "message": (
                        f"Approval required to send email. Run: nx approvals approve {appr.id}"
                    ),
                }

            # Verify and consume the approval
            consumed = await ApprovalsCenter.verify_and_consume(
                approval_id=approval_id,
                action_type="gmail_send",
                payload=payload,
            )
            if not consumed:
                raise PermissionError(
                    f"Approval {approval_id} is invalid, expired, or payload mismatch."
                )

            msg = email.message.EmailMessage()
            msg["To"] = to
            msg["Subject"] = subject
            msg["From"] = self.account_email
            msg.set_content(body)

            raw_bytes = msg.as_bytes()
            encoded_msg = base64.urlsafe_b64encode(raw_bytes).decode("utf-8")

            resp = await self._authed_request(
                "POST",
                "messages/send",
                json_data={"raw": encoded_msg},
            )
            if resp.status_code not in (200, 201):
                raise RuntimeError(f"Failed to send email: {resp.status_code} {resp.text}")

            res_json = resp.json()
            await log_audit_event(
                actor="user",
                action="gmail_send",
                target=to,
                decision="executed",
                summary_redacted=f"Email sent to {to} subject: {subject[:30]}",
            )
            return {"status": "sent", "message_id": res_json.get("id")}

        raise ValueError(f"Unknown guarded_write action: {action}")
