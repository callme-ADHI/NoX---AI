"""Integration and unit tests for NoX Daemon REST API, auth, CSRF, and WebSocket (Milestone 7)."""

from __future__ import annotations

import stat
from collections.abc import Generator
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from noxai.api.app import create_app
from noxai.api.auth import (
    COOKIE_NAME,
    check_gate_authenticated,
    generate_session_token,
    load_session_token,
)
from noxai.config import get_config
from noxai.db import execute, fetch_one, get_db


@pytest.fixture(autouse=True)
def setup_isolated_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    """Isolate DB, session token path, and configuration during tests."""
    data_dir = tmp_path / "share" / "nox"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "nox.db"
    token_file = data_dir / "session_token"

    # Patch paths
    import noxai.api.auth as auth_mod
    import noxai.db as db_mod

    monkeypatch.setattr(db_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(db_mod, "DB_PATH", db_file)
    monkeypatch.setattr(auth_mod, "DEFAULT_TOKEN_PATH", token_file)

    # Ensure clean token
    generate_session_token(token_file)

    yield


@pytest.fixture
def valid_token() -> str:
    from noxai.api.auth import load_session_token

    tok = load_session_token()
    assert tok is not None
    return tok


@pytest.fixture
def auth_headers(valid_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {valid_token}",
        "X-Nox-Client": "web",
    }


@pytest.fixture
def client(setup_isolated_env: None) -> Generator[TestClient, None, None]:
    """Test client with isolated app instance and database."""
    app = create_app(start_scheduler=False)
    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Auth & Token Tests
# ---------------------------------------------------------------------------


def test_session_token_creation_and_permissions(tmp_path: Path) -> None:
    """Session token is 64-char hex, stored with strict 0600 file permissions."""
    tfile = tmp_path / "token_test"
    token = generate_session_token(tfile)

    assert len(token) == 64
    assert tfile.exists()
    assert load_session_token(tfile) == token

    mode = stat.S_IMODE(tfile.stat().st_mode)
    assert mode == 0o600


@pytest.mark.asyncio
async def test_gate_authentication_modes() -> None:
    """Gate mode: off and token_only pass; command verifies exit code."""
    cfg = get_config()

    # mode: off
    cfg.gate.mode = "off"
    assert await check_gate_authenticated(cfg) is True

    # mode: token_only
    cfg.gate.mode = "token_only"
    assert await check_gate_authenticated(cfg) is True

    # mode: command with exit 0
    cfg.gate.mode = "command"
    cfg.gate.command = ["true"]
    assert await check_gate_authenticated(cfg) is True

    # mode: command with exit 1
    cfg.gate.command = ["false"]
    assert await check_gate_authenticated(cfg) is False

    # mode: command with missing command
    cfg.gate.command = []
    assert await check_gate_authenticated(cfg) is False


def test_status_endpoint_unauthenticated(client: TestClient) -> None:
    """GET /api/status is publicly accessible without token (Section 6.1)."""
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "authenticated" in data
    assert "gate_mode" in data
    assert "active_runs_count" in data
    assert "vector_backend" in data


def test_root_token_exchange(client: TestClient, valid_token: str) -> None:
    """GET /?t=<token> validates token and sets HttpOnly SameSite=Strict cookie."""
    # Bad token
    bad_resp = client.get("/?t=invalid_token_12345", follow_redirects=False)
    assert bad_resp.status_code == 401

    # Good token -> redirects to / with cookie set
    good_resp = client.get(f"/?t={valid_token}", follow_redirects=False)
    assert good_resp.status_code == 303
    assert good_resp.headers["location"] == "/"
    cookie_header = good_resp.headers.get("set-cookie", "")
    assert COOKIE_NAME in cookie_header
    assert "httponly" in cookie_header.lower()
    assert "samesite=strict" in cookie_header.lower()

    # Access / with cookie
    client.cookies.set(COOKIE_NAME, valid_token)
    authed_root = client.get("/")
    assert authed_root.status_code == 200
    assert "NoX" in authed_root.text

    # Access SPA route e.g. /chat
    chat_route = client.get("/chat")
    assert chat_route.status_code == 200
    assert "NoX" in chat_route.text

    # Access static file e.g. /manifest.json
    manifest_resp = client.get("/manifest.json")
    assert manifest_resp.status_code == 200
    assert "NoX AI Agent" in manifest_resp.text


def test_auth_headers_and_cookie_acceptance(client: TestClient, valid_token: str) -> None:
    """Protected endpoints accept cookie, Bearer header, or X-Nox-Token header."""
    # 1. No auth -> 401
    resp_no_auth = client.get("/api/conversations")
    assert resp_no_auth.status_code == 401

    # 2. Bearer token -> 200
    resp_bearer = client.get(
        "/api/conversations",
        headers={"Authorization": f"Bearer {valid_token}"},
    )
    assert resp_bearer.status_code == 200

    # 3. X-Nox-Token -> 200
    resp_header = client.get(
        "/api/conversations",
        headers={"X-Nox-Token": valid_token},
    )
    assert resp_header.status_code == 200

    # 4. Cookie -> 200
    client.cookies.set(COOKIE_NAME, valid_token)
    resp_cookie = client.get("/api/conversations")
    assert resp_cookie.status_code == 200


def test_csrf_defense_on_mutations(client: TestClient, valid_token: str) -> None:
    """State-changing requests require X-Nox-Client header (Section 6.1)."""
    client.cookies.set(COOKIE_NAME, valid_token)

    # Missing X-Nox-Client header on POST -> 403 Forbidden
    resp = client.post(
        "/api/jobs",
        json={"name": "test-job", "schedule_expr": "0 * * * *", "instruction": "test"},
    )
    assert resp.status_code == 403

    # Invalid client value -> 403
    resp_invalid = client.post(
        "/api/jobs",
        json={"name": "test-job", "schedule_expr": "0 * * * *", "instruction": "test"},
        headers={"X-Nox-Client": "attacker-site"},
    )
    assert resp_invalid.status_code == 403

    # Valid X-Nox-Client: web -> 200
    resp_ok = client.post(
        "/api/jobs",
        json={"name": "test-job-csrf", "schedule_expr": "0 * * * *", "instruction": "test"},
        headers={"X-Nox-Client": "web"},
    )
    assert resp_ok.status_code == 200


# ---------------------------------------------------------------------------
# Conversations API Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversations_api(client: TestClient, auth_headers: dict[str, str]) -> None:
    """List, read, and delete conversations."""
    async with get_db() as db:
        await execute(
            db,
            "INSERT INTO conversations (id, title) VALUES ('conv_test_1', 'Test Conversation')",
        )
        await execute(
            db,
            "INSERT INTO messages (id, conversation_id, role, content) "
            "VALUES ('msg_1', 'conv_test_1', 'user', 'Hello NoX')",
        )
        await db.commit()

    # List conversations
    resp = client.get("/api/conversations", headers=auth_headers)
    assert resp.status_code == 200
    convs = resp.json()
    assert any(c["id"] == "conv_test_1" for c in convs)

    # Get conversation details
    detail_resp = client.get("/api/conversations/conv_test_1", headers=auth_headers)
    assert detail_resp.status_code == 200
    data = detail_resp.json()
    assert data["id"] == "conv_test_1"
    assert len(data["messages"]) == 1
    assert data["messages"][0]["content"] == "Hello NoX"

    # Delete conversation
    del_resp = client.delete("/api/conversations/conv_test_1", headers=auth_headers)
    assert del_resp.status_code == 200
    assert del_resp.json()["ok"] is True

    # Verify deleted
    get_after_del = client.get("/api/conversations/conv_test_1", headers=auth_headers)
    assert get_after_del.status_code == 404


# ---------------------------------------------------------------------------
# Jobs API Tests
# ---------------------------------------------------------------------------


def test_jobs_crud_and_versioning(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Create, retrieve, patch with version snapshot, restore, and delete job."""
    # 1. Create job
    create_body = {
        "name": "backup-task",
        "description": "Daily backup",
        "schedule_type": "cron",
        "schedule_expr": "0 2 * * *",
        "timezone": "Asia/Kolkata",
        "instruction": "Run daily backup procedure",
        "config": {"kind": "agent"},
        "enabled": True,
    }
    create_resp = client.post("/api/jobs", json=create_body, headers=auth_headers)
    assert create_resp.status_code == 200
    created = create_resp.json()
    job_id = created["id"]
    assert created["name"] == "backup-task"

    # 2. Get job
    get_resp = client.get(f"/api/jobs/{job_id}", headers=auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["schedule_expr"] == "0 2 * * *"

    # 3. Patch job (updates version)
    patch_body = {
        "instruction": "Updated backup procedure with checksum verification",
    }
    patch_resp = client.patch(f"/api/jobs/{job_id}", json=patch_body, headers=auth_headers)
    assert patch_resp.status_code == 200
    assert patch_resp.json()["version"] == 2

    # 4. Get versions
    ver_resp = client.get(f"/api/jobs/{job_id}/versions", headers=auth_headers)
    assert ver_resp.status_code == 200
    versions = ver_resp.json()
    assert len(versions) == 2
    assert {v["version"] for v in versions} == {1, 2}

    # 5. Restore version 1
    restore_resp = client.post(
        f"/api/jobs/{job_id}/restore",
        json={"version": 1},
        headers=auth_headers,
    )
    assert restore_resp.status_code == 200
    assert restore_resp.json()["restored_version"] == 1

    restored_get = client.get(f"/api/jobs/{job_id}", headers=auth_headers)
    assert restored_get.json()["instruction"] == "Run daily backup procedure"

    # 6. Delete job
    del_resp = client.delete(f"/api/jobs/{job_id}", headers=auth_headers)
    assert del_resp.status_code == 200
    assert del_resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# Briefings & Feedback API Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_briefings_and_feedback(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Retrieve briefings and submit owner feedback rating."""
    async with get_db() as db:
        row = await fetch_one(db, "SELECT id FROM jobs WHERE name = 'daily-briefing'")
        job_id = row["id"] if row else "job_brief"
        await execute(
            db,
            """
            INSERT INTO job_runs (id, job_id, status)
            VALUES ('run_brief', ?, 'ok')
            """,
            (job_id,),
        )
        await execute(
            db,
            """
            INSERT INTO outputs (id, job_run_id, title, markdown, sources_json)
            VALUES (101, 'run_brief', 'Morning Cyber Briefing',
                    '# TL;DR\nAll systems nominal.', '["cyber"]')
            """,
        )
        await db.commit()

    # List
    list_resp = client.get("/api/briefings", headers=auth_headers)
    assert list_resp.status_code == 200
    assert any(b["id"] == 101 for b in list_resp.json())

    # Get single
    get_resp = client.get("/api/briefings/101", headers=auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["title"] == "Morning Cyber Briefing"

    # Send feedback
    fb_resp = client.post(
        "/api/briefings/101/feedback",
        json={"rating": 1, "note": "Great summary"},
        headers=auth_headers,
    )
    assert fb_resp.status_code == 200
    assert fb_resp.json()["ok"] is True

    # Verify updated
    updated_resp = client.get("/api/briefings/101", headers=auth_headers)
    assert updated_resp.json()["feedback"] == 1


# ---------------------------------------------------------------------------
# Memory API Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_facts_api(client: TestClient, auth_headers: dict[str, str]) -> None:
    """List, patch, pin, and delete durable memory facts."""
    async with get_db() as db:
        await execute(
            db,
            """
            INSERT INTO facts (id, key, value, category, pinned)
            VALUES (55, 'pref.theme', 'dark', 'preference', 0)
            """,
        )
        await db.commit()

    # List facts
    list_resp = client.get("/api/memory/facts", headers=auth_headers)
    assert list_resp.status_code == 200
    assert any(f["id"] == 55 for f in list_resp.json())

    # Update fact
    update_resp = client.patch(
        "/api/memory/facts/55",
        json={"value": "nord"},
        headers=auth_headers,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["value"] == "nord"

    # Pin fact
    pin_resp = client.post(
        "/api/memory/facts/55/pin",
        json={"pinned": True},
        headers=auth_headers,
    )
    assert pin_resp.status_code == 200
    assert pin_resp.json()["ok"] is True

    # Delete fact
    del_resp = client.delete("/api/memory/facts/55", headers=auth_headers)
    assert del_resp.status_code == 200
    assert del_resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# Sources API Tests
# ---------------------------------------------------------------------------


def test_sources_api_lifecycle(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Create, list, patch, and delete information sources."""
    # Create
    create_body = {
        "name": "Custom Tech Feed",
        "url": "https://custom-tech-feed.example.com/rss.xml",
        "kind": "rss",
        "tags": ["news", "cyber"],
    }
    post_resp = client.post("/api/sources", json=create_body, headers=auth_headers)
    assert post_resp.status_code == 200
    src_id = post_resp.json()["id"]

    # List
    list_resp = client.get("/api/sources", headers=auth_headers)
    assert list_resp.status_code == 200
    assert any(s["id"] == src_id for s in list_resp.json())

    # Patch
    patch_resp = client.patch(
        f"/api/sources/{src_id}",
        json={"name": "Bleeping Computer News", "enabled": False},
        headers=auth_headers,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["name"] == "Bleeping Computer News"
    assert patch_resp.json()["enabled"] == 0

    # Delete
    del_resp = client.delete(f"/api/sources/{src_id}", headers=auth_headers)
    assert del_resp.status_code == 200
    assert del_resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# Settings API Tests
# ---------------------------------------------------------------------------


def test_settings_api(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Non-safety settings can be retrieved and patched; safety settings are forbidden."""
    # Get settings
    get_resp = client.get("/api/settings", headers=auth_headers)
    assert get_resp.status_code == 200
    settings = get_resp.json()
    assert "theme" in settings

    # Patch non-safety settings
    patch_resp = client.patch(
        "/api/settings",
        json={"theme": "cyberpunk", "notifications": {"desktop": True}},
        headers=auth_headers,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["ok"] is True

    # Attempt to tamper with safety settings -> 400 Bad Request
    safety_tamper_resp = client.patch(
        "/api/settings",
        json={"safety": {"allowed_file_roots": ["/"]}},
        headers=auth_headers,
    )
    assert safety_tamper_resp.status_code == 400


# ---------------------------------------------------------------------------
# Approvals API Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approvals_resolution(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Approve or deny pending tool execution approvals."""
    async with get_db() as db:
        await execute(
            db,
            """
            INSERT INTO runs (id, kind, status)
            VALUES ('run_test', 'chat', 'running')
            """,
        )
        await execute(
            db,
            """
            INSERT INTO approvals (id, run_id, tool_name, args_json, reason, args_hash, status)
            VALUES (77, 'run_test', 'shell_execute', '{"cmd": "ls"}',
                    'Shell command execution', 'hash77', 'pending')
            """,
        )
        await db.commit()

    # Approve
    approve_resp = client.post(
        "/api/approvals/77",
        json={"decision": "approve"},
        headers=auth_headers,
    )
    assert approve_resp.status_code == 200
    assert approve_resp.json()["ok"] is True

    # Verify status in DB
    async with get_db() as db:
        row = await fetch_one(db, "SELECT status FROM approvals WHERE id = 77")
        assert row is not None
        assert row["status"] == "approved"


# ---------------------------------------------------------------------------
# WebSocket Tests
# ---------------------------------------------------------------------------


def test_websocket_ping_pong_and_auth(client: TestClient, valid_token: str) -> None:
    """WebSocket handles authentication, ping/pong, and event push."""
    # 1. Unauthenticated connection rejected
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws"):
            pass

    # 2. Authenticated connection via ?token=
    with client.websocket_connect(f"/ws?token={valid_token}") as ws:
        # Ping -> Pong
        ws.send_json({"type": "ping"})
        msg = {}
        for _ in range(250):
            m = ws.receive_json()
            if m.get("type") == "pong":
                msg = m
                break
        assert msg == {"type": "pong"}
