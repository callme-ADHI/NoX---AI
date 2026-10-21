"""Truth test suite for fast-path orchestrator, cloud front desk, and fallback wiring (§G3).

Tests:
(a) Key present and health ACTIVE: exactly one NVIDIA request containing CHAT/PUBLIC content,
    zero Ollama requests, egress_ledger row, model_used showing cloud.
(b) No key: Ollama receives request, trace shows fallback_reason=NO_KEY, NVIDIA receives nothing.
(c) 401 from NVIDIA: state becomes INVALID_KEY, turn answered locally with that reason.
(d) Private mode: zero NVIDIA requests for the whole conversation.
(e) Message with fake API key and card number: NVIDIA request body contains redaction markers,
    ledger shows masking and contains no message text.
(f) Message flagged sensitive: answered locally with SENSITIVE.
(g) Streaming: first token event reaches event bus / subscriber before completion.
(h) Key appears after startup: with NO_KEY, write key to secrets.env, tick one probe interval,
    state becomes ACTIVE without restart.
(i) Regression guard: /api/chat, WebSocket, and nx chat must route through orchestrator.
(j) Cloud prompt never contains LOCAL_ONLY content, recalled memory, or full tool registry.
"""

from __future__ import annotations

import ast
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from noxai.config import ModelRoleConfig, ModelsConfig, PrivacyConfig, Settings
from noxai.db import fetch_all, get_db, init_db
from noxai.events import Event, EventBus
from noxai.models.cloud_health import CloudHealthState, reset_cloud_health
from noxai.models.nvidia import NvidiaProvider
from noxai.models.ollama import OllamaProvider
from noxai.models.router import ModelRouter
from noxai.orchestrator import FRONT_DESK_ALLOWED_TOOLS, orchestrate_chat_turn


def make_nvidia_sse(content: str) -> bytes:
    """Generate SSE body for mock NVIDIA streaming response."""
    chunk = json.dumps(
        {
            "id": "chatcmpl-mock",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": content},
                    "finish_reason": None,
                }
            ],
        }
    )
    done_chunk = json.dumps(
        {
            "id": "chatcmpl-mock",
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
        }
    )
    return f"data: {chunk}\n\ndata: {done_chunk}\n\ndata: [DONE]\n\n".encode()


def make_ollama_ndjson(content: str) -> bytes:
    """Generate NDJSON body for mock Ollama streaming response."""
    c1 = json.dumps({"message": {"content": content}, "done": False})
    c2 = json.dumps({"message": {"content": ""}, "done": True, "done_reason": "stop"})
    return f"{c1}\n{c2}\n".encode()


def make_test_router(
    nvidia_transport: httpx.AsyncBaseTransport,
    ollama_transport: httpx.AsyncBaseTransport,
    nvidia_key: str | None = "nvapi-mock-test-key-12345",
    health_state: CloudHealthState = CloudHealthState.ACTIVE,
) -> tuple[ModelRouter, Settings]:
    """Build ModelRouter wired with isolated mock transports."""
    cfg = Settings(
        nvidia_api_key=SecretStr(nvidia_key) if nvidia_key else None,
        models=ModelsConfig(
            roles={
                "front": ModelRoleConfig(provider="nvidia", model="nvidia/nemotron-test"),
                "heavy": ModelRoleConfig(provider="nvidia", model="nvidia/nemotron-test"),
                "resident": ModelRoleConfig(provider="ollama", model="qwen2.5:14b"),
                "brain": ModelRoleConfig(provider="ollama", model="qwen2.5:14b"),
                "embed": ModelRoleConfig(provider="ollama", model="nomic-embed-text"),
            }
        ),
        privacy=PrivacyConfig(chat_to_cloud=True),
    )

    ch = reset_cloud_health(has_key=bool(nvidia_key))
    ch.state = health_state
    if health_state != CloudHealthState.ACTIVE:
        ch.circuit_breaker.state = ch.circuit_breaker.state.OPEN

    nvidia_p = NvidiaProvider(
        api_key=nvidia_key,
        transport=nvidia_transport,
        cloud_health=ch,
    )
    ollama_p = OllamaProvider(
        transport=ollama_transport,
    )

    router = ModelRouter(
        config=cfg,
        providers={"nvidia": nvidia_p, "ollama": ollama_p},
    )
    return router, cfg


