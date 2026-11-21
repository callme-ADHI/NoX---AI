"""Comprehensive test suite for Phase 4: Connectors, Gmail, IMAP, Calendar, and Mail Profile.

Covers:
- Migration 017 schema and constraints
- Gmail connector with MockTransport (OAuth, message sync, PII sanitization, guarded send)
- IMAP connector with mock imaplib (MIME parsing, attachments, PII sanitization)
- Calendar connector (Google Calendar API mock + RFC5545 ICS parsing)
- MailClassifier, PhishingDetector, and DigestGenerator
- Guarded writes with ApprovalsCenter
"""

from __future__ import annotations

import base64
import email.message
import os
import tempfile
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from noxai.connectors.base import ConnectorRegistry
from noxai.connectors.calendar import CalendarConnector
from noxai.connectors.gmail import GmailConnector
from noxai.connectors.imap import IMAPConnector
from noxai.connectors.mail_profile import (
    DigestGenerator,
    MailClassifier,
    PhishingDetector,
)
from noxai.db import get_db, run_migrations
from noxai.security.approvals import ApprovalsCenter


@pytest.fixture(autouse=True)
async def setup_test_db(tmp_path: Any, monkeypatch: Any) -> Any:
    db_file = tmp_path / "test_connectors.db"
    monkeypatch.setenv("NOX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOX_DATABASE_PATH", str(db_file))
    await run_migrations()
    yield


@pytest.mark.asyncio
async def test_migration_017_tables() -> None:
    """Verify that migration 017 creates all expected tables and indexes."""
    async with get_db() as db:
        async with db.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name IN (
                'connectors', 'mail_items', 'mail_attachments', 'calendar_events'
            )
            """
        ) as cur:
            tables = {r[0] for r in await cur.fetchall()}
            assert {"connectors", "mail_items", "mail_attachments", "calendar_events"}.issubset(
                tables
            )


@pytest.mark.asyncio
async def test_base_connector_and_registry() -> None:
    """Test BaseConnector registration, DB sync, and health updates."""
    conn = GmailConnector(name="test_gmail", config={"account": "user@example.com"})
    ConnectorRegistry.register(conn)
    assert ConnectorRegistry.get("test_gmail") == conn

    await conn.register_in_db()
    items = await ConnectorRegistry.list_db_connectors()
    assert any(it["name"] == "test_gmail" and it["type"] == "gmail" for it in items)

    await conn.update_health("degraded", reason="Network flaky")
    h = await conn.health()
    assert h["status"] == "degraded"
    assert h["reason"] == "Network flaky"

    ok = await ConnectorRegistry.set_enabled("test_gmail", False)
    assert ok is True
    items2 = await ConnectorRegistry.list_db_connectors()
    assert any(it["name"] == "test_gmail" and it["enabled"] is False for it in items2)


@pytest.mark.asyncio
async def test_gmail_connector_mocked_sync_and_pii_redaction() -> None:
    """Test Gmail OAuth endpoints and sync with PII redaction."""

    # Build fake Gmail REST API responses
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/profile"):
            return httpx.Response(200, json={"emailAddress": "student@college.edu"})
        elif "/messages?maxResults=25" in url:
            return httpx.Response(200, json={"messages": [{"id": "msg_001", "threadId": "th_001"}]})
        elif "/messages/msg_001?format=full" in url:
            # Body contains Indian Aadhaar and PAN numbers
            body_content = (
                "Hello, your Aadhaar is 2123 4567 8901 and PAN is ABCDE1234F. "
                "Assignment is due on Dec 10."
            )
            b64_body = base64.urlsafe_b64encode(body_content.encode("utf-8")).decode("utf-8")
            return httpx.Response(
                200,
                json={
                    "id": "msg_001",
                    "threadId": "th_001",
                    "labelIds": ["INBOX", "UNREAD"],
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "College Assignment Update"},
                            {"name": "From", "value": "Prof Smith <smith@college.edu>"},
                            {"name": "Date", "value": "2026-11-21T08:00:00Z"},
                        ],
                        "mimeType": "text/plain",
                        "body": {"data": b64_body, "size": len(body_content)},
                        "parts": [
                            {
                                "filename": "syllabus.pdf",
                                "mimeType": "application/pdf",
                                "body": {"size": 1024, "attachmentId": "att_1"},
                            }
                        ],
                    },
                },
            )
        elif url.endswith("/drafts"):
            return httpx.Response(200, json={"id": "draft_123"})
        elif url.endswith("/messages/send"):
            return httpx.Response(200, json={"id": "sent_456"})
        return httpx.Response(404, json={"error": "not found"})

    transport = httpx.MockTransport(handler)
    conn = GmailConnector(name="gmail_test", transport=transport)
    await conn.save_credentials(access_token="fake_token", client_id="cid", client_secret="csec")
    await conn.register_in_db()

    # Test auth
    is_authed = await conn.auth()
    assert is_authed is True
    assert conn.account_email == "student@college.edu"

    # Test sync
    sync_res = await conn.sync()
    assert sync_res["synced_count"] == 1

    # Verify message in DB and check that PII was redacted
    messages = await conn.fetch()
    assert len(messages) == 1
    m = messages[0]
    assert m["subject"] == "College Assignment Update"
    assert m["from_domain"] == "college.edu"
    # Ensure sensitive Aadhaar and PAN were redacted
    assert "2123 4567 8901" not in m["text"]
    assert "ABCDE1234F" not in m["text"]
    assert "[AADHAAR_REDACTED_" in m["text"]
    assert "[PAN_REDACTED_" in m["text"]

    # Verify attachment saved
    async with get_db() as db:
        async with db.execute(
            "SELECT filename, content_type, size_bytes FROM mail_attachments"
        ) as cur:
            atts = await cur.fetchall()
            assert len(atts) == 1
            assert atts[0][0] == "syllabus.pdf"
            assert atts[0][1] == "application/pdf"
            assert atts[0][2] == 1024


@pytest.mark.asyncio
async def test_gmail_guarded_write_approvals() -> None:
    """Test that sending email requires human approval."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/messages/send"):
            return httpx.Response(200, json={"id": "sent_999"})
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    conn = GmailConnector(name="gmail_guard", transport=transport)
    await conn.save_credentials(access_token="fake_token")

    payload = {
        "to": "advisor@college.edu",
        "subject": "Project Proposal",
        "body": "Here is the proposal.",
    }

    # Attempt send without approval -> returns pending_approval
    res1 = await conn.guarded_write("send", payload)
    assert res1["status"] == "pending_approval"
    approval_id = res1["approval_id"]
    assert approval_id

    # Approve action
    await ApprovalsCenter.approve(approval_id, actor="user")

    # Send with valid approval_id -> succeeds
    res2 = await conn.guarded_write("send", payload, approval_id=approval_id)
    assert res2["status"] == "sent"
    assert res2["message_id"] == "sent_999"


