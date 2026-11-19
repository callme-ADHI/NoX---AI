# Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
# commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
# Modified for NoX: Tests for Workstream E (Skills catalog, parser validation, safety gates, and MCP stdio client).

"""Unit and acceptance tests for Workstream E: Skills and MCP."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

import aiosqlite
import pytest

from noxai.mcp.client import (
    MCPServerConfig,
    MCPRegistry,
    StdioMCPClient,
    register_mcp_tools,
)
from noxai.skills.catalog import SkillsCatalog, get_skills_catalog
from noxai.skills.parser import (
    MAX_SKILL_MD_SIZE_BYTES,
    SkillValidationError,
    parse_frontmatter_block,
    validate_skill_folder,
)
from noxai.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# E1: Shipped Skills Discovery & Structure
# ---------------------------------------------------------------------------


def test_shipped_skills_discovery() -> None:
    catalog = get_skills_catalog()
    catalog.discover_skills()
    skills = catalog.list_skills()

    names = {s.name for s in skills}
    assert "briefing-format" in names, "briefing-format must be shipped"
    assert "notes-search" in names, "notes-search must be shipped"

    bf = catalog.get_skill("briefing-format")
    assert bf is not None
    assert len(bf.description) > 10
    assert len(bf.description) <= 1024
    assert "Briefing" in bf.body or "briefing" in bf.body

    ns = catalog.get_skill("notes-search")
    assert ns is not None
    assert len(ns.description) > 10
    assert len(ns.description) <= 1024
    assert "Vault" in ns.body or "notes" in ns.body or "recall" in ns.body


# ---------------------------------------------------------------------------
# E2: Validation Tests (Malformed frontmatter, oversized file, escaping symlink)
# ---------------------------------------------------------------------------


def test_validation_malformed_frontmatter(tmp_path: Path) -> None:
    # 1. Missing opening fence
    folder1 = tmp_path / "bad1"
    folder1.mkdir()
    (folder1 / "SKILL.md").write_text("name: bad1\ndescription: test\n---\nBody", encoding="utf-8")
    with pytest.raises(SkillValidationError, match="malformed_frontmatter"):
        validate_skill_folder(folder1)

    # 2. Unclosed frontmatter fence
    folder2 = tmp_path / "bad2"
    folder2.mkdir()
    (folder2 / "SKILL.md").write_text("---\nname: bad2\ndescription: unclosed\nBody without close", encoding="utf-8")
    with pytest.raises(SkillValidationError, match="malformed_frontmatter"):
        validate_skill_folder(folder2)

    # 3. Invalid YAML syntax
    folder3 = tmp_path / "bad3"
    folder3.mkdir()
    (folder3 / "SKILL.md").write_text("---\nname: bad3\n  broken: [yaml: foo\n---\nBody", encoding="utf-8")
    with pytest.raises(SkillValidationError, match="malformed_frontmatter"):
        validate_skill_folder(folder3)

    # 4. Folder name mismatch
    folder4 = tmp_path / "mismatch"
    folder4.mkdir()
    (folder4 / "SKILL.md").write_text("---\nname: other-name\ndescription: test\n---\nBody", encoding="utf-8")
    with pytest.raises(SkillValidationError, match="name_mismatch"):
        validate_skill_folder(folder4)


def test_validation_oversized_file(tmp_path: Path) -> None:
    folder = tmp_path / "oversized-skill"
    folder.mkdir()
    # Create SKILL.md exceeding 100 KB
    huge_content = (
        "---\nname: oversized-skill\ndescription: Test large skill\n---\n"
        + ("A" * (MAX_SKILL_MD_SIZE_BYTES + 500))
    )
    (folder / "SKILL.md").write_text(huge_content, encoding="utf-8")
    with pytest.raises(SkillValidationError, match="oversized"):
        validate_skill_folder(folder)


def test_validation_escaping_symlink(tmp_path: Path) -> None:
    folder = tmp_path / "escaping-skill"
    folder.mkdir()
    (folder / "SKILL.md").write_text("---\nname: escaping-skill\ndescription: test\n---\nBody", encoding="utf-8")

    # Create target outside folder and symlink inside
    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("classified data", encoding="utf-8")

    symlink_path = folder / "escaped_link"
    symlink_path.symlink_to(outside_file)

    with pytest.raises(SkillValidationError, match="escaping_symlink"):
        validate_skill_folder(folder)


# ---------------------------------------------------------------------------
# E3: Staged Installation & Disabled Initial Status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_staging_and_lifecycle(tmp_path: Path) -> None:
    user_skills_dir = tmp_path / "user_skills"
    catalog = SkillsCatalog(user_dir=user_skills_dir, repo_dir=tmp_path / "no_repo")

    # Prepare external candidate skill folder
    src_dir = tmp_path / "external_source" / "custom-helper"
    src_dir.mkdir(parents=True)
    (src_dir / "SKILL.md").write_text(
        "---\nname: custom-helper\ndescription: A helpful external skill\n---\n# Helper instructions",
        encoding="utf-8",
    )

    # Install skill
    installed = catalog.install_skill_from_path(src_dir)
    assert installed.name == "custom-helper"
    assert installed.enabled is False, "Newly installed skill must start disabled"
    assert installed.allow_scripts is False, "Newly installed skill must have scripts disallowed"

    # Sync to DB
    db_file = tmp_path / "skills_test.db"
    async with aiosqlite.connect(str(db_file)) as db:
        await catalog.sync_state(db)
        skill_in_cat = catalog.get_skill("custom-helper")
        assert skill_in_cat is not None
        assert skill_in_cat.enabled is False

        # Enable the skill
        ok = await catalog.enable_skill("custom-helper", db=db)
        assert ok is True
        assert catalog.get_skill("custom-helper").enabled is True

        # Disable again
        ok = await catalog.disable_skill("custom-helper", db=db)
        assert ok is True
        assert catalog.get_skill("custom-helper").enabled is False


# ---------------------------------------------------------------------------
# E4: Script Safety Gate (Refusal with Default Config)
# ---------------------------------------------------------------------------


def test_script_safety_refusal_by_default(tmp_path: Path) -> None:
    folder = tmp_path / "scripted-skill"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: scripted-skill\ndescription: Skill containing scripts\n---\nInstructions",
        encoding="utf-8",
    )
    scripts_dir = folder / "scripts"
    scripts_dir.mkdir()
    script_file = scripts_dir / "run_test.sh"
    script_file.write_text("#!/bin/sh\necho 'hello from script'\n", encoding="utf-8")
    script_file.chmod(script_file.stat().st_mode | stat.S_IEXEC)

    catalog = SkillsCatalog(user_dir=tmp_path / "user", repo_dir=tmp_path)
    catalog.discover_skills()
    skill = catalog.get_skill("scripted-skill")
    assert skill is not None
    assert skill.allow_scripts is False

    # Attempting to execute with default settings must raise PermissionError
    with pytest.raises(PermissionError, match="Refusing script execution"):
        catalog.execute_script("scripted-skill", "run_test.sh")

    # Explicitly allow scripts
    skill.allow_scripts = True
    res = catalog.execute_script("scripted-skill", "run_test.sh")
    assert res.returncode == 0
    assert "hello from script" in res.stdout


# ---------------------------------------------------------------------------
# E5: Minimal Stdio JSON-RPC MCP Server & Client Acceptance Test
# ---------------------------------------------------------------------------

FAKE_MCP_SERVER_CODE = """
import sys
import json