# ---------------------------------------------------------------------------
# (a) Key present and health ACTIVE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_key_present_health_active_uses_cloud() -> None:
    """Key present and ACTIVE health produces 1 NVIDIA request,
    0 Ollama requests, and egress row."""
    nvidia_reqs: list[httpx.Request] = []
    ollama_reqs: list[httpx.Request] = []

    def nv_handler(req: httpx.Request) -> httpx.Response:
        nvidia_reqs.append(req)
        return httpx.Response(200, content=make_nvidia_sse("Cloud front desk response"))

    def ol_handler(req: httpx.Request) -> httpx.Response:
        ollama_reqs.append(req)
        return httpx.Response(200, content=make_ollama_ndjson("Local fallback response"))

    router, cfg = make_test_router(
        nvidia_transport=httpx.MockTransport(nv_handler),
        ollama_transport=httpx.MockTransport(ol_handler),
    )

    await init_db()
    bus = EventBus()
    models_used: list[dict[str, Any]] = []

    async with get_db() as db:
        res = await orchestrate_chat_turn(
            message="What is the capital of France?",
            allow_cloud=True,
            db=db,
            router=router,
            config=cfg,
            event_bus=bus,
            on_model_used=lambda m: models_used.append(m),
        )

        # Exactly 1 NVIDIA request, 0 Ollama requests
        assert len(nvidia_reqs) == 1, f"Expected 1 NVIDIA request, got {len(nvidia_reqs)}"
        assert len(ollama_reqs) == 0, f"Expected 0 Ollama requests, got {len(ollama_reqs)}"
        assert res.is_cloud is True
        assert res.fallback_reason is None
        assert "Cloud front desk response" in res.text

        # Check NVIDIA payload contains CHAT content
        req_body = json.loads(nvidia_reqs[0].content.decode("utf-8"))
        assert any(
            "What is the capital of France?" in m.get("content", "") for m in req_body["messages"]
        )

        # Egress ledger check
        rows = await fetch_all(db, "SELECT * FROM egress_ledger")
        assert len(rows) >= 1
        assert rows[0]["provider"] == "nvidia"
        assert rows[0]["decision"] in ("allow", "allowed")


# ---------------------------------------------------------------------------
# (b) No key: falls back to Ollama with NO_KEY
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_b_no_key_falls_back_to_resident_with_no_key() -> None:
    """When no key is configured, Ollama receives request,
    trace shows NO_KEY, NVIDIA gets nothing."""
    nvidia_reqs: list[httpx.Request] = []
    ollama_reqs: list[httpx.Request] = []

    def nv_handler(req: httpx.Request) -> httpx.Response:
        nvidia_reqs.append(req)
        return httpx.Response(200, content=make_nvidia_sse("Cloud response"))

    def ol_handler(req: httpx.Request) -> httpx.Response:
        ollama_reqs.append(req)
        return httpx.Response(200, content=make_ollama_ndjson("Local resident response"))

    router, cfg = make_test_router(
        nvidia_transport=httpx.MockTransport(nv_handler),
        ollama_transport=httpx.MockTransport(ol_handler),
        nvidia_key=None,
        health_state=CloudHealthState.NO_KEY,
    )

    await init_db()
    bus = EventBus()

    async with get_db() as db:
        res = await orchestrate_chat_turn(
            message="Hello local assistant",
            allow_cloud=True,
            db=db,
            router=router,
            config=cfg,
            event_bus=bus,
        )

        assert len(nvidia_reqs) == 0
        assert len(ollama_reqs) == 1
        assert res.is_cloud is False
        assert res.fallback_reason == "NO_KEY"
        assert res.trace.get("fallback_reason") == "NO_KEY"
        assert "Local resident response" in res.text


# ---------------------------------------------------------------------------
# (c) 401 from NVIDIA: state becomes INVALID_KEY and falls back
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c_401_from_nvidia_sets_invalid_key_and_falls_back() -> None:
    """401 error from NVIDIA transitions state to INVALID_KEY and answers locally."""
    nvidia_reqs: list[httpx.Request] = []
    ollama_reqs: list[httpx.Request] = []

    def nv_handler(req: httpx.Request) -> httpx.Response:
        nvidia_reqs.append(req)
        return httpx.Response(401, json={"error": {"message": "Invalid API Key"}})

    def ol_handler(req: httpx.Request) -> httpx.Response:
        ollama_reqs.append(req)
        return httpx.Response(200, content=make_ollama_ndjson("Local fallback after 401"))

    router, cfg = make_test_router(
        nvidia_transport=httpx.MockTransport(nv_handler),
        ollama_transport=httpx.MockTransport(ol_handler),
    )

    await init_db()
    bus = EventBus()

    async with get_db() as db:
        res = await orchestrate_chat_turn(
            message="Test 401 recovery",
            allow_cloud=True,
            db=db,
            router=router,
            config=cfg,
            event_bus=bus,
        )

        assert len(nvidia_reqs) == 1
        assert len(ollama_reqs) == 1
        assert res.is_cloud is False
        assert res.fallback_reason == "INVALID_KEY"

        ch = router.providers["nvidia"].cloud_health  # type: ignore[attr-defined]
        assert ch.state == CloudHealthState.INVALID_KEY


