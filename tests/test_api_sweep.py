"""Comprehensive API and WebSocket sweep tests with realistic database.

Loads a populated database containing 40 facts, 120 episodes, 500 messages,
30 runs, BLOB embeddings with invalid UTF-8, and sweeps all routes and WebSockets.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from noxai.api.app import create_app
from noxai.api.auth import generate_session_token, load_session_token
from noxai.events import global_event_bus, make_event
from tests.fixtures.populate import populate_realistic_db


@pytest.fixture
def realistic_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Path, None, None]:
    """Create and isolate a realistic database and token."""
    data_dir = tmp_path / "share" / "nox"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "nox.db"
    token_file = data_dir / "session_token"

    # Populate realistic database
    populate_realistic_db(db_file)

    # Patch paths across modules
    import noxai.api.auth as auth_mod
    import noxai.db as db_mod

    monkeypatch.setattr(db_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(db_mod, "DB_PATH", db_file)
    monkeypatch.setattr(auth_mod, "DEFAULT_TOKEN_PATH", token_file)

    # Generate token
    generate_session_token(token_file)

    yield db_file


@pytest.fixture
def valid_token(realistic_app: TestClient) -> str:
    tok = load_session_token()
    assert tok is not None
    return str(tok)


@pytest.fixture
def auth_headers(valid_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {valid_token}",
        "X-Nox-Client": "web",
    }


@pytest.fixture
def realistic_app(realistic_env: Path) -> Generator[TestClient, None, None]:
    """TestClient against realistic populated database."""
    app = create_app(start_scheduler=False)
    with TestClient(app) as client:
        yield client


def _check_no_blobs_or_embeddings(obj: Any, path: str = "") -> list[str]:
    """Recursively verify no key or value contains raw BLOBs or forbidden embedding keys."""
    violations: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            current_path = f"{path}.{k}" if path else str(k)
            if k in ("embedding", "vector"):
                violations.append(f"Forbidden key '{k}' found at {current_path}")
            if isinstance(v, bytes):
                violations.append(f"Raw bytes value found at {current_path}")
            violations.extend(_check_no_blobs_or_embeddings(v, current_path))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            current_path = f"{path}[{idx}]"
            if isinstance(item, bytes):
                violations.append(f"Raw bytes value found at {current_path}")
            violations.extend(_check_no_blobs_or_embeddings(item, current_path))
    return violations


# ---------------------------------------------------------------------------
# GET Endpoints Sweep
# ---------------------------------------------------------------------------

GET_ROUTES = [
    ("/api/status", {}),
    ("/api/conversations", {"limit": 10}),
    ("/api/conversations/conv-001", {}),
    ("/api/jobs", {}),
    ("/api/jobs/job_seed_daily", {}),
    ("/api/jobs/job_seed_daily/runs", {"limit": 5}),
    ("/api/jobs/job_custom_1/versions", {}),
    ("/api/briefings", {"limit": 5}),
    ("/api/briefings/1", {}),
    ("/api/memory/facts", {}),
    ("/api/memory/episodes", {}),
    ("/api/memory/search", {"q": "project"}),
    ("/api/audit", {"limit": 20}),
    ("/api/settings", {}),
    ("/api/sources", {}),
]


@pytest.mark.parametrize("route,params", GET_ROUTES)
def test_all_get_endpoints_sweep(
    realistic_app: TestClient,
    auth_headers: dict[str, str],
    route: str,
    params: dict[str, Any],
) -> None:
    """Verify all GET routes return 200, valid JSON, under 2s, with no BLOB leaks."""
    start = time.perf_counter()
    resp = realistic_app.get(route, params=params, headers=auth_headers)
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, f"Route {route} exceeded 2s response time: {elapsed:.3f}s"
    assert resp.status_code == 200, f"Route {route} failed with {resp.status_code}: {resp.text}"

    data = resp.json()
    violations = _check_no_blobs_or_embeddings(data)
    assert not violations, f"BLOB/embedding violations in {route}: {violations}"


# ---------------------------------------------------------------------------
# POST/PATCH/DELETE Endpoints No-500 Sweep
# ---------------------------------------------------------------------------

MUTATION_ROUTES = [
    ("POST", "/api/briefings/1/feedback", {"rating": 1, "note": "Great"}),
    ("POST", "/api/memory/facts/1/pin", {"pinned": True}),
    ("PATCH", "/api/memory/facts/1", {"value": "Updated fact value"}),
    ("PATCH", "/api/settings", {"preferences": {"theme": "light"}}),
    ("POST", "/api/jobs/job_custom_1/restore", {"version": 1}),
]


@pytest.mark.parametrize("method,route,body", MUTATION_ROUTES)
def test_mutation_endpoints_never_return_500(
    realistic_app: TestClient,
    auth_headers: dict[str, str],
    method: str,
    route: str,
    body: dict[str, Any],
) -> None:
    """Verify mutation routes never return 500 internal server error."""
    if method == "POST":
        resp = realistic_app.post(route, json=body, headers=auth_headers)
    elif method == "PATCH":
        resp = realistic_app.patch(route, json=body, headers=auth_headers)
    elif method == "DELETE":
        resp = realistic_app.delete(route, headers=auth_headers)
    else:
        raise ValueError(f"Unsupported method {method}")

    assert resp.status_code != 500, f"{method} {route} returned 500: {resp.text}"


# ---------------------------------------------------------------------------
# WebSocket Sweep
# ---------------------------------------------------------------------------


def test_websocket_event_sweep(
    realistic_app: TestClient,
    valid_token: str,
) -> None:
    """Verify all event types pushed through event bus reach WebSocket client cleanly."""
    with realistic_app.websocket_connect(f"/ws?token={valid_token}") as ws:
        # Send ping and drain any replayed historical events until pong is received
        ws.send_json({"type": "ping"})
        msg = None
        for _ in range(100):
            data = ws.receive_json()
            if data.get("type") == "pong":
                msg = data
                break
        assert msg is not None
        assert msg.get("type") == "pong"

        events_to_test = [
            make_event(
                "chunk", {"text": "Streaming tokens... 🚀", "model": "brain"}, run_id="run-001"
            ),
            make_event(
                "tool_call", {"tool": "read_file", "args": {"path": "/tmp/test"}}, run_id="run-001"
            ),
            make_event(
                "tool_result", {"tool": "read_file", "result": "file contents"}, run_id="run-001"
            ),
            make_event("step_start", {"step": 1}, run_id="run-001"),
            make_event("step_finish", {"step": 1}, run_id="run-001"),
            make_event("done", {"text": "Run completed successfully"}, run_id="run-001"),
            make_event("error", {"error": "Simulated error message"}, run_id="run-001"),
        ]

        for ev in events_to_test:
            assert realistic_app.portal is not None
            realistic_app.portal.call(global_event_bus.publish, ev)
            received = ws.receive_json()
            assert received["type"] == ev.type
            assert received["run_id"] == ev.run_id
            assert received["data"] == ev.data


# ---------------------------------------------------------------------------
# Response Model Byte Scan & Bad Input Tests (Spec E3)
# ---------------------------------------------------------------------------


def test_no_bytes_fields_in_any_response_model() -> None:
    """Scan all pydantic models in routes module; fail if any field is bytes or contains bytes."""
    import inspect
    import typing

    from pydantic import BaseModel

    import noxai.api.routes as routes_mod

    found_models: list[type[BaseModel]] = []
    for attr_name in dir(routes_mod):
        attr = getattr(routes_mod, attr_name)
        if inspect.isclass(attr) and issubclass(attr, BaseModel) and attr is not BaseModel:
            found_models.append(attr)

    assert len(found_models) >= 10, f"Expected multiple models, found {len(found_models)}"

    violations = []
    for model in found_models:
        for field_name, field_info in model.model_fields.items():
            ann = field_info.annotation
            args = typing.get_args(ann)
            if ann is bytes or bytes in args:
                violations.append(f"{model.__name__}.{field_name} has bytes type")

    assert not violations, f"Response models contain bytes fields: {violations}"


def test_error_envelope_and_correlation_id_on_forced_error(
    realistic_app: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unhandled server errors return 500 with standard error envelope."""
    import noxai.api.routes as routes_mod

    async def _failing_auth(cfg):
        raise RuntimeError("Forced simulation error for E3 verification")

    # Temporarily monkeypatch check_gate_authenticated called by get_system_status
    monkeypatch.setattr(routes_mod, "check_gate_authenticated", _failing_auth)

    resp = realistic_app.get("/api/status", headers=auth_headers)
    assert resp.status_code == 500

    data = resp.json()
    assert "error" in data
    err = data["error"]
    assert err["code"] == "internal_error"
    assert err["message"] == "Something went wrong"
    assert "correlation_id" in err
    assert resp.headers.get("X-Request-Id") == err["correlation_id"]


