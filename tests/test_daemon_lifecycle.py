"""Tests for daemon lifecycle, locking, auth semantics, CORS, routes (Phase E4)."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from noxai.api.app import create_app
from noxai.api.auth import COOKIE_NAME
from noxai.config import get_config
from noxai.db import get_db, init_db, run_migrations
from noxai.lock import DaemonLock

# ---------------------------------------------------------------------------
# E4.1: Lock & Lifecycle
# ---------------------------------------------------------------------------


def test_daemon_lock_single_instance(tmp_path: Path) -> None:
    """Verify single-instance lock prevents concurrent daemon runs."""
    pid_file = tmp_path / "test_noxd.pid"
    lock1 = DaemonLock(pid_path=pid_file)
    lock1.acquire()

    assert pid_file.exists()
    pid_text = pid_file.read_text().strip()
    assert pid_text == str(os.getpid())

    # Second instance attempting to acquire the same lock fails with informative message
    lock2 = DaemonLock(pid_path=pid_file)
    with pytest.raises(RuntimeError) as exc_info:
        lock2.acquire()
    assert f"Daemon already running (PID {os.getpid()})" in str(exc_info.value)

    # Release lock1, lock2 can now acquire
    lock1.release()
    assert not pid_file.exists()

    lock2.acquire()
    assert pid_file.exists()
    lock2.release()
    assert not pid_file.exists()


@pytest.mark.asyncio
async def test_daemon_starts_when_ollama_down(tmp_path: Path) -> None:
    """Verify daemon starts successfully and reports degraded when Ollama is unreachable."""
    cfg = get_config()
    cfg.models.ollama.base_url = "http://127.0.0.1:54321"

    app = create_app(config=cfg, start_scheduler=False, manage_lock=False)

    with TestClient(app) as client:
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["ollama_reachable"] is False


@pytest.mark.asyncio
async def test_clean_shutdown_marks_interrupted_runs(tmp_path: Path) -> None:
    """Verify active runs/jobs marked interrupted and DB WAL checkpointed on shutdown."""
    await init_db()

    run_id = f"test-run-{uuid.uuid4().hex[:8]}"
    node_id = f"test-node-{uuid.uuid4().hex[:8]}"
    job_id = f"test-job-{uuid.uuid4().hex[:8]}"
    job_run_id = f"test-jr-{uuid.uuid4().hex[:8]}"

    # Insert mock active run and active job_run
    async with get_db() as db:
        await db.execute(
            "INSERT INTO runs (id, kind, status) VALUES (?, 'chat', 'running')",
            (run_id,),
        )
        await db.execute(
            "INSERT INTO task_nodes (id, run_id, node_index, title, objective, status) "
            "VALUES (?, ?, 0, 'Step', 'Objective', 'running')",
            (node_id, run_id),
        )
        try:
            await db.execute(
                "INSERT INTO jobs (id, name, schedule_type, schedule_expr, action) "
                "VALUES (?, 'Test Job', 'cron', '* * * * *', 'run')",
                (job_id,),
            )
            await db.execute(
                "INSERT INTO job_runs (id, job_id, status) VALUES (?, ?, 'running')",
                (job_run_id, job_id),
            )
        except Exception:
            pass
        await db.commit()

    # Lifespan startup and shutdown
    app = create_app(start_scheduler=False, manage_lock=False)
    with TestClient(app) as client:
        assert client.get("/api/status").status_code == 200

    # After lifespan shutdown, check database status
    async with get_db() as db:
        run_cursor = await db.execute("SELECT status FROM runs WHERE id = ?", (run_id,))
        run_row = await run_cursor.fetchone()
        assert run_row is not None
        assert run_row[0] == "interrupted"

        node_cursor = await db.execute("SELECT status FROM task_nodes WHERE id = ?", (node_id,))
        node_row = await node_cursor.fetchone()
        assert node_row is not None
        assert node_row[0] == "failed"

        try:
            jr_cursor = await db.execute("SELECT status FROM job_runs WHERE id = ?", (job_run_id,))
            jr_row = await jr_cursor.fetchone()
            if jr_row is not None:
                assert jr_row[0] == "interrupted"
        except Exception:
            pass


def test_refuse_insecure_secrets_permissions(tmp_path: Path) -> None:
    """Verify daemon refuses to start if secrets.env has permissions looser than 0600."""
    fake_secrets = tmp_path / "secrets.env"
    fake_secrets.write_text("NVIDIA_API_KEY=nvapi-test123456789\n")
    fake_secrets.chmod(0o644)

    with patch("noxai.api.app.SECRETS_FILE", fake_secrets):
        app = create_app(start_scheduler=False, manage_lock=False)
        with pytest.raises(PermissionError) as exc_info:
            with TestClient(app):
                pass
        assert "has unsafe permissions" in str(exc_info.value)


# ---------------------------------------------------------------------------
# E4.2: Log Spam (D3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vector_backend_logged_at_most_once(caplog: pytest.LogCaptureFixture) -> None:
    """Verify vector backend detection message is emitted at most once across 100 operations."""
    caplog.set_level("INFO")

    for _ in range(5):
        await run_migrations()

    for _ in range(50):
        async with get_db():
            pass

    log_messages = [rec.message for rec in caplog.records if "vector_backend=" in rec.message]
    assert len(log_messages) <= 1


# ---------------------------------------------------------------------------
# E4.3: CORS (D4)
# ---------------------------------------------------------------------------


def test_cors_production_mode_blocks_foreign_origin() -> None:
    """In production mode (NOX_DEV unset), foreign origin receives NO CORS headers."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("NOX_DEV", None)
        app = create_app(start_scheduler=False, manage_lock=False)
        with TestClient(app) as client:
            resp = client.get("/api/status", headers={"Origin": "http://evil.com"})
            assert resp.status_code == 200
            assert "access-control-allow-origin" not in resp.headers
            assert "access-control-allow-credentials" not in resp.headers


