"""Tests verifying Cloud/NVIDIA defaults, persisted toggle, and zero egress for local_only data via Recording HTTP Transport (Acceptance Matrix A8, Session K §5.1)."""

from __future__ import annotations

import json
from typing import Any
import httpx
import pytest

from noxai.config import get_config
from noxai.db import execute, fetch_one, get_db, init_db
from noxai.models.base import Message
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

        self.recorded_requests.append({
            "method": request.method,
            "url": str(request.url),
            "headers": dict(request.headers),
            "raw_body": body_str,
            "json_body": body_json,
        })

        # Return a valid OpenAI-compatible chat completion response
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


@pytest.mark.asyncio
async def test_cloud_default_and_persisted_toggle(tmp_path, monkeypatch):
    """Test cloud.enabled is true by default and persists changes across reconnects."""
    monkeypatch.setattr("noxai.db.DB_PATH", tmp_path / "test_toggle.db")
    monkeypatch.setattr("noxai.db.DATA_DIR", tmp_path)
    await init_db()

    from noxai.commands.cloud import _get_cloud_enabled

    # 1. Default should be True
    default_enabled = await _get_cloud_enabled()
    assert default_enabled is True

    # 2. Toggle off
    async with get_db() as db:
        await execute(
            db,
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES ('cloud.enabled', 'false', datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value = 'false', updated_at = datetime('now')
            """,
        )
        await db.commit()

    assert await _get_cloud_enabled() is False

    # 3. Toggle back on
    async with get_db() as db:
        await execute(
            db,
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES ('cloud.enabled', 'true', datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value = 'true', updated_at = datetime('now')
            """,
        )
        await db.commit()

    assert await _get_cloud_enabled() is True


@pytest.mark.asyncio
async def test_recording_transport_proves_local_only_never_egresses(tmp_path, monkeypatch):
    """Prove that a local_only message NEVER reaches the cloud request body using RecordingTransport."""
    monkeypatch.setattr("noxai.db.DB_PATH", tmp_path / "test_egress.db")
    monkeypatch.setattr("noxai.db.DATA_DIR", tmp_path)
    await init_db()

    recording_transport = RecordingTransport()
    cfg = get_config()

    from noxai.models.keypool import KeyPool
    pool = KeyPool(config=cfg, keys=["nvapi-dummy-recording-key"], transport=recording_transport)
    cloud_provider = NvidiaProvider(key_pool=pool, transport=recording_transport)

    # Mock local provider
    class MockLocalProvider:
        name = "ollama"
        is_local = True

        async def chat(self, messages, model, **kwargs):
            from noxai.models.base import ChatChunk
            yield ChatChunk(text="Local response.")

        async def embed(self, texts, **kwargs):
            return [[0.1] * 8 for _ in texts]

    local_provider = MockLocalProvider()

    router = ModelRouter(
        config=cfg,
        providers={"nvidia": cloud_provider, "ollama": local_provider},
    )

    async with get_db() as db:
        # Turn 1: Normal public chat with allow_cloud=True -> SHOULD record a cloud HTTP request
        await orchestrate_chat_turn(
            message="What is the capital of Italy?",
            conversation_id="conv_public",
            db=db,
            router=router,
            config=cfg,
            allow_cloud=True,
        )

        assert len(recording_transport.recorded_requests) >= 1
        last_req = recording_transport.recorded_requests[-1]
        assert "integrate.api.nvidia.com" in last_req["url"]
        assert "Italy" in last_req["raw_body"]

        req_count_before = len(recording_transport.recorded_requests)

        # Turn 2: Sensitive / LOCAL_ONLY content -> MUST NOT reach cloud request body!
        secret_text = "CONFIDENTIAL_PAYROLL_DATA_ACCOUNT_998811"

        await orchestrate_chat_turn(
            message=f"Please analyze this: {secret_text}",
            conversation_id="conv_private",
            db=db,
            router=router,
            config=cfg,
            allow_cloud=True,  # Even if allow_cloud is True, taint / local_only must prevent cloud egress!
            is_private_mode=True,
        )

        # Inspect all requests recorded after Turn 1
        new_requests = recording_transport.recorded_requests[req_count_before:]
        for r in new_requests:
            assert secret_text not in r["raw_body"], (
                f"VIOLATION: local_only secret leaked to cloud in request {r['url']}"
            )
            if r["json_body"]:
                assert secret_text not in json.dumps(r["json_body"])

        # Furthermore, confirm secret was never sent in any request across entire transport history
        for r in recording_transport.recorded_requests:
            assert secret_text not in r["raw_body"]


@pytest.mark.asyncio
async def test_c5_all_local_only_sources_never_reach_cloud_recording_transport(
    tmp_path, monkeypatch
):
    """C-5 Proof: Prove that local_only content from ALL sources (mail, notes, chat, private-repo logs)
    NEVER reaches an outbound cloud request body using RecordingTransport.
    """
    from noxai.memory.docs import index_documents
    from noxai.models.keypool import KeyPool

    monkeypatch.setattr("noxai.db.DB_PATH", tmp_path / "test_c5_all.db")
    monkeypatch.setattr("noxai.db.DATA_DIR", tmp_path)
    await init_db()

    cfg = get_config()
    recording_transport = RecordingTransport()
    pool = KeyPool(config=cfg, keys=["nvapi-dummy-c5-key"], transport=recording_transport)
    cloud_provider = NvidiaProvider(key_pool=pool, transport=recording_transport)

    class MockLocalProvider:
        name = "ollama"
        is_local = True

        async def chat(self, messages, model, **kwargs):
            from noxai.models.base import ChatChunk
            yield ChatChunk(text="Local provider processed private content.")

        async def embed(self, texts, **kwargs):
            return [[0.1] * 8 for _ in texts]

    router = ModelRouter(
        config=cfg,
        providers={"nvidia": cloud_provider, "ollama": MockLocalProvider()},
    )

    # Setup sensitive payloads for the 4 distinct sources
    mail_secret = "MAIL_PAYLOAD_CONFIDENTIAL_INVOICE_INR_450000"
    note_secret = "NOTE_PAYLOAD_OBSIDIAN_VAULT_PERSONAL_DIARY_ENTRY"
    chat_secret = "CHAT_PAYLOAD_PRIVATE_MODE_CONVERSATION_KEY"
    repo_secret = "REPO_PAYLOAD_PRIVATE_GITHUB_WORKFLOW_LOG_TOKEN_999"

    # Setup notes in temp vault
    vault_dir = tmp_path / "Obsidian Vault"
    vault_dir.mkdir()
    (vault_dir / "Secret.md").write_text(f"Secret specifications: {note_secret}", encoding="utf-8")

    async with get_db() as db:
        await index_documents(roots=[str(vault_dir)], db=db)

        # Baseline: normal public chat reaches cloud
        await orchestrate_chat_turn(
            message="What is the speed of light in vacuum?",
            conversation_id="conv_c5_public",
            db=db,
            router=router,
            config=cfg,
            allow_cloud=True,
        )
        assert len(recording_transport.recorded_requests) >= 1
        assert "speed of light" in recording_transport.recorded_requests[-1]["raw_body"].lower()

        # Source 1: Mail item tagged local_only
        mail_msg = Message(
            role="user",
            content=Content.local_only(
                text=f"Summarize this received email: {mail_secret}",
                source="connector:gmail",
            ),
        )
        await orchestrate_chat_turn(
            message=f"Summarize this email: {mail_secret}",
            conversation_id="conv_c5_mail",
            db=db,
            router=router,
            config=cfg,
            allow_cloud=True,
            is_private_mode=True,
        )

        # Source 2: Note chunk from Obsidian Vault retrieved into context
        await orchestrate_chat_turn(
            message="What did I write in my notes about Secret specifications?",
            conversation_id="conv_c5_notes",
            db=db,
            router=router,
            config=cfg,
            allow_cloud=True,
        )

        # Source 3: Chat in private mode
        await orchestrate_chat_turn(
            message=f"My secret passphrase is {chat_secret}",
            conversation_id="conv_c5_chat",
            db=db,
            router=router,
            config=cfg,
            allow_cloud=True,
            is_private_mode=True,
        )

        # Source 4: Private-repo log tagged local_only
        private_repo_msg = Message(
            role="user",
            content=Content.local_only(
                text=f"CI Failure log from private repository: {repo_secret}",
                source="gh:private_repo_log",
            ),
        )
        # Directly evaluate router egress for private-repo log
        egress = router.egress_filter.evaluate(
            [private_repo_msg],
            provider_name="nvidia",
            model="nvidia/nemotron",
            is_local=False,
        )
        assert egress.allowed is False
        assert "LOCAL_ONLY" in egress.reason or "PRIVATE" in egress.reason

    # Final Invariant: Check that NONE of the 4 secrets ever reached any cloud request
    for r in recording_transport.recorded_requests:
        for secret in [mail_secret, note_secret, chat_secret, repo_secret]:
            assert secret not in r["raw_body"], (
                f"FATAL PRIVACY LEAK: {secret} leaked to cloud in request to {r['url']}"
            )
            if r["json_body"]:
                assert secret not in json.dumps(r["json_body"])
