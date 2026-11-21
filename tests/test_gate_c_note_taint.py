"""Automated proof for Gate C: Retrieved note chunks are strictly taint-proof (Session N Gate C).

Invariant: If a retrieved note chunk has sys_taint with PRIVATE or comes from the user's
Obsidian/Vault/Study notes, or if a message chunk comes from a private channel, the outgoing
LLM request MUST NOT be routed to a cloud provider unless the user explicitly overrode it.

Verified using RecordingTransport: asserts no text or path from the note appears in any cloud
request body, and that the router chose the local model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from noxai.config import get_config
from noxai.db import get_db, init_db
from noxai.memory.conversation import build_four_part_context
from noxai.memory.docs import index_documents
from noxai.models.base import ChatChunk, Message
from noxai.models.keypool import KeyPool
from noxai.models.nvidia import NvidiaProvider
from noxai.models.router import ModelRouter
from noxai.orchestrator import orchestrate_chat_turn
from noxai.taint import Content, DataClass, TaintFlag


class RecordingTransport(httpx.AsyncBaseTransport):
    """Real HTTP transport that records all requests without sending them over the wire."""

    def __init__(self) -> None:
        self.recorded_requests: list[dict[str, Any]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body_bytes = await request.aread()
        body_str = body_bytes.decode("utf-8", errors="replace")
        try:
            body_json = json.loads(body_str)
        except Exception:
            body_json = None

        self.recorded_requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "headers": dict(request.headers),
                "raw_body": body_str,
                "json_body": body_json,
            }
        )

        resp_data = {
            "id": "chatcmpl-recording-mock",
            "object": "chat.completion",
            "created": 1234567,
            "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Recorded transport reply."},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        }
        return httpx.Response(200, json=resp_data)


class MockLocalProvider:
    name = "ollama"
    is_local = True

    async def chat(self, messages, model, **kwargs):
        yield ChatChunk(text="Local model successfully processed note context.")

    async def embed(self, texts, **kwargs):
        return [[0.1] * 8 for _ in texts]


@pytest.mark.asyncio
async def test_note_retrieval_taint_proof_local_reroute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify that retrieved notes taint context with PRIVATE/LOCAL_ONLY
    and router chooses local model.
    """
    monkeypatch.setattr("noxai.db.DB_PATH", tmp_path / "test_gate_c.db")
    monkeypatch.setattr("noxai.db.DATA_DIR", tmp_path)
    await init_db()

    # 1. Create a simulated vault directory and note
    vault_dir = tmp_path / "Vault"
    vault_dir.mkdir()
    note_file = vault_dir / "SecretSpecs.md"
    secret_note_snippet = "TOP_SECRET_TITAN_CORE_V3_ALGORITHM_NONCE_77123"
    note_content = (
        f"# Project Titan Architecture\n\n"
        f"This document contains strictly private system specifications.\n\n"
        f"Internal key: {secret_note_snippet}\n"
        f"Deployment target: Arch Linux isolated enclave.\n"
    )
    note_file.write_text(note_content, encoding="utf-8")

    # 2. Index the note in document memory
    async with get_db() as db:
        stats = await index_documents(roots=[str(vault_dir)], db=db)
        assert stats.indexed >= 1

    # 3. Setup RecordingTransport for cloud provider
    cfg = get_config()
    recording_transport = RecordingTransport()
    pool = KeyPool(config=cfg, keys=["nvapi-dummy-gate-c-key"], transport=recording_transport)
    cloud_provider = NvidiaProvider(key_pool=pool, transport=recording_transport)
    local_provider = MockLocalProvider()

    router = ModelRouter(
        config=cfg,
        providers={"nvidia": cloud_provider, "ollama": local_provider},
    )

    # 4. Turn 1: Normal public chat with allow_cloud=True -> cloud is called
    async with get_db() as db:
        await orchestrate_chat_turn(
            message="What is the speed of sound in air?",
            conversation_id="conv_gate_c",
            db=db,
            router=router,
            config=cfg,
            allow_cloud=True,
        )

    assert len(recording_transport.recorded_requests) >= 1
    assert "speed of sound" in recording_transport.recorded_requests[-1]["raw_body"].lower()

    req_count_before = len(recording_transport.recorded_requests)

    # 5. Turn 2: Query referencing user's notes -> note chunks retrieved into context
    note_query = "What did I write in my notes about Project Titan architecture?"
    models_used: list[dict[str, Any]] = []

    def on_model_used_cb(info: dict[str, Any]) -> None:
        models_used.append(info)

    async with get_db() as db:
        turn_result = await orchestrate_chat_turn(
            message=note_query,
            conversation_id="conv_gate_c",
            db=db,
            router=router,
            config=cfg,
            allow_cloud=True,  # Even though cloud is enabled...
            on_model_used=on_model_used_cb,
        )

    # 6. Assertions for Gate C
    # A. Output came from the local model
    assert "Local model" in turn_result.text

    # B. The model used record proves fallback from front (cloud)
    # to resident/brain (local) due to PRIVACY_BLOCKED
    assert len(models_used) >= 1
    used = models_used[-1]
    assert used["is_local"] is True
    assert used["provider"] == "ollama"
    assert "PRIVACY_BLOCKED" in str(used.get("fallback_reason", ""))

    # C. Absolute zero egress: no request sent during Turn 2, and no note text in any request body
    new_requests = recording_transport.recorded_requests[req_count_before:]
    assert len(new_requests) == 0, (
        f"Expected 0 cloud requests for private note query, got {len(new_requests)}"
    )

    for req in recording_transport.recorded_requests:
        assert secret_note_snippet not in req["raw_body"], (
            f"LEAK DETECTED: Note text {secret_note_snippet} leaked in request to {req['url']}"
        )
        assert "SecretSpecs.md" not in req["raw_body"]


@pytest.mark.asyncio
async def test_build_four_part_context_taint_invariants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Direct invariant test on build_four_part_context taint tagging."""
    monkeypatch.setattr("noxai.db.DB_PATH", tmp_path / "test_gate_c_direct.db")
    monkeypatch.setattr("noxai.db.DATA_DIR", tmp_path)
    await init_db()

    vault_dir = tmp_path / "Vault"
    vault_dir.mkdir()
    note_file = vault_dir / "StudyNotes.md"
    note_file.write_text("Quantum entanglement notes from study.", encoding="utf-8")

    async with get_db() as db:
        await index_documents(roots=[str(vault_dir)], db=db)

        # Build context for a query matching the note
        msgs = await build_four_part_context(
            conversation_id="conv_direct",
            current_user_message=Message(
                role="user", content=Content.chat("What did I note about quantum entanglement?")
            ),
            db=db,
            base_system_prompt=Message(
                role="system", content=Content("You are NoX.", taint=TaintFlag.NONE)
            ),
        )

    sys_msg = msgs[0]
    assert sys_msg.role == "system"
    # System message must carry PRIVATE taint because document chunks were retrieved
    assert bool(sys_msg.content.taint & TaintFlag.PRIVATE) is True
    assert sys_msg.content.get_data_class() == DataClass.LOCAL_ONLY
    assert "RELEVANT USER NOTES & DOCUMENTS" in sys_msg.content.text