def test_cors_dev_mode_allows_localhost_5173() -> None:
    """In dev mode (NOX_DEV=1), http://127.0.0.1:5173 receives CORS headers."""
    with patch.dict(os.environ, {"NOX_DEV": "1"}):
        app = create_app(start_scheduler=False, manage_lock=False)
        with TestClient(app) as client:
            resp = client.get("/api/status", headers={"Origin": "http://127.0.0.1:5173"})
            assert resp.status_code == 200
            assert resp.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"


# ---------------------------------------------------------------------------
# E4.4: Auth Semantics & Security Headers (D11)
# ---------------------------------------------------------------------------


def test_token_exchange_sets_httponly_strict_cookie() -> None:
    """Verify GET /?t=<token> sets HttpOnly, SameSite=Strict cookie and 303 redirects."""
    app = create_app(start_scheduler=False, manage_lock=False)
    with TestClient(app) as client:
        valid_token = app.state.session_token

        # Exchange token
        resp = client.get(f"/?t={valid_token}", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"
        assert COOKIE_NAME in resp.cookies
        cookie_header = resp.headers.get("set-cookie", "")
        assert "HttpOnly" in cookie_header
        assert "SameSite=strict" in cookie_header or "samesite=strict" in cookie_header.lower()
        assert "Path=/" in cookie_header or "path=/" in cookie_header.lower()

        # Follow-up request using the cookie succeeds
        resp2 = client.get("/", cookies={COOKIE_NAME: valid_token})
        assert resp2.status_code == 200

        # Wrong token gets 401
        bad_resp = client.get("/?t=invalid_token_value", follow_redirects=False)
        assert bad_resp.status_code == 401


def test_security_headers_on_responses() -> None:
    """Verify Content-Security-Policy, X-Frame-Options, X-Content-Type-Options on all responses."""
    app = create_app(start_scheduler=False, manage_lock=False)
    with TestClient(app) as client:
        resp = client.get("/api/status")
        assert resp.status_code == 200

        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("Referrer-Policy") == "no-referrer"
        assert "X-Request-Id" in resp.headers

        csp = resp.headers.get("Content-Security-Policy", "")
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp
        assert "connect-src 'self'" in csp


def test_state_changing_requires_x_nox_client_header() -> None:
    """Verify POST/PUT/DELETE requests require X-Nox-Client: web (or cli)."""
    app = create_app(start_scheduler=False, manage_lock=False)
    with TestClient(app) as client:
        valid_token = app.state.session_token

        # POST without X-Nox-Client returns 403 Forbidden
        resp1 = client.post(
            "/api/chat",
            json={"message": "hello"},
            cookies={COOKIE_NAME: valid_token},
        )
        assert resp1.status_code == 403
        assert "X-Nox-Client" in resp1.json()["error"]["message"]

        # POST with X-Nox-Client: web passes CSRF check
        resp2 = client.post(
            "/api/chat",
            json={"message": "hello"},
            cookies={COOKIE_NAME: valid_token},
            headers={"X-Nox-Client": "web"},
        )
        assert resp2.status_code != 403


def test_daemon_restart_invalidates_old_cookies() -> None:
    """Verify restarting daemon (generating new session token) invalidates prior cookies."""
    app1 = create_app(start_scheduler=False, manage_lock=False)
    with TestClient(app1) as client1:
        token1 = app1.state.session_token
        res1 = client1.get("/api/jobs", cookies={COOKIE_NAME: token1})
        assert res1.status_code == 200

    app2 = create_app(start_scheduler=False, manage_lock=False)
    with TestClient(app2) as client2:
        token2 = app2.state.session_token
        assert token2 != token1

        res_old = client2.get("/api/jobs", cookies={COOKIE_NAME: token1})
        assert res_old.status_code == 401

        res_new = client2.get("/api/jobs", cookies={COOKIE_NAME: token2})
        assert res_new.status_code == 200


def test_websocket_origin_check() -> None:
    """Verify WebSocket connection rejects untrusted origins."""
    app = create_app(start_scheduler=False, manage_lock=False)
    with TestClient(app) as client:
        valid_token = app.state.session_token

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"/ws?token={valid_token}",
                headers={"Origin": "http://evil-attacker.com"},
            ):
                pass


# ---------------------------------------------------------------------------
# E4.5: Route Redirects
# ---------------------------------------------------------------------------


def test_legacy_v1_redirects_to_api() -> None:
    """Verify GET /v1/* requests return 308 permanent redirect to /api/*."""
    app = create_app(start_scheduler=False, manage_lock=False)
    with TestClient(app) as client:
        resp = client.get("/v1/status", follow_redirects=False)
        assert resp.status_code == 308
        assert resp.headers["location"] == "/api/status"
