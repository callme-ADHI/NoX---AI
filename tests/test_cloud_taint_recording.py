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