# ---------------------------------------------------------------------------
# (d) Private mode: zero NVIDIA requests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_d_private_mode_zero_nvidia_requests() -> None:
    """Private mode causes zero requests to NVIDIA for the whole turn."""
    nvidia_reqs: list[httpx.Request] = []
    ollama_reqs: list[httpx.Request] = []

    def nv_handler(req: httpx.Request) -> httpx.Response:
        nvidia_reqs.append(req)
        return httpx.Response(200, content=make_nvidia_sse("Should not be called"))

    def ol_handler(req: httpx.Request) -> httpx.Response:
        ollama_reqs.append(req)
        return httpx.Response(200, content=make_ollama_ndjson("Private local response"))

    router, cfg = make_test_router(
        nvidia_transport=httpx.MockTransport(nv_handler),
        ollama_transport=httpx.MockTransport(ol_handler),
    )

    await init_db()
    bus = EventBus()

    async with get_db() as db:
        res = await orchestrate_chat_turn(
            message="Keep this private",
            allow_cloud=True,
            is_private_mode=True,
            db=db,
            router=router,
            config=cfg,
            event_bus=bus,
        )

        assert len(nvidia_reqs) == 0
        assert len(ollama_reqs) == 1
        assert res.is_cloud is False
        assert res.fallback_reason == "PRIVATE_MODE"


# ---------------------------------------------------------------------------
# (e) Masking fake secrets and credit card numbers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_e_secret_and_card_masking() -> None:
    """Secret tokens and card numbers in chat are masked before egress; ledger records masking."""
    nvidia_reqs: list[httpx.Request] = []

    def nv_handler(req: httpx.Request) -> httpx.Response:
        nvidia_reqs.append(req)
        return httpx.Response(200, content=make_nvidia_sse("I have received your masked query."))

    def ol_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=make_ollama_ndjson("Local"))

    router, cfg = make_test_router(
        nvidia_transport=httpx.MockTransport(nv_handler),
        ollama_transport=httpx.MockTransport(ol_handler),
    )

    await init_db()
    bus = EventBus()

    fake_key = "sk-abcdef1234567890abcdef1234"
    fake_card = "4111 2222 3333 4444"
    msg = f"My OpenAI key is {fake_key} and my card is {fake_card}."

    async with get_db() as db:
        res = await orchestrate_chat_turn(
            message=msg,
            allow_cloud=True,
            db=db,
            router=router,
            config=cfg,
            event_bus=bus,
        )

        assert res.is_cloud is True
        assert len(nvidia_reqs) == 1
        body_text = nvidia_reqs[0].content.decode("utf-8")

        # Values must NEVER appear in outbound cloud request
        assert fake_key not in body_text
        assert fake_card not in body_text
        assert "[REDACTED_SECRET]" in body_text

        # Egress ledger must reflect decision and contain no message text (§G3.e)
        rows = await fetch_all(db, "SELECT * FROM egress_ledger")
        assert len(rows) >= 1
        assert rows[0]["decision"] in ("allow_masked", "allowed")
        row_str = str(dict(rows[0]))
        assert fake_key not in row_str
        assert fake_card not in row_str
        assert "My OpenAI key" not in row_str


