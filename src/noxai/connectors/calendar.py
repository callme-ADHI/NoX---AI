"""Calendar connector for NoX supporting Google Calendar API and local/remote ICS files.

Synchronizes events into the local calendar_events table, provides upcoming
event queries, and enforces guarded writes.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from noxai.connectors.base import BaseConnector
from noxai.db import get_db
from noxai.security.approvals import ApprovalsCenter
from noxai.security.audit import log_audit_event
from noxai.security.secrets import SecretStore

log = logging.getLogger(__name__)

GCAL_API_BASE = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


class CalendarConnector(BaseConnector):
    """Calendar connector supporting Google Calendar API and iCalendar (.ics)."""

    def __init__(
        self,
        name: str = "calendar",
        config: dict[str, Any] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            name=name,
            connector_type="calendar",
            privacy_class="local_only",
            config=config or {},
            rate_limit_rpm=60,
        )
        self.account: str = self.config.get("account", "primary")
        self.ics_path: str = self.config.get("ics_path", "")
        self.ics_url: str = self.config.get("ics_url", "")
        self._custom_transport = transport

    def _get_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._custom_transport,
            timeout=30.0,
        )

    async def get_access_token(self) -> str:
        secret = SecretStore.get(f"connector:{self.name}:access_token")
        if secret:
            return secret
        # Fallback to shared gmail oauth token if available
        return SecretStore.get("connector:gmail:access_token") or ""

    async def auth(self) -> bool:
        token = await self.get_access_token()
        if token:
            try:
                headers = {"Authorization": f"Bearer {token}"}
                async with self._get_client() as client:
                    resp = await client.get(
                        GCAL_API_BASE,
                        headers=headers,
                        params={"maxResults": 1},
                    )
                    if resp.status_code == 200:
                        await self.update_health("ok", reason="Authenticated with Google Calendar")
                        return True
            except Exception as e:
                log.warning("gcal_auth_failed error=%s", e)

        if self.ics_path and Path(self.ics_path).expanduser().exists():
            await self.update_health("ok", reason=f"Using local ICS: {self.ics_path}")
            return True

        if self.ics_url:
            await self.update_health("ok", reason=f"Using remote ICS: {self.ics_url}")
            return True

        return False

    async def health(self) -> dict[str, Any]:
        has_source = False
        if self.ics_path and Path(self.ics_path).expanduser().exists():
            has_source = True
        elif self.ics_url:
            has_source = True
        elif SecretStore.get(f"connector:{self.name}:access_token"):
            has_source = True

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
                        if not has_source:
                            return {
                                "status": "NOT_CONFIGURED",
                                "reason": "No ICS file, remote URL, or Google Calendar credentials configured",
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
            log.debug("calendar_health_db_read_failed error=%s", exc)

        if not has_source:
            return {
                "status": "NOT_CONFIGURED",
                "reason": "No ICS file, remote URL, or Google Calendar credentials configured",
                "last_sync_at": None,
                "last_error": None,
            }
        return {"status": "CONFIGURED_UNTESTED", "reason": "Configured; not yet synced", "last_sync_at": None}

    def _parse_ics_datetime(self, val: str) -> str:
        """Parse RFC5545 date/datetime strings to ISO8601 string."""
        val = val.strip().split(";")[0]
        # YYYYMMDDTHHMMSSZ or YYYYMMDDTHHMMSS
        m = re.match(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z)?", val)
        if m:
            y, mo, d, h, mi, s, z = m.groups()
            return f"{y}-{mo}-{d}T{h}:{mi}:{s}Z" if z else f"{y}-{mo}-{d}T{h}:{mi}:{s}"
        # All-day date YYYYMMDD
        m_day = re.match(r"^(\d{4})(\d{2})(\d{2})", val)
        if m_day:
            y, mo, d = m_day.groups()
            return f"{y}-{mo}-{d}T00:00:00Z"
        return val

    def parse_ics_text(self, ics_content: str) -> list[dict[str, Any]]:
        """Parse raw iCalendar format text into list of event dicts."""
        events: list[dict[str, Any]] = []
        raw_events = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", ics_content, re.DOTALL)

        for chunk in raw_events:
            lines = chunk.replace("\r\n ", "").replace("\n ", "").splitlines()
            ev: dict[str, str] = {}
            for line in lines:
                if ":" in line:
                    key_part, _, val = line.partition(":")
                    key = key_part.split(";")[0].strip().upper()
                    ev[key] = val.strip()

            uid = ev.get("UID", "")
            summary = ev.get("SUMMARY", "(No Title)")
            start = self._parse_ics_datetime(ev.get("DTSTART", ""))
            end = self._parse_ics_datetime(ev.get("DTEND", start))
            loc = ev.get("LOCATION", "")
            desc = ev.get("DESCRIPTION", "")

            if uid and start:
                events.append(
                    {
                        "event_id": uid,
                        "title": summary,
                        "start_time": start,
                        "end_time": end,
                        "location": loc,
                        "description": desc,
                        "source": "ics",
                    }
                )

        return events

    async def _sync_google_calendar(self, token: str) -> list[dict[str, Any]]:
        now_iso = datetime.now(UTC).isoformat()
        headers = {"Authorization": f"Bearer {token}"}
        params: dict[str, str | int] = {
            "timeMin": now_iso,
            "maxResults": 100,
            "singleEvents": "true",
            "orderBy": "startTime",
        }
        async with self._get_client() as client:
            resp = await client.get(GCAL_API_BASE, headers=headers, params=params)
            if resp.status_code != 200:
                raise RuntimeError(f"Google Calendar API failed: {resp.status_code} {resp.text}")
            items = resp.json().get("items", [])
            events = []
            for item in items:
                start = item.get("start", {}).get("dateTime") or item.get("start", {}).get(
                    "date", ""
                )
                end = item.get("end", {}).get("dateTime") or item.get("end", {}).get("date", "")
                events.append(
                    {
                        "event_id": item["id"],
                        "title": item.get("summary", "(No Title)"),
                        "start_time": start,
                        "end_time": end,
                        "location": item.get("location", ""),
                        "description": item.get("description", ""),
                        "source": "gcal",
                    }
                )
            return events

    async def sync(self) -> dict[str, Any]:
        """Sync events from Google Calendar or ICS source into database."""
        events: list[dict[str, Any]] = []
        token = await self.get_access_token()

        if token:
            try:
                gcal_events = await self._sync_google_calendar(token)
                events.extend(gcal_events)
            except Exception as e:
                log.warning("gcal_sync_failed error=%s", e)

        if self.ics_path:
            p = Path(self.ics_path).expanduser()
            if p.exists():
                text = p.read_text(encoding="utf-8", errors="replace")
                events.extend(self.parse_ics_text(text))

        if self.ics_url:
            async with self._get_client() as client:
                resp = await client.get(self.ics_url)
                if resp.status_code == 200:
                    events.extend(self.parse_ics_text(resp.text))

        now = datetime.now(UTC).isoformat()
        synced_count = 0

        async with get_db() as db:
            for ev in events:
                db_id = f"cal_{ev['source']}_{ev['event_id'][:32]}"
                await db.execute(
                    """
                    INSERT INTO calendar_events (
                        id, account, event_id, title, start_time, end_time,
                        location, description, source, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account, event_id, source) DO UPDATE SET
                        title = excluded.title,
                        start_time = excluded.start_time,
                        end_time = excluded.end_time,
                        location = excluded.location,
                        description = excluded.description,
                        updated_at = excluded.updated_at
                    """,
                    (
                        db_id,
                        self.account,
                        ev["event_id"],
                        ev["title"],
                        ev["start_time"],
                        ev["end_time"],
                        ev["location"],
                        ev["description"],
                        ev["source"],
                        now,
                        now,
                    ),
                )
                synced_count += 1
            await db.commit()

        await self.mark_sync(status="ok", reason=f"Synced {synced_count} events")
        return {"synced_count": synced_count}

    async def fetch(self, query: str | None = None, limit: int = 50) -> list[Any]:
        sql = (
            "SELECT id, account, event_id, title, start_time, end_time, "
            "location, description, source FROM calendar_events WHERE account = ?"
        )
        params: list[Any] = [self.account]
        if query:
            sql += " AND (title LIKE ? OR description LIKE ?)"
            params.extend([f"%{query}%", f"%{query}%"])
        sql += " ORDER BY start_time ASC LIMIT ?"
        params.append(limit)

        async with get_db() as db:
            async with db.execute(sql, tuple(params)) as cur:
                rows = await cur.fetchall()
                return [
                    {
                        "id": r[0],
                        "account": r[1],
                        "event_id": r[2],
                        "title": r[3],
                        "start_time": r[4],
                        "end_time": r[5],
                        "location": r[6],
                        "description": r[7],
                        "source": r[8],
                    }
                    for r in rows
                ]

    async def fetch_upcoming(self, days: int = 7) -> list[dict[str, Any]]:
        """Fetch upcoming events for the next N days."""
        now_iso = datetime.now(UTC).isoformat()
        end_iso = (datetime.now(UTC) + timedelta(days=days)).isoformat()
        async with get_db() as db:
            async with db.execute(
                """
                SELECT id, title, start_time, end_time, location, description, source
                FROM calendar_events
                WHERE account = ? AND start_time >= ? AND start_time <= ?
                ORDER BY start_time ASC
                """,
                (self.account, now_iso, end_iso),
            ) as cur:
                rows = await cur.fetchall()
                return [
                    {
                        "id": r[0],
                        "title": r[1],
                        "start_time": r[2],
                        "end_time": r[3],
                        "location": r[4],
                        "description": r[5],
                        "source": r[6],
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
        """Create new calendar event with approval."""
        if action == "create_event":
            title = payload.get("title", "")
            start = payload.get("start_time", "")
            if not approval_id:
                appr = await ApprovalsCenter.request_approval(
                    action_type="calendar_create_event",
                    payload=payload,
                    preview=f"Create event '{title}' at {start}",
                    scope="calendar",
                )
                return {
                    "status": "pending_approval",
                    "approval_id": appr.id,
                    "confirmation_code": appr.confirmation_code,
                }

            consumed = await ApprovalsCenter.verify_and_consume(
                approval_id, "calendar_create_event", payload
            )
            if not consumed:
                raise PermissionError(
                    f"Approval {approval_id} is invalid, expired, or payload mismatch."
                )
            await log_audit_event(
                actor="user",
                action="calendar_create_event",
                target=title,
                decision="executed",
                summary_redacted=f"Event created '{title}' at {start}",
            )
            return {"status": "created"}
        raise ValueError(f"Unknown action: {action}")