def test_bad_inputs_standard_envelope(
    realistic_app: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Verify bad payloads, wrong types, and limits receive standard error envelope."""
    # 1. Oversized body (>1 MB)
    large_payload = {"message": "A" * 1_050_000}
    resp = realistic_app.post("/api/chat", json=large_payload, headers=auth_headers)
    assert resp.status_code in (413, 400)
    data = resp.json()
    assert "error" in data
    assert "correlation_id" in data["error"]
    assert resp.headers.get("X-Request-Id") == data["error"]["correlation_id"]

    # 2. Invalid JSON
    raw_headers = dict(auth_headers)
    raw_headers["Content-Type"] = "application/json"
    resp = realistic_app.post("/api/chat", content="not-a-json-object", headers=raw_headers)
    assert resp.status_code == 422
    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == "validation_error"
    assert "correlation_id" in data["error"]

    # 3. Wrong types (string instead of integer rating)
    resp = realistic_app.post(
        "/api/briefings/1/feedback",
        json={"rating": "invalid_string_not_an_int"},
        headers=auth_headers,
    )
    assert resp.status_code == 422
    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == "validation_error"

    # 4. Negative limit in query
    resp = realistic_app.get("/api/conversations?limit=-10", headers=auth_headers)
    assert resp.status_code == 422
    data = resp.json()
    assert "error" in data

    # 5. Excessively huge limit in query
    resp = realistic_app.get("/api/conversations?limit=999999", headers=auth_headers)
    assert resp.status_code == 422
    data = resp.json()
    assert "error" in data

    # 6. SQL-ish string in input (safely handled, not 500)
    resp = realistic_app.post(
        "/api/chat",
        json={"message": "' OR 1=1; DROP TABLE facts; --"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "run_id" in data
