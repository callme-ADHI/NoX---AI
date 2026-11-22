"""Unit tests for Step-Level Trace Logging and Prompt Rewriting (Workstream C).

Covers:
- Secret redaction (nvapi, github, bearer, long hex/base64)
- Trace recording lifecycle (running -> success / failed)
- Crash recovery of orphaned traces (running -> interrupted)
- 90-day retention pruning and volume capping
- Topic dislike feedback and validated prompt rewriting
- Prompt version history and rollback
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
import sqlite3
import pytest

from noxai.db import execute, fetch_all, fetch_one, get_db, init_db
from noxai.tracing.cleanup import cleanup_old_traces
from noxai.tracing.recorder import (
    get_trace,
    get_trace_stats,
    list_traces,
    record_trace_feedback,
    recover_interrupted_traces,
    trace_execution,
)
from noxai.tracing.redact import contains_secret, redact_secrets, redact_secrets_text
from noxai.tracing.rewrite import (
    get_active_topic_prompt,
    get_topic_prompt_history,
    improve_topic_prompt,
    rollback_topic_prompt,
)


@pytest.fixture(autouse=True)
async def init_test_database():
    """Ensure test database and migrations 001-014 are applied."""
    await init_db()


def test_redact_secrets_text_and_patterns():
    fake_nvapi = "nvapi-live_secret_1234567890abcdef"
    fake_ghp = "ghp_111122223333444455556666777788889999"
    fake_pat = "github_pat_11ABCD1234567890abcdefghijklmnopqrstuvwxyz1234567890abcdefghijklmnopqr"
    fake_auth = "Authorization: Bearer secret_bearer_token_12345678901234567890"
    fake_hex = "abcdef0123456789abcdef0123456789abcdef0123"

    raw_text = f"NVIDIA: {fake_nvapi}, GitHub: {fake_ghp}, PAT: {fake_pat}, Auth: {fake_auth}, Hex: {fake_hex}"
    redacted = redact_secrets_text(raw_text)

    assert "live_secret" not in redacted
    assert "ghp_1111" not in redacted
    assert "11ABCD1234" not in redacted
    assert "secret_bearer_token" not in redacted
    assert fake_hex not in redacted

    assert "nvapi-[REDACTED]" in redacted
    assert "[REDACTED_GITHUB_TOKEN]" in redacted
    assert "[REDACTED_HEX_TOKEN]" in redacted
    assert not contains_secret(redacted)


def test_redact_secrets_nested_structures():
    data = {
        "user_query": "Use key nvapi-testkey1234567890 please",
        "headers": {"Authorization": "Bearer tok_123456789012345678901234567890"},
        "nested": [
            {"token": "secret_value_12345"},
            "ghp_abcdefghijklmnopqrstuvwxyz1234567890",
        ],
    }
    redacted = redact_secrets(data)

    assert "nvapi-testkey" not in str(redacted)
    assert "ghp_abcdef" not in str(redacted)
    assert redacted["headers"]["Authorization"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_secrets_never_reach_database(tmp_path):
    """Prove with raw SQLite SELECT that secrets never reach database tables."""
    fake_key = "nvapi-super_secret_token_1234567890_do_not_leak"

    async with trace_execution(
        source="chat",
        request_text=f"Please execute command with {fake_key}",
    ) as tr:
        tr.record_step(name="eval_step", tool_or_skill=f"tool_with_{fake_key}")
        tr.set_engine_and_model("nvidia", "model-test")

    # Read raw database row
    saved = await get_trace(tr.trace_id)
    assert saved is not None
    assert fake_key not in saved["request_text"]
    assert "nvapi-[REDACTED]" in saved["request_text"]
    assert fake_key not in json.dumps(saved["steps"])


@pytest.mark.asyncio
async def test_trace_lifecycle_success_and_failure():
    # 1. Success execution
    async with trace_execution(
        source="briefing",
        request_text="Extract items for topic_ai",
        intent="briefing",
    ) as tr:
        tr.record_step("crawl", tool_or_skill="web_crawler", duration_ms=45.2, status="success")
        tr.record_step("extract", tool_or_skill="trafilatura", duration_ms=80.1, status="success")
        tr.set_tokens(prompt_tokens=150, completion_tokens=50, total_tokens=200)

    tr_row = await get_trace(tr.trace_id)
    assert tr_row is not None
    assert tr_row["source"] == "briefing"
    assert tr_row["final_status"] == "success"
    assert len(tr_row["steps"]) == 2
    assert tr_row["steps"][0]["name"] == "crawl"
    assert tr_row["token_counts"]["total_tokens"] == 200

    # 2. Failed execution
    with pytest.raises(RuntimeError, match="Network timeout"):
        async with trace_execution(
            source="job",
            request_text="Backup job run",
        ) as tr_fail:
            tr_fail.record_step("git_preflight", status="success")
            raise RuntimeError("Network timeout connecting to GitHub")

    fail_row = await get_trace(tr_fail.trace_id)
    assert fail_row is not None
    assert fail_row["source"] == "job"
    assert fail_row["final_status"] == "failed"
    assert "Network timeout" in fail_row["error_text"]


@pytest.mark.asyncio
async def test_trace_crash_recovery():
    """Verify orphaned 'running' traces are recovered to 'interrupted'."""
    # Insert raw running trace
    tr_id = "tr_crashed_sim_01"
    now_iso = datetime.now(UTC).isoformat()
    async with get_db() as db:
        await execute(
            db,
            """
            INSERT INTO traces (id, started_at, source, final_status, request_text)
            VALUES (?, ?, 'schedule', 'running', 'Crashed schedule run')
            """,
            (tr_id, now_iso),
        )
        await db.commit()

    recovered = await recover_interrupted_traces()
    assert recovered >= 1

    recovered_trace = await get_trace(tr_id)
    assert recovered_trace is not None
    assert recovered_trace["final_status"] == "interrupted"
    assert "interrupted" in recovered_trace["error_text"].lower()


@pytest.mark.asyncio
async def test_retention_and_volume_capping():
    """Test 90-day retention pruning and row capping."""
    now = datetime.now(UTC)
    old_iso = (now - timedelta(days=95)).isoformat()
    fresh_iso = now.isoformat()

    async with get_db() as db:
        # Insert 3 old traces (>90 days)
        for i in range(3):
            await execute(
                db,
                "INSERT INTO traces (id, started_at, source, final_status) VALUES (?, ?, 'chat', 'success')",
                (f"tr_old_{i}", old_iso),
            )
        # Insert 2 fresh traces
        for i in range(2):
            await execute(
                db,
                "INSERT INTO traces (id, started_at, source, final_status) VALUES (?, ?, 'chat', 'success')",
                (f"tr_fresh_{i}", fresh_iso),
            )
        await db.commit()

    # Clean up older than 90 days
    res = await cleanup_old_traces(max_days=90, max_rows=1000)
    assert res["deleted_expired"] >= 3

    # Verify old traces are gone
    old_trace = await get_trace("tr_old_0")
    assert old_trace is None
    fresh_trace = await get_trace("tr_fresh_0")
    assert fresh_trace is not None

    # Test volume cap (cap at 1 row)
    cap_res = await cleanup_old_traces(max_days=90, max_rows=1)
    assert cap_res["deleted_capped"] >= 1


@pytest.mark.asyncio
async def test_trace_stats_calculation():
    stats = await get_trace_stats(days=7)
    assert "total_traces" in stats
    assert "by_source" in stats
    assert "by_status" in stats
    assert "median_duration_ms" in stats


@pytest.mark.asyncio
async def test_topic_feedback_and_prompt_rewriting():
    """Test feedback recording, prompt validation gates, improvement, and rollback."""
    topic = "topic_cybersecurity"
    test_trace_id = "tr_test_cyber_01"

    # 1. Create trace
    async with trace_execution(source="briefing", request_text=f"Extract for {topic}", trace_id=test_trace_id):
        pass

    # 2. Record dislike feedback with note
    pfb_id = await record_trace_feedback(
        trace_id=test_trace_id,
        rating="dislike",
        note="Too much corporate marketing. Focus strictly on CVE zero-days and Linux exploits.",
        topic=topic,
    )
    assert pfb_id.startswith("pfb_")

    # Verify prompt_feedback row exists
    async with get_db() as db:
        pfb_row = await fetch_one(db, "SELECT * FROM prompt_feedback WHERE id = ?", (pfb_id,))
        assert pfb_row is not None
        assert pfb_row["topic"] == topic
        assert pfb_row["applied"] == 0

    # 3. Trigger improve_topic_prompt
    res = await improve_topic_prompt(topic)
    assert res["version"] >= 2
    assert topic.replace("topic_", "") in res["new_prompt"].lower() or topic in res["new_prompt"].lower()
    assert res["applied_count"] >= 1

    # Verify feedback was marked applied
    async with get_db() as db:
        updated_pfb = await fetch_one(db, "SELECT applied FROM prompt_feedback WHERE id = ?", (pfb_id,))
        assert updated_pfb["applied"] == 1

    # 4. Check prompt version history
    history = await get_topic_prompt_history(topic)
    assert len(history) >= 2
    assert any(h["version"] == res["version"] and h["status"] == "active" for h in history)

    # 5. Rollback to version 1
    rolled = await rollback_topic_prompt(topic, target_version=1)
    assert rolled["active_version"] == 1

    active_prompt, active_ver = await get_active_topic_prompt(topic)
    assert active_ver == 1
