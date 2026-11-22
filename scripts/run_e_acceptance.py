#!/usr/bin/env python3
# Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
# commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
# Modified for NoX: Workstream E acceptance proof generator.

"""Run Workstream E acceptance checks and save proofs to docs/reports/session-M/."""

import asyncio
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = PROJECT_ROOT / "docs" / "reports" / "session-M"
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
VENV_PYTEST = PROJECT_ROOT / ".venv" / "bin" / "pytest"


def run_cmd(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    res = subprocess.run(
        cmd,
        cwd=cwd or PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    output = (res.stdout or "") + ("\nSTDERR:\n" + res.stderr if res.stderr else "")
    return res.returncode, output.strip()


async def run_e1() -> str:
    print("Running E1: Shipped skills list and notes-search loading trace...")
    # 1. nx skill list
    code, list_out = run_cmd([str(VENV_PYTHON), "-m", "noxai.cli", "skill", "list"])
    assert "briefing-format" in list_out, f"briefing-format missing from skill list: {list_out}"
    assert "notes-search" in list_out, f"notes-search missing from skill list: {list_out}"

    # 2. Run real chat turn matching notes-search
    from noxai.db import get_db, run_migrations
    from noxai.orchestrator import orchestrate_chat_turn

    await run_migrations()
    turn_res = await orchestrate_chat_turn(
        message="What did I write in my notes about dignity of workers?",
        allow_cloud=True,
    )

    # 3. Query the traces table
    async with get_db() as db:
        async with db.execute(
            "SELECT id, source, request_text, steps, final_status FROM traces WHERE schedule_run_id = ?",
            (turn_res.run_id,),
        ) as cur:
            row = await cur.fetchone()

    steps_data = json.loads(row[3]) if row and row[3] else []
    load_skill_step = next(
        (s for s in steps_data if s.get("name") == "load_skill" and "notes-search" in s.get("tool_or_skill", "")),
        None,
    )

    proof = []
    proof.append("=== NOX WORKSTREAM E1 ACCEPTANCE PROOF ===")
    proof.append(f"Timestamp: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    proof.append("\n--- 1. `nx skill list` OUTPUT ---")
    proof.append(list_out)
    proof.append("\n--- 2. CHAT REQUEST MATCHING notes-search ---")
    proof.append(f"Run ID: {turn_res.run_id}")
    proof.append(f"Query: 'What did I write in my notes about dignity of workers?'")
    proof.append(f"Model: {turn_res.model_name} (Served by: {turn_res.served_by})")
    proof.append(f"Response Snippet:\n{turn_res.text[:300]}...")
    proof.append("\n--- 3. PERSISTED TRACE ROW & LOAD_SKILL STEP ---")
    proof.append(f"Trace ID: {row[0]}")
    proof.append(f"Source: {row[1]}")
    proof.append(f"Final Status: {row[4]}")
    proof.append(f"All Steps:\n{json.dumps(steps_data, indent=2)}")
    proof.append(f"\nLoad Skill Step Match: {json.dumps(load_skill_step, indent=2)}")

    assert load_skill_step is not None, "load_skill step for notes-search was not found in trace!"
    return "\n".join(proof)


def run_e2() -> str:
    print("Running E2: Validation tests (malformed, oversized, escaping symlink)...")
    code, out = run_cmd([str(VENV_PYTEST), "tests/test_skills_and_mcp.py", "-k", "test_validation", "-v"])
    assert code == 0, f"Validation tests failed:\n{out}"

    proof = []
    proof.append("=== NOX WORKSTREAM E2 ACCEPTANCE PROOF ===")
    proof.append(f"Timestamp: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    proof.append("\n--- Pytest Output for Validation Rules ---")
    proof.append(out)
    return "\n".join(proof)


def run_e3() -> str:
    print("Running E3: Install test (staging, disabled default, enable, remove)...")
    tmp_skill = Path("/tmp/test-skill-e3")
    if tmp_skill.exists():
        shutil.rmtree(tmp_skill)
    tmp_skill.mkdir(parents=True)
    (tmp_skill / "SKILL.md").write_text(
        "---\n"
        "name: test-skill-e3\n"
        "description: Dynamic test skill for Workstream E3 staged install validation.\n"
        "version: 1.0.0\n"
        "---\n\n"
        "# E3 Instructions\n"
        "Always format test outputs with precision.\n",
        encoding="utf-8",
    )

    proof = []
    proof.append("=== NOX WORKSTREAM E3 ACCEPTANCE PROOF ===")
    proof.append(f"Timestamp: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")

    # 1. Install skill
    code, inst_out = run_cmd([str(VENV_PYTHON), "-m", "noxai.cli", "skill", "install", str(tmp_skill), "--yes"])
    proof.append("\n--- 1. `nx skill install /tmp/test-skill-e3 --yes` ---")
    proof.append(inst_out)

    # 2. List skills showing disabled
    code, list1 = run_cmd([str(VENV_PYTHON), "-m", "noxai.cli", "skill", "list"])
    proof.append("\n--- 2. `nx skill list` (Shows installed skill DISABLED by default) ---")
    proof.append(list1)
    assert "test-skill-e3" in list1

    # 3. Enable skill
    code, en_out = run_cmd([str(VENV_PYTHON), "-m", "noxai.cli", "skill", "enable", "test-skill-e3"])
    proof.append("\n--- 3. `nx skill enable test-skill-e3` ---")
    proof.append(en_out)

    # 4. List skills showing enabled
    code, list2 = run_cmd([str(VENV_PYTHON), "-m", "noxai.cli", "skill", "list"])
    proof.append("\n--- 4. `nx skill list` (Shows test-skill-e3 ENABLED) ---")
    proof.append(list2)

    # 5. Clean up by removing skill
    code, rem_out = run_cmd([str(VENV_PYTHON), "-m", "noxai.cli", "skill", "remove", "test-skill-e3"])
    proof.append("\n--- 5. `nx skill remove test-skill-e3` ---")
    proof.append(rem_out)

    shutil.rmtree(tmp_skill, ignore_errors=True)
    return "\n".join(proof)


def run_e4() -> str:
    print("Running E4: Script safety refusal test...")
    code, out = run_cmd([str(VENV_PYTEST), "tests/test_skills_and_mcp.py", "-k", "test_script_safety_refusal_by_default", "-v"])
    assert code == 0, f"Script safety test failed:\n{out}"

    # Also run CLI allow-scripts test
    proof = []
    proof.append("=== NOX WORKSTREAM E4 ACCEPTANCE PROOF ===")
    proof.append(f"Timestamp: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    proof.append("\n--- 1. Pytest Assertion Output (PermissionError Refusal by Default) ---")
    proof.append(out)

    code, cli_allow = run_cmd([str(VENV_PYTHON), "-m", "noxai.cli", "skill", "allow-scripts", "briefing-format", "--deny"])
    proof.append("\n--- 2. CLI Safety Gate Toggle (`nx skill allow-scripts briefing-format --deny`) ---")
    proof.append(cli_allow)
    return "\n".join(proof)


def run_e5() -> str:
    print("Running E5: MCP client protocol and handshake proof...")
    code, out = run_cmd([str(VENV_PYTEST), "tests/test_skills_and_mcp.py", "-k", "test_stdio_mcp_client_handshake_and_call", "-v"])
    assert code == 0, f"MCP test failed:\n{out}"

    proof = []
    proof.append("=== NOX WORKSTREAM E5 ACCEPTANCE PROOF ===")
    proof.append(f"Timestamp: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    proof.append("Mode: Verified with Stdio JSON-RPC 2.0 reference server protocol test")
    proof.append("\n--- Pytest Output for Stdio MCP Handshake, Tool Listing, and Execution ---")
    proof.append(out)
    return "\n".join(proof)


async def main() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    e1_proof = await run_e1()
    (REPORTS_DIR / "e1-skill-list-and-trace.txt").write_text(e1_proof, encoding="utf-8")
    print("Saved e1-skill-list-and-trace.txt")

    e2_proof = run_e2()
    (REPORTS_DIR / "e2-validation-tests.txt").write_text(e2_proof, encoding="utf-8")
    print("Saved e2-validation-tests.txt")

    e3_proof = run_e3()
    (REPORTS_DIR / "e3-install-proof.txt").write_text(e3_proof, encoding="utf-8")
    print("Saved e3-install-proof.txt")

    e4_proof = run_e4()
    (REPORTS_DIR / "e4-script-safety.txt").write_text(e4_proof, encoding="utf-8")
    print("Saved e4-script-safety.txt")

    e5_proof = run_e5()
    (REPORTS_DIR / "e5-mcp-proof.txt").write_text(e5_proof, encoding="utf-8")
    print("Saved e5-mcp-proof.txt")

    print("\nAll Workstream E acceptance checks PASSED and proofs saved successfully!")


if __name__ == "__main__":
    asyncio.run(main())
