"""Tests for built-in tools and registry execution mechanics."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from noxai.config import ModelRoleConfig, ModelsConfig, Settings
from noxai.taint import TaintFlag
from noxai.tools.builtin.file_tools import (
    ListFilesParams,
    ReadFileParams,
    WriteFileParams,
    list_files,
    read_file,
    write_file,
)
from noxai.tools.builtin.system import (
    GetTimeParams,
    RunShellParams,
    SystemInfoParams,
    get_time,
    run_shell,
    system_info,
)
from noxai.tools.builtin.web import FetchPageParams, fetch_page
from noxai.tools.registry import ToolContext, registry


@pytest.fixture
def test_ctx(tmp_path: Path) -> ToolContext:
    cfg = Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="ollama", model="qwen2.5:14b"),
                "embed": ModelRoleConfig(provider="ollama", model="nomic-embed-text"),
            }
        )
    )
    cfg.safety.allowed_file_roots = [str(tmp_path)]
    cfg.safety.denied_paths = [str(tmp_path / ".ssh")]
    return ToolContext(
        run_id="run_test_1",
        allow_cloud=False,
        config=cfg,
    )


class TestSystemTools:
    async def test_get_time(self, test_ctx: ToolContext) -> None:
        """get_time returns date, time, and timezone information."""
        res = await get_time(GetTimeParams(timezone="UTC"), test_ctx)
        assert res.ok
        assert "Date:" in res.output
        assert "Timezone: UTC" in res.output

    async def test_system_info(self, test_ctx: ToolContext) -> None:
        """system_info returns CPU, RAM, and disk stats."""
        res = await system_info(SystemInfoParams(), test_ctx)
        assert res.ok
        assert "CPU:" in res.output
        assert "RAM:" in res.output
        assert "Disk" in res.output

    async def test_run_shell_safe_command(self, test_ctx: ToolContext) -> None:
        """Safe allowlisted command runs without requiring approval."""
        res = await run_shell(RunShellParams(command="uptime"), test_ctx)
        assert res.ok
        assert "load average" in res.output or "up" in res.output

    async def test_run_shell_forbidden_command(self, test_ctx: ToolContext) -> None:
        """Forbidden shell command is blocked with ok=False."""
        res = await run_shell(RunShellParams(command="rm -rf /"), test_ctx)
        assert not res.ok
        assert "forbidden" in res.output.lower()

    async def test_run_shell_nonexistent_command(self, test_ctx: ToolContext) -> None:
        """Nonexistent binary approved by owner returns a clean command not found error."""

        async def auto_approve(*_args: object) -> bool:
            return True

        test_ctx.request_approval = auto_approve
        res = await run_shell(RunShellParams(command="nonexistent_binary_xyz"), test_ctx)
        assert not res.ok
        assert "not found" in res.output.lower()


class TestFileTools:
    async def test_write_read_and_diff(self, test_ctx: ToolContext, tmp_path: Path) -> None:
        """Writing and reading a file, followed by an update that produces a diff."""
        target_file = tmp_path / "notes.txt"

        # 1. Initial write
        w_res = await write_file(
            WriteFileParams(path=str(target_file), content="Line 1\nLine 2\n"),
            test_ctx,
        )
        assert w_res.ok
        assert target_file.exists()

        # 2. Read file
        r_res = await read_file(ReadFileParams(path=str(target_file)), test_ctx)
        assert r_res.ok
        assert "Line 1" in r_res.output
        assert len(r_res.sources) == 1

        # 3. Update file with new content to trigger diff
        w_res2 = await write_file(
            WriteFileParams(path=str(target_file), content="Line 1\nLine 2 modified\nLine 3\n"),
            test_ctx,
        )
        assert w_res2.ok
        assert "Diff:" in w_res2.output
        assert "+Line 3" in w_res2.output

    async def test_read_binary_file_refused(self, test_ctx: ToolContext, tmp_path: Path) -> None:
        """Binary files containing null bytes are refused."""
        bin_file = tmp_path / "data.bin"
        bin_file.write_bytes(b"\x00\x01\x02\x03\x04")

        res = await read_file(ReadFileParams(path=str(bin_file)), test_ctx)
        assert not res.ok
        assert "binary file" in res.output.lower()

    async def test_list_files(self, test_ctx: ToolContext, tmp_path: Path) -> None:
        """list_files lists folders and files in allowed roots."""
        (tmp_path / "file1.txt").write_text("1")
        (tmp_path / "file2.txt").write_text("2")
        (tmp_path / "subfolder").mkdir()

        res = await list_files(ListFilesParams(directory=str(tmp_path)), test_ctx)
        assert res.ok
        assert "file1.txt" in res.output
        assert "subfolder/" in res.output


class TestWebTools:
    async def test_fetch_page_ssrf_blocked(self, test_ctx: ToolContext) -> None:
        """fetch_page intercepts and blocks SSRF targets before sending HTTP requests."""
        res_loopback = await fetch_page(FetchPageParams(url="http://127.0.0.1:8080"), test_ctx)
        assert not res_loopback.ok
        assert "SSRF Security Violation" in res_loopback.output

        res_meta = await fetch_page(FetchPageParams(url="http://169.254.169.254/latest"), test_ctx)
        assert not res_meta.ok
        assert "SSRF Security Violation" in res_meta.output


class TestRegistryExecution:
    async def test_registry_untrusted_taint_escalation(
        self, test_ctx: ToolContext, tmp_path: Path
    ) -> None:
        """When untrusted content exists in the run context, write tools require approval."""
        approval_requested = []

        async def fake_approval_handler(run_id, tool, args, tier, reason):
            approval_requested.append((tool, tier, reason))
            return True  # Owner clicks approve

        test_ctx.request_approval = fake_approval_handler
        test_ctx.context_taint |= TaintFlag.UNTRUSTED  # Context tainted by previous web/file read

        target = str(tmp_path / "approved.txt")
        result = await registry.execute(
            "write_file",
            {"path": target, "content": "Approved content"},
            test_ctx,
        )

        assert result.ok
        assert len(approval_requested) == 1
        assert approval_requested[0][0] == "write_file"
        assert "untrusted" in approval_requested[0][2].lower()

    async def test_registry_audit_log_persists(self, test_ctx: ToolContext, tmp_path: Path) -> None:
        """Every tool execution writes an entry to the audit_log database table."""

        db_path = tmp_path / "audit_test.db"
        async with aiosqlite.connect(db_path) as db:
            # Create audit_log table
            await db.execute("""
                CREATE TABLE audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT DEFAULT (datetime('now')),
                    run_id TEXT,
                    tool TEXT,
                    args_json TEXT,
                    tier TEXT,
                    decision TEXT,
                    approver TEXT,
                    result_summary TEXT,
                    duration_ms INTEGER,
                    ok INTEGER
                )
            """)
            await db.commit()

            test_ctx.db = db
            res = await registry.execute("get_time", {}, test_ctx)
            assert res.ok

            cursor = await db.execute(
                "SELECT tool, tier, decision, ok FROM audit_log WHERE run_id = ?",
                (test_ctx.run_id,),
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "get_time"
            assert row[1] == "read"
            assert row[2] == "auto"
            assert row[3] == 1