@pytest.mark.asyncio
async def test_imap_connector_with_mock_client() -> None:
    """Test IMAP connector MIME parsing, attachment handling, and sync."""
    msg = email.message.EmailMessage()
    msg["Subject"] = "Hackathon Registration Confirmed"
    msg["From"] = "Organizers <team@devpost.com>"
    msg["Date"] = "2026-11-21T09:00:00Z"
    msg["Message-ID"] = "<hack123@devpost.com>"
    msg.set_content("Welcome! Submit your project by December 1st. Please review the rules.")
    msg.add_attachment(b"guide content", maintype="text", subtype="plain", filename="rules.txt")
    raw_bytes = msg.as_bytes()

    mock_client = MagicMock()
    mock_client.select.return_value = ("OK", [b"1"])
    mock_client.search.return_value = ("OK", [b"1"])
    mock_client.fetch.return_value = ("OK", [(b"1 (RFC822 {100}", raw_bytes)])

    conn = IMAPConnector(
        name="imap_test", config={"host": "imap.fake.com", "username": "me@devpost.com"}
    )
    await conn.set_credentials("imap.fake.com", "me@devpost.com", "secretpass")

    with patch.object(conn, "_sync_connect", return_value=mock_client):
        res = await conn.sync()
        assert res["synced_count"] == 1

        items = await conn.fetch()
        assert len(items) == 1
        it = items[0]
        assert it["subject"] == "Hackathon Registration Confirmed"
        assert it["from_domain"] == "devpost.com"
        assert "Submit your project by December 1st" in it["text"]


@pytest.mark.asyncio
async def test_calendar_connector_and_ics_parsing() -> None:
    """Test Calendar ICS parsing and Google Calendar event sync."""
    ics_sample = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//NoX Test//EN