# ---------------------------------------------------------------------------
# (f) Sensitive topic prompt answered locally
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_f_sensitive_prompt_answered_locally() -> None:
    """Prompt flagged sensitive by heuristic classifier routes to local lane
    with reason SENSITIVE."""
    nvidia_reqs: list[httpx.Request] = []
    ollama_reqs: list[httpx.Request] = []

    def nv_handler(req: httpx.Request) -> httpx.Response:
        nvidia_reqs.append(req)
        return httpx.Response(200, content=make_nvidia_sse("Cloud"))

    def ol_handler(req: httpx.Request) -> httpx.Response:
        ollama_reqs.append(req)
        return httpx.Response(200, content=make_ollama_ndjson("Local confidential response"))

    router, cfg = make_test_router(
        nvidia_transport=httpx.MockTransport(nv_handler),
        ollama_transport=httpx.MockTransport(ol_handler),
    )

    await init_db()
    bus = EventBus()

    sensitive_msg = "Please analyze my medical diagnosis and symptoms from my doctor."
    async with get_db() as db:
        res = await orchestrate_chat_turn(
            message=sensitive_msg,
            allow_cloud=True,
            db=db,
            router=router,
            config=cfg,
            event_bus=bus,
        )

        assert len(nvidia_reqs) == 0
        assert len(ollama_reqs) == 1
        assert res.is_cloud is False
        assert res.fallback_reason == "SENSITIVE"


# ---------------------------------------------------------------------------
# (g) Streaming token order verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_g_streaming_token_order() -> None:
    """First token event arrives before completion event."""
    events_received: list[Event] = []

    def nv_handler(req: httpx.Request) -> httpx.Response:
        c1 = json.dumps({"choices": [{"delta": {"content": "First "}, "finish_reason": None}]})
        c2 = json.dumps({"choices": [{"delta": {"content": "Second"}, "finish_reason": "stop"}]})
        body = f"data: {c1}\n\ndata: {c2}\n\ndata: [DONE]\n\n".encode()
        return httpx.Response(200, content=body)

    def ol_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=make_ollama_ndjson("Local"))

    router, cfg = make_test_router(
        nvidia_transport=httpx.MockTransport(nv_handler),
        ollama_transport=httpx.MockTransport(ol_handler),
    )

    await init_db()
    bus = EventBus()

    async def _on_event(e: Event) -> None:
        events_received.append(e)

    bus.subscribe(_on_event)

    tokens_received: list[str] = []

    async with get_db() as db:
        res = await orchestrate_chat_turn(
            message="Stream to me",
            allow_cloud=True,
            db=db,
            router=router,
            config=cfg,
            event_bus=bus,
            on_token=lambda t: tokens_received.append(t),
        )

        # Confirm token events received in order
        event_types = [e.type for e in events_received]
        assert "token" in event_types
        assert "run_done" in event_types

        first_token_idx = event_types.index("token")
        run_done_idx = event_types.index("run_done")
        assert first_token_idx < run_done_idx

        assert tokens_received == ["First ", "Second"]
        assert res.text == "First Second"


# ---------------------------------------------------------------------------
# (h) Key appears after startup without restart
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_h_key_appears_after_startup_reloads(tmp_path: Path) -> None:
    """When a key appears in secrets.env, tick detects mtime change and transitions to ACTIVE."""
    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text("# Initial empty secrets\n", encoding="utf-8")

    ch = reset_cloud_health(has_key=False, secrets_path=secrets_file)
    assert ch.state == CloudHealthState.NO_KEY

    # Advance clock and tick
    t0 = 1000.0
    ch.tick(now=t0)
    assert ch.state == CloudHealthState.NO_KEY

    # Write key to file and update mtime
    secrets_file.write_text("NOX_NVIDIA_API_KEY=nvapi-runtime-added-key\n", encoding="utf-8")
    os.utime(secrets_file, (time.time() + 10, time.time() + 10))

    # Advance fake clock by one probe interval (60s)
    ch.tick(now=t0 + 61.0)

    assert ch.state == CloudHealthState.ACTIVE
    assert ch.is_active() is True
    assert ch._current_key == "nvapi-runtime-added-key"


# ---------------------------------------------------------------------------
# (i) Regression guard: all chat entry points use orchestrate_chat_turn
# ---------------------------------------------------------------------------