while True:
    line = sys.stdin.readline()
    if not line:
        break
    try:
        msg = json.loads(line.strip())
    except Exception:
        continue

    method = msg.get("method")
    msg_id = msg.get("id")

    if method == "initialize":
        resp = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test-mock-server", "version": "1.0.0"}
            }
        }
        sys.stdout.write(json.dumps(resp) + "\\n")
        sys.stdout.flush()
    elif method == "notifications/initialized":
        # Notification - no response
        pass
    elif method == "tools/list":
        resp = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "tools": [
                    {
                        "name": "echo_tool",
                        "description": "Echo input text",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                            "required": ["text"]
                        }
                    },
                    {
                        "name": "restricted_tool",
                        "description": "A tool not on allowlist",
                        "inputSchema": {"type": "object"}
                    }
                ]
            }
        }
        sys.stdout.write(json.dumps(resp) + "\\n")
        sys.stdout.flush()
    elif method == "tools/call":
        params = msg.get("params", {})
        t_name = params.get("name")
        args = params.get("arguments", {})
        if t_name == "echo_tool":
            result_val = f"echo:{args.get('text', '')}"
            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": result_val}]}
            }
        else:
            resp = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Unknown tool {t_name}"}
            }
        sys.stdout.write(json.dumps(resp) + "\\n")
        sys.stdout.flush()
"""


@pytest.mark.asyncio
async def test_stdio_mcp_client_handshake_and_call(tmp_path: Path) -> None:
    server_py = tmp_path / "mock_mcp_server.py"
    server_py.write_text(FAKE_MCP_SERVER_CODE, encoding="utf-8")

    # 1. Config starts disabled
    cfg = MCPServerConfig(
        name="test_mock",
        command=sys.executable,
        args=[str(server_py)],
        enabled=False,
        allowlist=["echo_tool"],
    )

    client = StdioMCPClient(cfg)

    # Start handshake
    await client.start()

    # Disabled server refuses tool call
    with pytest.raises(PermissionError, match="disabled"):
        await client.call_tool("echo_tool", {"text": "hello"})

    # Enable server
    cfg.enabled = True

    # List tools - filtered by allowlist
    tools = await client.list_tools()
    assert len(tools) == 1
    assert tools[0].name == "echo_tool"
    assert tools[0].server_name == "test_mock"

    # Call allowed tool
    res = await client.call_tool("echo_tool", {"text": "NoX MCP Test"})
    assert "NoX MCP Test" in str(res)

    # Call disallowed tool (restricted_tool not in allowlist)
    with pytest.raises(PermissionError, match="not in allowlist"):
        await client.call_tool("restricted_tool", {})

    # Tool registration into ToolRegistry
    registry = ToolRegistry()
    mcp_reg = MCPRegistry(config_file=tmp_path / "mcp.json")
    mcp_reg.register_server(cfg)
    # Cache client instance
    mcp_reg._clients[cfg.name] = client

    count = await register_mcp_tools(registry, mcp_registry=mcp_reg)
    assert count == 1
    assert registry.get("echo_tool") is not None

    # Graceful shutdown
    await client.close()