BEGIN:VEVENT
UID:event_abc_123
SUMMARY:AI Architecture Review
DESCRIPTION:Review OpenJarvis parity & security foundation
LOCATION:Room 404
DTSTART:20261121T140000Z
DTEND:20261121T150000Z
END:VEVENT
BEGIN:VEVENT
UID:event_def_456
SUMMARY:Hackathon Submission Due
DESCRIPTION:Final commit deadline
LOCATION:Online
DTSTART:20261122T180000Z
DTEND:20261122T190000Z
END:VEVENT
END:VCALENDAR
"""
    conn = CalendarConnector(name="cal_test")
    events = conn.parse_ics_text(ics_sample)
    assert len(events) == 2
    assert events[0]["event_id"] == "event_abc_123"
    assert events[0]["title"] == "AI Architecture Review"
    assert events[0]["start_time"] == "2026-11-21T14:00:00Z"
    assert events[0]["location"] == "Room 404"

    # Test file-based sync
    with tempfile.NamedTemporaryFile("w", suffix=".ics", delete=False) as f:
        f.write(ics_sample)
        f_path = f.name

    try:
        conn.ics_path = f_path
        await conn.register_in_db()
        sync_res = await conn.sync()
        assert sync_res["synced_count"] == 2

        upcoming = await conn.fetch_upcoming(days=3)
        assert len(upcoming) >= 1
    finally:
        if os.path.exists(f_path):
            os.unlink(f_path)


def test_phishing_detector() -> None:
    """Test phishing detector against brand spoofing and lookalikes."""
    # Legitimate Google email
    res1 = PhishingDetector.check_sender(
        from_name="Google Accounts",
        from_domain="google.com",
        subject="Security Notification",
        text="New login detected from Linux.",
    )
    assert res1["suspicious"] is False

    # Spoofed PayPal email from suspicious domain
    res2 = PhishingDetector.check_sender(
        from_name="PayPal Support",
        from_domain="paypal-verify-account.tk",
        subject="Account Suspended",
        text="Action required within 24 hours: verify your password immediately.",
    )
    assert res2["suspicious"] is True
    assert res2["risk_level"] == "high"
    assert any("PayPal" in r for r in res2["reasons"])


def test_mail_classifier_categories_and_extraction() -> None:
    """Test rule-based email categorization and action item/deadline extraction."""
    classifier = MailClassifier()

    # 1. Categories
    cat1 = classifier.classify(
        "Midterm exam syllabus", "Exam questions covered in lecture", "college.edu"
    )
    assert cat1 == "College"

    cat2 = classifier.classify(
        "Invitation to Technical Interview",
        "We are pleased to invite you for interview",
        "uber.com",
    )
    assert cat2 == "Opportunities"

    cat3 = classifier.classify(
        "Hacking Challenge 2026", "Devpost CTF and bounty details", "devpost.com"
    )
    assert cat3 == "Hackathons/CTF"

    cat4 = classifier.classify(
        "HDFC Bank Alert: Rs. 5000 debited", "Transaction at UPI merchant", "hdfcbank.com"
    )
    assert cat4 == "Payments/Banking"

    # 2. Deadlines
    deadlines = classifier.extract_deadlines(
        "Paper Review", "Please submit your revisions due December 15th, 2026."
    )
    assert len(deadlines) >= 1
    assert "December 15" in deadlines[0]

    # 3. Action items
    actions = classifier.extract_action_items("Please submit the completed assignment by Monday.")
    assert len(actions) >= 1
    assert "the completed assignment by Monday" in actions[0]


@pytest.mark.asyncio
async def test_digest_generator() -> None:
    """Test daily morning and evening digest compilation."""
    now = datetime.now(UTC).isoformat()
    async with get_db() as db:
        # Insert a sample email
        await db.execute(
            """
            INSERT INTO mail_items (
                id, account, message_id, subject, from_name, from_domain,
                date, text_sanitized, created_at
            ) VALUES (
                'dig_1', 'me@nox.ai', 'm1', 'Hackathon Final Deadline',
                'Devpost Staff', 'devpost.com', ?,
                'Please submit your code before Dec 1.', ?
            )
            """,
            (now, now),
        )
        # Insert a sample calendar event
        future = (datetime.now(UTC) + timedelta(hours=4)).isoformat()
        await db.execute(
            """
            INSERT INTO calendar_events (
                id, account, event_id, title, start_time, end_time,
                location, description, source, created_at, updated_at
            ) VALUES (
                'ev_1', 'me@nox.ai', 'e1', 'Team Sync', ?, ?,
                'Office', 'Standup', 'ics', ?, ?
            )
            """,
            (future, future, now, now),
        )
        await db.commit()

    generator = DigestGenerator()
    digest = await generator.generate_digest(period="morning")

    assert digest["period"] == "morning"
    assert digest["total_emails_24h"] >= 1
    assert digest["calendar_events_count"] >= 1
    assert "Hackathons/CTF" in digest["categories_breakdown"]
    assert len(digest["deadlines"]) >= 1
    assert len(digest["action_items"]) >= 1
