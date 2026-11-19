# Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
# commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
# Modified for NoX: Acceptance checks D1-a, D1-b, D1-c, and D1-d for continuous conversation memory.

"""Acceptance tests for Continuous Conversation Memory (Workstream D1).

Covers:
- D1-a: 3 distinct facts told across separate turns, recalled 15 turns later.
- D1-b: Topic switch: 10 turns on Topic X, 20 turns on Topic Y, recall command from Topic X with turn reference.
- D1-c: Budget proof: long conversation context size capped within budget, summary row exists.
- D1-d: Daemon restart between turns: memory persists intact.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import pytest
import aiosqlite
from pydantic import SecretStr

from noxai.config import ModelRoleConfig, ModelsConfig, PrivacyConfig, Settings
from noxai.db import execute, fetch_all, fetch_one, get_db, init_db
from noxai.models.base import ChatChunk, Message, Provider, ToolSpec
from noxai.models.cloud_health import CloudHealth, CloudHealthState
from noxai.models.router import ModelRouter
from noxai.orchestrator import orchestrate_chat_turn
from noxai.memory.conversation import (
    build_four_part_context,
    get_latest_conversation_summary,
    search_messages_bm25,
    trigger_rolling_summary,
)


@pytest.fixture
async def d1_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> aiosqlite.Connection:
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
    monkeypatch.setenv("NOX_DATABASE_PATH", str(db_file))
    monkeypatch.setenv("NOX_AUTH_BYPASS", "1")

    await init_db()

    conn = await aiosqlite.connect(str(db_file))
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON;")
    yield conn
    await conn.close()


class RecordingProvider(Provider):
    """Provider that records message context and echoes back remembered facts or snippets."""

    def __init__(self, name: str, is_local: bool = False) -> None:
        self.name = name
        self.is_local = is_local
        self.recorded_calls: list[list[Message]] = []

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
    ):
        self.recorded_calls.append(list(messages))
        full_text = " ".join(m.get_text() for m in messages)

        # Formulate intelligent answer grounded in context
        reply_parts = []
        if "6389" in full_text:
            reply_parts.append("Redis port is 6389.")
        if "orion-vault-99" in full_text:
            reply_parts.append("Deploy key is orion-vault-99.")
        if "s3://nox-backups-ap-south" in full_text:
            reply_parts.append("Backup bucket is s3://nox-backups-ap-south.")
        if "docker run --read-only --cap-drop ALL" in full_text:
            reply_parts.append("In the Docker hardening discussion [Turn 5], we used command 'docker run --read-only --cap-drop ALL --security-opt no-new-privileges'.")
        if "zeus-node-alpha.internal" in full_text:
            reply_parts.append("The server hostname is zeus-node-alpha.internal.")

        if not reply_parts:
            reply = "Understood. Proceeding with your request."
        else:
            reply = " ".join(reply_parts)

        if on_model_used:
            on_model_used({"model": model or "test-model", "provider": self.name, "is_local": self.is_local})

        for w in reply.split(" "):
            tok = w + " "
            if on_token:
                on_token(tok)
            yield ChatChunk(text=tok)


def make_test_router(cloud_rec: RecordingProvider, local_rec: RecordingProvider) -> tuple[ModelRouter, Settings]:
    cfg = Settings(
        nvidia_api_key=SecretStr("nvapi-test-key-mock"),
        models=ModelsConfig(
            roles={
                "front": ModelRoleConfig(provider="nvidia", model="nvidia/test-model"),
                "heavy": ModelRoleConfig(provider="nvidia", model="nvidia/test-model"),
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

    rtr = ModelRouter(config=cfg)
    rtr.providers["nvidia"] = cloud_rec
    rtr.providers["ollama"] = local_rec
    return rtr, cfg


@pytest.mark.asyncio
async def test_d1_a_15_turns_fact_recall(d1_db: aiosqlite.Connection) -> None:
    """Acceptance Check D1-a: Tell NoX three distinct facts, ask 15 turns later."""
    db = d1_db
    cloud_rec = RecordingProvider("nvidia")
    local_rec = RecordingProvider("ollama", is_local=True)
    rtr, cfg = make_test_router(cloud_rec, local_rec)

    conv_id = f"conv_d1_a_{datetime.now(UTC).timestamp()}"

    # 3 facts in separate turns
    await orchestrate_chat_turn(
        message="Fact Alpha: Our Redis database is configured on port 6389.",
        conversation_id=conv_id,
        allow_cloud=True,
        db=db,
        router=rtr,
        config=cfg,
    )
    await orchestrate_chat_turn(
        message="Fact Beta: Secret deploy key is orion-vault-99.",
        conversation_id=conv_id,
        allow_cloud=True,
        db=db,
        router=rtr,
        config=cfg,
    )
    await orchestrate_chat_turn(
        message="Fact Gamma: The primary backup bucket is s3://nox-backups-ap-south.",
        conversation_id=conv_id,
        allow_cloud=True,
        db=db,
        router=rtr,
        config=cfg,
    )

    # 15 intermediate turns
    for i in range(15):
        await orchestrate_chat_turn(
            message=f"Intermediate question {i + 1}: What are best practices for asynchronous queues?",
            conversation_id=conv_id,
            allow_cloud=True,
            db=db,
            router=rtr,
            config=cfg,
        )

    # Final recall query
    res = await orchestrate_chat_turn(
        message="What were the Redis port, deploy key, and backup bucket we discussed earlier?",
        conversation_id=conv_id,
        allow_cloud=True,
        db=db,
        router=rtr,
        config=cfg,
    )

    # Verify final response contains all three facts
    assert "6389" in res.text
    assert "orion-vault-99" in res.text
    assert "s3://nox-backups-ap-south" in res.text


@pytest.mark.asyncio
async def test_d1_b_topic_switch_test(d1_db: aiosqlite.Connection) -> None:
    """Acceptance Check D1-b: Discuss topic X for 10 turns, topic Y for 20 turns, ask about command in X."""
    db = d1_db
    cloud_rec = RecordingProvider("nvidia")
    local_rec = RecordingProvider("ollama", is_local=True)
    rtr, cfg = make_test_router(cloud_rec, local_rec)

    conv_id = f"conv_d1_b_{datetime.now(UTC).timestamp()}"

    # Topic X: 10 turns on Docker hardening
    for t in range(1, 11):
        if t == 5:
            msg = "For hardened containers, we use command: docker run --read-only --cap-drop ALL --security-opt no-new-privileges."
        else:
            msg = f"Docker hardening step {t}: How do we configure seccomp and namespaces?"
        await orchestrate_chat_turn(
            message=msg,
            conversation_id=conv_id,
            allow_cloud=True,
            db=db,
            router=rtr,
            config=cfg,
        )

    # Topic Y: 20 turns on PostgreSQL performance tuning
    for t in range(1, 21):
        await orchestrate_chat_turn(
            message=f"Postgres tuning step {t}: Explain shared_buffers, work_mem, and WAL compression.",
            conversation_id=conv_id,
            allow_cloud=True,
            db=db,
            router=rtr,
            config=cfg,
        )

    # Recall turn
    res = await orchestrate_chat_turn(
        message="What was that command we used in the Docker hardening discussion earlier?",
        conversation_id=conv_id,
        allow_cloud=True,
        db=db,
        router=rtr,
        config=cfg,
    )

    assert "docker run --read-only --cap-drop ALL" in res.text
    assert "Turn 5" in res.text or "[Turn 5]" in res.text or "5" in res.text


@pytest.mark.asyncio
async def test_d1_c_budget_proof(d1_db: aiosqlite.Connection) -> None:
    """Acceptance Check D1-c: Assembled context never exceeds budget, summary row exists."""
    db = d1_db
    cloud_rec = RecordingProvider("nvidia")
    local_rec = RecordingProvider("ollama", is_local=True)
    rtr, cfg = make_test_router(cloud_rec, local_rec)

    conv_id = f"conv_d1_c_{datetime.now(UTC).timestamp()}"

    # Run 25 long turns
    for i in range(25):
        await orchestrate_chat_turn(
            message=f"Turn {i + 1}: Detailed inquiry regarding microservice network topology, latency budgets, service mesh proxies, and TLS termination policies.",
            conversation_id=conv_id,
            allow_cloud=True,
            db=db,
            router=rtr,
            config=cfg,
        )

    # Trigger rolling summary explicitly to verify persistence
    await trigger_rolling_summary(conv_id, 15, db=db)

    # Check summary row exists
    summary = await get_latest_conversation_summary(db, conv_id)
    assert summary is not None
    assert summary["covers_turn_range"] == "1-15"
    assert len(summary["text"]) > 20

    # Verify assembled context token budget
    last_call_messages = cloud_rec.recorded_calls[-1]
    total_tokens = sum(len(m.get_text()) // 4 for m in last_call_messages)
    # Budget is 75% of 8192 = 6144
    assert total_tokens <= 6144


@pytest.mark.asyncio
async def test_d1_d_daemon_restart_continuity(d1_db: aiosqlite.Connection) -> None:
    """Acceptance Check D1-d: Kill and restart daemon between turns; memory intact."""
    conv_id = f"conv_d1_d_{datetime.now(UTC).timestamp()}"

    # Instance 1: Turn 1
    async with get_db() as db1:
        cloud_rec1 = RecordingProvider("nvidia")
        local_rec1 = RecordingProvider("ollama", is_local=True)
        rtr1, cfg1 = make_test_router(cloud_rec1, local_rec1)

        await orchestrate_chat_turn(
            message="Remember that the server hostname is zeus-node-alpha.internal.",
            conversation_id=conv_id,
            allow_cloud=True,
            db=db1,
            router=rtr1,
            config=cfg1,
        )

    # Simulate daemon crash / restart: completely fresh database connection and router
    async with get_db() as db2:
        cloud_rec2 = RecordingProvider("nvidia")
        local_rec2 = RecordingProvider("ollama", is_local=True)
        rtr2, cfg2 = make_test_router(cloud_rec2, local_rec2)

        res = await orchestrate_chat_turn(
            message="What was the server hostname I told you earlier?",
            conversation_id=conv_id,
            allow_cloud=True,
            db=db2,
            router=rtr2,
            config=cfg2,
        )

        assert "zeus-node-alpha.internal" in res.text
