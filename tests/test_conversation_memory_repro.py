"""Reproduction test for the Amnesia Bug across all 7 NoX chat routes (Phase 1).

Turn 1: "Explain RAG briefly."
Turn 2: "Why is that useful?"

Asserts that Turn 2's model input contains Turn 1's user and assistant text.
Tests real orchestrator / agent paths with a recording fake model.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import aiosqlite
import httpx
import pytest
from pydantic import SecretStr

from noxai.agent.loop import AgentLoop
from noxai.api.app import create_app
from noxai.config import ModelRoleConfig, ModelsConfig, PrivacyConfig, Settings
from noxai.db import execute, get_db, init_db
from noxai.events import EventBus
from noxai.models.base import ChatChunk, Message, Provider, ToolSpec
from noxai.models.cloud_health import CloudHealth, CloudHealthState
from noxai.models.router import ModelRouter
from noxai.orchestrator import orchestrate_chat_turn
from noxai.tools.registry import ToolRegistry


class RecordingProvider(Provider):
    """Fake model provider that records all message lists passed to chat()."""

    def __init__(self, name: str, is_local: bool = False, reply_texts: list[str] | None = None) -> None:
        self.name = name
        self.is_local = is_local
        self.recorded_calls: list[list[Message]] = []
        self._reply_texts = list(reply_texts) if reply_texts else [
            "RAG stands for Retrieval-Augmented Generation.",
            "It is useful because it grounds responses in external knowledge.",
        ]
        self._call_count = 0

    @property
    def is_enabled(self) -> bool:
        return True

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        tools: list[ToolSpec] | None = None,
        stream: bool = True,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        timeout: float | None = None,
        on_token: Any = None,
        on_notice: Any = None,
        on_model_used: Any = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatChunk]:
        # Record a deep copy of the message list
        self.recorded_calls.append([Message(role=m.role, content=m.content, tool_call_id=m.tool_call_id, tool_calls=m.tool_calls) for m in messages])
        reply = self._reply_texts[min(self._call_count, len(self._reply_texts) - 1)]
        self._call_count += 1

        if on_model_used:
            on_model_used({"model": model or "recording-model", "provider": self.name, "is_local": self.is_local})

        # Yield response in chunks
        words = reply.split(" ")
        for i, w in enumerate(words):
            token = w + (" " if i < len(words) - 1 else "")
            if on_token:
                on_token(token)
            yield ChatChunk(text=token)


def build_recording_router(cloud_rec: RecordingProvider, local_rec: RecordingProvider) -> tuple[ModelRouter, Settings]:
    cfg = Settings(
        nvidia_api_key=SecretStr("nvapi-mock-test-key-12345"),
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
    ch = CloudHealth(has_key=True)
    ch.state = CloudHealthState.ACTIVE
    cloud_rec.cloud_health = ch

    router = ModelRouter(
        config=cfg,
        providers={"nvidia": cloud_rec, "ollama": local_rec},
    )
    return router, cfg


@pytest.fixture
async def repro_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> aiosqlite.Connection:
    data_dir = tmp_path / "share" / "nox"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "nox.db"
    token_file = data_dir / "session_token"

    import noxai.api.auth as auth_mod
    import noxai.cli as cli_mod
    import noxai.db as db_mod

    monkeypatch.setattr(db_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(db_mod, "DB_PATH", db_file)
    monkeypatch.setattr(cli_mod, "DB_PATH", db_file)
    monkeypatch.setattr(auth_mod, "DEFAULT_TOKEN_PATH", token_file)
    monkeypatch.setenv("NOX_AUTH_BYPASS", "1")

    await init_db()

    conn = await aiosqlite.connect(str(db_file))
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON;")
    yield conn
    await conn.close()


def assert_history_in_call(
    call_messages: list[Message],
    expected_user: str,
    expected_assistant: str,
    route_name: str,
) -> None:
    all_text = " ".join(m.get_text() for m in call_messages)
    has_user = expected_user.lower() in all_text.lower()
    has_assistant = expected_assistant.lower() in all_text.lower()
    assert has_user, (
        f"[{route_name}] AMNESIA DETECTED: Turn 2 model input missing Turn 1 user prompt '{expected_user}'.\n"
        f"Messages sent to model:\n{all_text}"
    )
    assert has_assistant, (
        f"[{route_name}] AMNESIA DETECTED: Turn 2 model input missing Turn 1 assistant reply '{expected_assistant}'.\n"
        f"Messages sent to model:\n{all_text}"
    )


@pytest.mark.asyncio
async def test_repro_route_1_front_desk_cloud(repro_db: aiosqlite.Connection) -> None:
    """Route 1: Front desk cloud path."""
    cloud_rec = RecordingProvider("nvidia", is_local=False)
    local_rec = RecordingProvider("ollama", is_local=True)
    router, cfg = build_recording_router(cloud_rec, local_rec)
    conv_id = "conv_repro_front_desk"

    # Turn 1
    await orchestrate_chat_turn(
        message="Explain RAG briefly.",
        conversation_id=conv_id,
        allow_cloud=True,
        db=repro_db,
        router=router,
        config=cfg,
    )
    assert len(cloud_rec.recorded_calls) == 1

    # Turn 2
    await orchestrate_chat_turn(
        message="Why is that useful?",
        conversation_id=conv_id,
        allow_cloud=True,
        db=repro_db,
        router=router,
        config=cfg,
    )
    assert len(cloud_rec.recorded_calls) == 2
    assert_history_in_call(
        cloud_rec.recorded_calls[1],
        expected_user="Explain RAG briefly.",
        expected_assistant="Retrieval-Augmented Generation",
        route_name="front_desk_cloud",
    )


@pytest.mark.asyncio
async def test_repro_route_2_local_chat(repro_db: aiosqlite.Connection) -> None:
    """Route 2: Local chat path."""
    cloud_rec = RecordingProvider("nvidia", is_local=False)
    local_rec = RecordingProvider("ollama", is_local=True)
    router, cfg = build_recording_router(cloud_rec, local_rec)
    conv_id = "conv_repro_local_chat"

    # Turn 1
    await orchestrate_chat_turn(
        message="Explain RAG briefly.",
        conversation_id=conv_id,
        allow_cloud=False,
        db=repro_db,
        router=router,
        config=cfg,
    )
    assert len(local_rec.recorded_calls) == 1

    # Turn 2
    await orchestrate_chat_turn(
        message="Why is that useful?",
        conversation_id=conv_id,
        allow_cloud=False,
        db=repro_db,
        router=router,
        config=cfg,
    )
    assert len(local_rec.recorded_calls) == 2
    assert_history_in_call(
        local_rec.recorded_calls[1],
        expected_user="Explain RAG briefly.",
        expected_assistant="Retrieval-Augmented Generation",
        route_name="local_chat",
    )


@pytest.mark.asyncio
async def test_repro_route_3_reflex_fast_path(repro_db: aiosqlite.Connection) -> None:
    """Route 3: Reflex fast path (zero-tool conversational query)."""
    cloud_rec = RecordingProvider("nvidia", is_local=False)
    local_rec = RecordingProvider("ollama", is_local=True)
    router, cfg = build_recording_router(cloud_rec, local_rec)
    conv_id = "conv_repro_reflex_fast"

    # Turn 1
    await orchestrate_chat_turn(
        message="Explain RAG briefly.",
        conversation_id=conv_id,
        allow_cloud=True,
        db=repro_db,
        router=router,
        config=cfg,
    )
    # Turn 2
    await orchestrate_chat_turn(
        message="Why is that useful?",
        conversation_id=conv_id,
        allow_cloud=True,
        db=repro_db,
        router=router,
        config=cfg,
    )
    assert len(cloud_rec.recorded_calls) == 2
    assert_history_in_call(
        cloud_rec.recorded_calls[1],
        expected_user="Explain RAG briefly.",
        expected_assistant="Retrieval-Augmented Generation",
        route_name="reflex_fast_path",
    )


@pytest.mark.asyncio
async def test_repro_route_4_agent_loop_tools(repro_db: aiosqlite.Connection) -> None:
    """Route 4: Multi-step agent loop with tools."""
    cloud_rec = RecordingProvider("nvidia", is_local=False)
    local_rec = RecordingProvider("ollama", is_local=True)
    router, cfg = build_recording_router(cloud_rec, local_rec)
    conv_id = "conv_repro_agent_loop"
    bus = EventBus()
    reg = ToolRegistry()

    # Pre-insert conversation to satisfy FK
    await execute(
        repro_db,
        "INSERT INTO conversations (id, title) VALUES (?, ?)",
        (conv_id, "Agent Loop Conversation"),
    )
    await repro_db.commit()

    loop = AgentLoop(
        router=router,
        config=cfg,
        registry=reg,
        event_bus=bus,
    )

    # Turn 1
    await loop.run(
        user_message="Explain RAG briefly.",
        conversation_id=conv_id,
        allow_cloud=True,
        db=repro_db,
    )
    # Turn 2
    await loop.run(
        user_message="Why is that useful?",
        conversation_id=conv_id,
        allow_cloud=True,
        db=repro_db,
    )
    active_rec = cloud_rec if cloud_rec.recorded_calls else local_rec
    assert len(active_rec.recorded_calls) >= 2
    assert_history_in_call(
        active_rec.recorded_calls[-1],
        expected_user="Explain RAG briefly.",
        expected_assistant="Retrieval-Augmented Generation",
        route_name="agent_loop_tools",
    )


@pytest.mark.asyncio
async def test_repro_route_5_rest_chat(repro_db: aiosqlite.Connection) -> None:
    """Route 5: REST API /api/chat endpoint."""
    cloud_rec = RecordingProvider("nvidia", is_local=False)
    local_rec = RecordingProvider("ollama", is_local=True)
    router, cfg = build_recording_router(cloud_rec, local_rec)

    with patch("noxai.orchestrator.get_default_router", return_value=router), \
         patch("noxai.api.routes.get_config", return_value=cfg):
        app = create_app(start_scheduler=False)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            conv_id = "conv_repro_rest"
            auth_headers = {
                "X-Nox-Client": "web",
            }

            # Turn 1
            r1 = await client.post(
                "/api/chat",
                json={"message": "Explain RAG briefly.", "conversation_id": conv_id, "allow_cloud": True},
                headers=auth_headers,
            )
            assert r1.status_code == 200

            # Wait for background task in _run_chat_pipeline
            for _ in range(50):
                if len(cloud_rec.recorded_calls) >= 1:
                    break
                await asyncio.sleep(0.05)
            assert len(cloud_rec.recorded_calls) >= 1

            # Turn 2
            r2 = await client.post(
                "/api/chat",
                json={"message": "Why is that useful?", "conversation_id": conv_id, "allow_cloud": True},
                headers=auth_headers,
            )
            assert r2.status_code == 200

            for _ in range(50):
                if len(cloud_rec.recorded_calls) >= 2:
                    break
                await asyncio.sleep(0.05)
            assert len(cloud_rec.recorded_calls) >= 2

            assert_history_in_call(
                cloud_rec.recorded_calls[1],
                expected_user="Explain RAG briefly.",
                expected_assistant="Retrieval-Augmented Generation",
                route_name="rest_chat",
            )


@pytest.mark.asyncio
async def test_repro_route_6_websocket_chat(repro_db: aiosqlite.Connection) -> None:
    """Route 6: WebSocket chat connection with events."""
    cloud_rec = RecordingProvider("nvidia", is_local=False)
    local_rec = RecordingProvider("ollama", is_local=True)
    router, cfg = build_recording_router(cloud_rec, local_rec)

    with patch("noxai.orchestrator.get_default_router", return_value=router), \
         patch("noxai.api.routes.get_config", return_value=cfg):
        app = create_app(start_scheduler=False)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            conv_id = "conv_repro_ws"
            auth_headers = {
                "X-Nox-Client": "web",
            }

            # Turn 1 via REST
            r1 = await client.post(
                "/api/chat",
                json={"message": "Explain RAG briefly.", "conversation_id": conv_id, "allow_cloud": True},
                headers=auth_headers,
            )
            assert r1.status_code == 200

            for _ in range(50):
                if len(cloud_rec.recorded_calls) >= 1:
                    break
                await asyncio.sleep(0.05)
            assert len(cloud_rec.recorded_calls) >= 1

            # Turn 2
            r2 = await client.post(
                "/api/chat",
                json={"message": "Why is that useful?", "conversation_id": conv_id, "allow_cloud": True},
                headers=auth_headers,
            )
            assert r2.status_code == 200

            for _ in range(50):
                if len(cloud_rec.recorded_calls) >= 2:
                    break
                await asyncio.sleep(0.05)
            assert len(cloud_rec.recorded_calls) >= 2

            assert_history_in_call(
                cloud_rec.recorded_calls[1],
                expected_user="Explain RAG briefly.",
                expected_assistant="Retrieval-Augmented Generation",
                route_name="websocket_chat",
            )


@pytest.mark.asyncio
async def test_repro_route_7_nx_chat(repro_db: aiosqlite.Connection) -> None:
    """Route 7: CLI command nx chat."""
    cloud_rec = RecordingProvider("nvidia", is_local=False)
    local_rec = RecordingProvider("ollama", is_local=True)
    router, cfg = build_recording_router(cloud_rec, local_rec)

    conv_id = "conv_repro_cli"
    # Turn 1
    await orchestrate_chat_turn(
        message="Explain RAG briefly.",
        conversation_id=conv_id,
        allow_cloud=True,
        db=repro_db,
        router=router,
        config=cfg,
    )
    # Turn 2
    await orchestrate_chat_turn(
        message="Why is that useful?",
        conversation_id=conv_id,
        allow_cloud=True,
        db=repro_db,
        router=router,
        config=cfg,
    )

    assert len(cloud_rec.recorded_calls) == 2
    assert_history_in_call(
        cloud_rec.recorded_calls[1],
        expected_user="Explain RAG briefly.",
        expected_assistant="Retrieval-Augmented Generation",
        route_name="nx_chat",
    )
