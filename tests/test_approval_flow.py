"""Tests for tool approval flow: args_hash, single-use consumed status, polling, CLI."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import patch

import aiosqlite
import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from noxai.cli import app
from noxai.config import Settings
from noxai.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolPendingApproval,
    ToolRegistry,
    ToolResult,
    approve_approval,
    deny_approval,
)


class DummyWriteParams(BaseModel):
    path: str
    content: str


async def dummy_write_func(params: DummyWriteParams, ctx: ToolContext) -> ToolResult:
    return ToolResult(ok=True, output=f"Wrote to {params.path}", summary="write ok")


@pytest.fixture
def write_tool_def() -> ToolDefinition:
    return ToolDefinition(
        name="test_write_file",
        description="Write content to a file",
        params_model=DummyWriteParams,
        tier="write",
        func=dummy_write_func,
    )


@pytest.fixture
def mock_config() -> Settings:
    return Settings(
        models={"roles": {"brain": {"provider": "ollama", "model": "qwen2.5:14b"},
                          "embed": {"provider": "ollama", "model": "nomic-embed-text"}}},
    )


@pytest.fixture
async def migrated_db(tmp_path: Path) -> aiosqlite.Connection:
    """Create an in-memory or temp file DB with 001 and 002 migrations applied."""
    db_file = tmp_path / "nox.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA foreign_keys = ON;")
    with open("src/noxai/migrations/001_initial.sql") as f:
        conn.executescript(f.read())
    with open("src/noxai/migrations/002_spec_alignment.sql") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()

    async_conn = await aiosqlite.connect(str(db_file))
    async_conn.row_factory = aiosqlite.Row
    yield async_conn
    await async_conn.close()


@pytest.mark.asyncio
async def test_args_hash_computation(
    write_tool_def: ToolDefinition,
    mock_config: Settings,
    migrated_db: aiosqlite.Connection,
) -> None:
    """Approval row records correct SHA256 of canonical JSON arguments."""
    reg = ToolRegistry()
    reg.register(write_tool_def)

    args = {"path": "/tmp/test.txt", "content": "hello world"}
    expected_hash = hashlib.sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()

    ctx = ToolContext(
        run_id="run_hash_test",
        config=mock_config,
        db=migrated_db,
        poll_approval=False,
    )

    with pytest.raises(ToolPendingApproval) as exc_info:
        await reg.execute("test_write_file", args, ctx)

    assert exc_info.value.args_hash == expected_hash

    # Verify DB row
    cursor = await migrated_db.execute(
        "SELECT * FROM approvals WHERE run_id = 'run_hash_test'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["args_hash"] == expected_hash
    assert row["status"] == "pending"
    assert row["tool_name"] == "test_write_file"


@pytest.mark.asyncio
async def test_approval_consumed_single_use(
    write_tool_def: ToolDefinition,
    mock_config: Settings,
    migrated_db: aiosqlite.Connection,
) -> None:
    """When approved via callback, approval status transitions to 'consumed' (single-use)."""
    reg = ToolRegistry()
    reg.register(write_tool_def)

    async def approve_cb(
        run_id: str,
        tool: str,
        args: dict[str, Any],
        tier: str,
        reason: str | None,
    ) -> bool:
        return True

    ctx = ToolContext(
        run_id="run_consume_test",
        config=mock_config,
        db=migrated_db,
        request_approval=approve_cb,
    )

    result = await reg.execute("test_write_file", {"path": "/tmp/a", "content": "1"}, ctx)
    assert result.ok is True

    # Verify status in DB is 'consumed'
    cursor = await migrated_db.execute(
        "SELECT status FROM approvals WHERE run_id = 'run_consume_test'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["status"] == "consumed"


@pytest.mark.asyncio
async def test_polling_approval_resolves_when_approved(
    write_tool_def: ToolDefinition,
    mock_config: Settings,
    migrated_db: aiosqlite.Connection,
) -> None:
    """Tool execution polls DB and proceeds once another task approves it."""
    reg = ToolRegistry()
    reg.register(write_tool_def)

    ctx = ToolContext(
        run_id="run_poll_test",
        config=mock_config,
        db=migrated_db,
        poll_approval=True,
        approval_timeout=2.0,
    )

    async def background_approver() -> None:
        await asyncio.sleep(0.2)
        cursor = await migrated_db.execute(
            "SELECT id FROM approvals WHERE run_id = 'run_poll_test'"
        )
        row = await cursor.fetchone()
        assert row is not None
        await approve_approval(row["id"], db=migrated_db)

    task = asyncio.create_task(background_approver())
    result = await reg.execute("test_write_file", {"path": "/tmp/poll", "content": "data"}, ctx)
    await task

    assert result.ok is True
    assert result.summary == "write ok"

    # Status must now be consumed
    cursor = await migrated_db.execute(
        "SELECT status FROM approvals WHERE run_id = 'run_poll_test'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["status"] == "consumed"


@pytest.mark.asyncio
async def test_polling_approval_denied_in_db(
    write_tool_def: ToolDefinition,
    mock_config: Settings,
    migrated_db: aiosqlite.Connection,
) -> None:
    """Tool execution returns denied ToolResult when another task denies it."""
    reg = ToolRegistry()
    reg.register(write_tool_def)

    ctx = ToolContext(
        run_id="run_deny_test",
        config=mock_config,
        db=migrated_db,
        poll_approval=True,
        approval_timeout=2.0,
    )

    async def background_denier() -> None:
        await asyncio.sleep(0.2)
        cursor = await migrated_db.execute(
            "SELECT id FROM approvals WHERE run_id = 'run_deny_test'"
        )
        row = await cursor.fetchone()
        assert row is not None
        await deny_approval(row["id"], db=migrated_db)

    task = asyncio.create_task(background_denier())
    result = await reg.execute("test_write_file", {"path": "/tmp/deny", "content": "data"}, ctx)
    await task

    assert result.ok is False
    assert "denied" in result.output.lower()


@pytest.mark.asyncio
async def test_approval_timeout_marks_expired(
    write_tool_def: ToolDefinition,
    mock_config: Settings,
    migrated_db: aiosqlite.Connection,
) -> None:
    """Timeout expires approval row and returns denied."""
    reg = ToolRegistry()
    reg.register(write_tool_def)

    ctx = ToolContext(
        run_id="run_timeout_test",
        config=mock_config,
        db=migrated_db,
        poll_approval=True,
        approval_timeout=0.3,
    )

    result = await reg.execute("test_write_file", {"path": "/tmp/timeout", "content": "data"}, ctx)
    assert result.ok is False

    cursor = await migrated_db.execute(
        "SELECT status FROM approvals WHERE run_id = 'run_timeout_test'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["status"] == "expired"


def test_cli_tools_approve_and_deny(tmp_path: Path) -> None:
    """nx tools approve and nx tools deny CLI commands."""
    db_file = tmp_path / "nox.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA foreign_keys = ON;")
    with open("src/noxai/migrations/001_initial.sql") as f:
        conn.executescript(f.read())
    with open("src/noxai/migrations/002_spec_alignment.sql") as f:
        conn.executescript(f.read())

    # Insert two pending approvals
    conn.execute(
        "INSERT INTO approvals (id, tool_name, args_json, args_hash, reason, status) "
        "VALUES (1, 'write_file', '{}', 'hash1', 'testing approve', 'pending')"
    )
    conn.execute(
        "INSERT INTO approvals (id, tool_name, args_json, args_hash, reason, status) "
        "VALUES (2, 'run_shell', '{}', 'hash2', 'testing deny', 'pending')"
    )
    conn.commit()
    conn.close()

    runner = CliRunner()
    with patch("noxai.cli.DB_PATH", db_file):
        # List command
        res_list = runner.invoke(app, ["tools", "list"])
        assert res_list.exit_code == 0
        assert "write_file" in res_list.output
        assert "run_shell" in res_list.output

        # Approve command
        res_app = runner.invoke(app, ["tools", "approve", "1"])
        assert res_app.exit_code == 0
        assert "Approved tool execution 1" in res_app.output

        # Deny command
        res_den = runner.invoke(app, ["tools", "deny", "2"])
        assert res_den.exit_code == 0
        assert "Denied tool execution 2" in res_den.output

    # Verify statuses in sqlite
    conn = sqlite3.connect(str(db_file))
    r1 = conn.execute("SELECT status FROM approvals WHERE id = 1").fetchone()[0]
    r2 = conn.execute("SELECT status FROM approvals WHERE id = 2").fetchone()[0]
    conn.close()
    assert r1 == "approved"
    assert r2 == "denied"