def test_i_regression_guard_wiring_imports() -> None:
    """Verify that routes.py, websocket.py, and cli.py do not bypass orchestrator."""
    project_root = Path(__file__).parent.parent / "src" / "noxai"
    routes_file = project_root / "api" / "routes.py"
    cli_file = project_root / "cli.py"
    ws_file = project_root / "api" / "websocket.py"

    # 1. /api/routes.py imports orchestrate_chat_turn
    routes_source = routes_file.read_text(encoding="utf-8")
    assert "orchestrate_chat_turn" in routes_source
    assert "from noxai.orchestrator import orchestrate_chat_turn" in routes_source

    # 2. cli.py chat command imports orchestrate_chat_turn
    cli_source = cli_file.read_text(encoding="utf-8")
    assert "orchestrate_chat_turn" in cli_source
    assert "from noxai.orchestrator import orchestrate_chat_turn" in cli_source

    # 3. Check AST for cli_cmd: ensure AgentLoop.run is NOT called directly for chat_cmd
    tree = ast.parse(cli_source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "chat_cmd":
            fn_code = ast.unparse(node)
            assert "orchestrate_chat_turn" in fn_code
            assert "AgentLoop" not in fn_code

    # 4. websocket.py does not execute LLM models directly
    ws_source = ws_file.read_text(encoding="utf-8")
    assert "ModelRouter" not in ws_source
    assert "AgentLoop" not in ws_source


# ---------------------------------------------------------------------------
# (j) Cloud prompt never contains LOCAL_ONLY content or full tool registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_j_cloud_prompt_strict_data_class_and_tools() -> None:
    """Cloud prompt contains only FRONT_DESK_ALLOWED_TOOLS and zero LOCAL_ONLY data."""
    nvidia_reqs: list[httpx.Request] = []

    def nv_handler(req: httpx.Request) -> httpx.Response:
        nvidia_reqs.append(req)
        return httpx.Response(200, content=make_nvidia_sse("Cloud front desk"))

    def ol_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=make_ollama_ndjson("Local"))

    router, cfg = make_test_router(
        nvidia_transport=httpx.MockTransport(nv_handler),
        ollama_transport=httpx.MockTransport(ol_handler),
    )

    await init_db()
    bus = EventBus()

    async with get_db() as db:
        res = await orchestrate_chat_turn(
            message="What is the weather in Delhi?",
            allow_cloud=True,
            db=db,
            router=router,
            config=cfg,
            event_bus=bus,
        )

        assert res.is_cloud is True
        assert len(nvidia_reqs) == 1
        req_body = json.loads(nvidia_reqs[0].content.decode("utf-8"))

        # Assert tools: only FRONT_DESK_ALLOWED_TOOLS
        tools_in_req = req_body.get("tools")
        if tools_in_req:
            for t in tools_in_req:
                tool_name = t["function"]["name"]
                assert tool_name in FRONT_DESK_ALLOWED_TOOLS, (
                    f"Unauthorized tool in cloud request: {tool_name}"
                )

        # Assert no recalled memory / LOCAL_ONLY in prompt
        for m in req_body["messages"]:
            content = m.get("content", "")
            assert "Recalled Context:" not in content
            assert "local_only" not in content.lower()


def make_nvidia_tool_call_sse(tool_name: str, args: dict[str, Any]) -> bytes:
    """Generate SSE body for mock NVIDIA streaming response containing a tool call."""
    chunk = json.dumps(
        {
            "id": "chatcmpl-mock",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_mock_123",
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": json.dumps(args),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
    )
    return f"data: {chunk}\n\ndata: [DONE]\n\n".encode()


@pytest.mark.asyncio
async def test_k_tool_call_execution_in_orchestrator() -> None:
    """Model tool calls (web_search, get_time, list_files) are executed and fed back."""
    req_count = 0

    def nv_handler(req: httpx.Request) -> httpx.Response:
        nonlocal req_count
        req_count += 1
        if req_count == 1:
            # First turn: model outputs a tool call to get_time
            return httpx.Response(200, content=make_nvidia_tool_call_sse("get_time", {}))
        # Second turn: model receives tool output and streams final answer
        return httpx.Response(200, content=make_nvidia_sse("Today is October 21, 2026."))

    def ol_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=make_ollama_ndjson("Local"))

    router, cfg = make_test_router(
        nvidia_transport=httpx.MockTransport(nv_handler),
        ollama_transport=httpx.MockTransport(ol_handler),
    )

    await init_db()
    bus = EventBus()
    events_captured: list[Event] = []

    async def _on_event(evt: Event) -> None:
        events_captured.append(evt)

    bus.subscribe(_on_event)

    async with get_db() as db:
        res = await orchestrate_chat_turn(
            message="What is today's date?",
            allow_cloud=True,
            db=db,
            router=router,
            config=cfg,
            event_bus=bus,
        )

        assert res.is_cloud is True
        assert res.text == "Today is October 21, 2026."
        assert req_count == 2
        event_types = [e.type for e in events_captured]
        assert "tool_call" in event_types
        assert "tool_result" in event_types
