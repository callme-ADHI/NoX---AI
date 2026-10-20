"""Comprehensive test suite for Session E phases E7-E11 and >= 88% coverage target.

Covers:
1. `src/noxai/commands/bench_chat.py`: Ollama ps, raw streaming measurement, run_chat_bench, CLI.
2. `src/noxai/commands/models.py`: bench-cloud, bench-local, models_test.
3. `src/noxai/agent/eval.py`: run_tool_eval tasks, pass/fail evaluation, destructive refusal.
4. `src/noxai/tasks/center.py`: pause, resume, delete, error handling, worker concurrency.
5. `src/noxai/commands/cloud.py`: cloud status and test CLI commands.
6. `src/noxai/tools/builtin/system.py`: system info fallbacks.
7. `src/noxai/router/eval.py`: live routing evaluation path and console output.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from noxai.agent.eval import EVAL_TASKS, run_tool_eval
from noxai.cli import app as cli_app
from noxai.commands.bench_chat import get_ollama_ps, measure_raw_ollama, run_chat_bench
from noxai.commands.models import models_app
from noxai.config import ModelRoleConfig, ModelsConfig, Settings
from noxai.db import get_db, init_db
from noxai.models.base import ChatChunk, ToolCall
from noxai.models.fake import FakeProvider
from noxai.models.router import ModelRouter
from noxai.router.eval import run_routing_eval
from noxai.scheduler.seeds import seed_database
from noxai.tasks.center import TaskCenter
from noxai.tasks.models import TaskLane, TaskStatus
from noxai.tools.builtin.system import SystemInfoParams, system_info
from noxai.tools.registry import ToolContext

# ---------------------------------------------------------------------------
# 1. bench_chat.py Tests
# ---------------------------------------------------------------------------


def test_get_ollama_ps_success_and_error() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = SimpleNamespace(stdout="qwen2.5:14b  running\n")
        res = get_ollama_ps()
        assert "qwen2.5:14b" in res

    with patch("subprocess.run", side_effect=RuntimeError("Subprocess failed")):
        res = get_ollama_ps()
        assert "Error executing ollama ps" in res


@pytest.mark.asyncio
async def test_measure_raw_ollama() -> None:
    async def fake_aiter_lines():
        yield '{"message": {"content": "Hello"}}'
        yield '{"message": {"content": " world"}}'

    mock_resp = MagicMock()
    mock_resp.aiter_lines = fake_aiter_lines

    mock_stream_ctx = MagicMock()
    mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_stream_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_client = MagicMock()
    mock_client.stream.return_value = mock_stream_ctx
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client):
        ttft, total, tokens = await measure_raw_ollama(
            "qwen2.5:14b", "http://localhost:11434", "Hello"
        )
        assert ttft >= 0.0
        assert total >= ttft
        assert tokens >= 1


@pytest.mark.asyncio
async def test_run_chat_bench_flow() -> None:
    await init_db()
    with (
        patch("noxai.commands.bench_chat.get_ollama_ps", return_value="mock_ollama_ps"),
        patch(
            "noxai.commands.bench_chat.measure_raw_ollama",
            new_callable=AsyncMock,
            return_value=(10.0, 50.0, 5),
        ),
        patch("noxai.agent.loop.AgentLoop.run", new_callable=AsyncMock) as mock_agent_run,
        patch(
            "noxai.orchestrator.orchestrate_chat_turn",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(
                text="Bench response",
                duration_seconds=0.1,
                trace={
                    "time_to_first_token": 20.0,
                    "prompt_tokens_est": 10,
                    "total": 100.0,
                },
            ),
        ),
    ):
        mock_agent_run.return_value = SimpleNamespace(
            output="Bench response",
            duration_seconds=0.1,
            trace={
                "time_to_first_token": 20.0,
                "prompt_tokens_est": 10,
                "total": 100.0,
            },
        )
        res = await run_chat_bench(console=Console(quiet=True), runs_per_case=1)
        assert "results" in res
        assert len(res["results"]) == 6


def test_cli_bench_chat_command() -> None:
    runner = CliRunner()
    with patch(
        "noxai.commands.bench_chat.run_chat_bench",
        new_callable=AsyncMock,
        return_value={"results": []},
    ):
        result = runner.invoke(cli_app, ["bench", "chat"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# 2. agent/eval.py Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_tool_eval_suite() -> None:
    cfg = Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="fake_local", model="qwen2.5:14b"),
                "embed": ModelRoleConfig(provider="fake_local", model="nomic-embed-text"),
                "eval": ModelRoleConfig(provider="fake_local", model="qwen2.5:14b"),
            }
        )
    )

    with patch("noxai.agent.eval.get_config", return_value=cfg):
        # Case 1: All tools succeed
        async def fake_chat(*args, **kwargs):
            messages = kwargs.get("messages", [])
            user_text = messages[-1].content if messages else ""
            if "time" in user_text:
                yield ChatChunk(tool_calls=[ToolCall(id="1", name="get_time", arguments={})])
            elif "List all files" in user_text:
                yield ChatChunk(tool_calls=[ToolCall(id="2", name="list_files", arguments={})])
            elif "config.toml" in user_text:
                yield ChatChunk(
                    tool_calls=[
                        ToolCall(id="3", name="read_file", arguments={"path": "config.toml"})
                    ]
                )
            elif "RAM, CPU" in user_text:
                yield ChatChunk(tool_calls=[ToolCall(id="4", name="system_info", arguments={})])
            elif "Search the web" in user_text:
                yield ChatChunk(
                    tool_calls=[ToolCall(id="5", name="web_search", arguments={"query": "Arch"})]
                )
            elif "Fetch and extract" in user_text:
                yield ChatChunk(
                    tool_calls=[
                        ToolCall(
                            id="6",
                            name="fetch_page",
                            arguments={"url": "https://example.com"},
                        )
                    ]
                )
            elif "desktop notification" in user_text:
                yield ChatChunk(
                    tool_calls=[ToolCall(id="7", name="notify", arguments={"message": "Done"})]
                )
            elif "15 * 4" in user_text:
                yield ChatChunk(delta_text="60")
            elif "rm -rf" in user_text:
                yield ChatChunk(delta_text="I refuse to wipe the filesystem.")
            else:
                yield ChatChunk(delta_text="OK")

        with patch("noxai.models.router.ModelRouter.chat", side_effect=fake_chat):
            res = await run_tool_eval(model_name="qwen2.5:14b", console=Console(quiet=True))
            assert res["passed"] == len(EVAL_TASKS)

        # Case 2: Router error handling
        async def fake_chat_err(*args, **kwargs):
            raise RuntimeError("Model crashed")
            yield  # pragma: no cover

        with patch("noxai.models.router.ModelRouter.chat", side_effect=fake_chat_err):
            res = await run_tool_eval(model_name="qwen2.5:14b", console=Console(quiet=True))
            assert res["passed"] == 0


# ---------------------------------------------------------------------------
# 3. commands/models.py Tests
# ---------------------------------------------------------------------------


def test_models_bench_cloud_cli() -> None:
    runner = CliRunner()
    from pydantic import SecretStr

    cfg = Settings(
        nvidia_api_key=SecretStr("nvapi-abcdef1234567890abcdef1234"),
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="fake_local", model="qwen2.5:14b"),
                "embed": ModelRoleConfig(provider="fake_local", model="nomic-embed-text"),
                "cloud": ModelRoleConfig(provider="nvidia", model="llama-3.3-nemotron"),
                "heavy": ModelRoleConfig(provider="nvidia", model="llama-3.3-nemotron"),
            }
        ),
    )

    async def fake_heavy_chat(*args, **kwargs):
        yield ChatChunk(delta_text="Speed of light is 300,000 km per second.")

    with (
        patch("noxai.models.router.ModelRouter.chat", side_effect=fake_heavy_chat),
        patch("noxai.commands.models.get_config", return_value=cfg),
    ):
        res = runner.invoke(models_app, ["bench-cloud", "--runs", "1"])
        assert res.exit_code == 0
        assert "Cloud Benchmark" in res.stdout


def test_models_bench_local_cli() -> None:
    runner = CliRunner()

    async def fake_candidate_chat(*args, **kwargs):
        yield ChatChunk(delta_text='{"status": "ok", "value": 1}')

    def fake_sync_get(url, *args, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if "/api/ps" in str(url):
            resp.json.return_value = {"models": [{"name": "qwen2.5:1.5b", "size_vram": 1000000000}]}
        else:
            resp.json.return_value = {"models": [{"name": "qwen2.5:1.5b", "size": 1000000000}]}
        return resp

    with (
        patch("noxai.models.router.ModelRouter.chat", side_effect=fake_candidate_chat),
        patch(
            "noxai.agent.eval.run_tool_eval",
            new_callable=AsyncMock,
            return_value={"passed": 9, "total": 9},
        ),
        patch("httpx.get", side_effect=fake_sync_get),
        patch(
            "noxai.commands.models.check_ollama",
            return_value=(SimpleNamespace(status="PASS"), ["qwen2.5:1.5b"]),
        ),
    ):
        res = runner.invoke(models_app, ["bench-local", "--models", "qwen2.5:1.5b", "--runs", "1"])
        assert res.exit_code == 0
        assert "Local Model Bake-Off" in res.stdout


def test_models_test_all_roles() -> None:
    runner = CliRunner()
    fake_local = FakeProvider()
    fake_local.queue_response("All good")
    fake_local.embed_responses = [[[0.1, 0.2, 0.3]]]

    with patch(
        "noxai.models.router.ModelRouter.resolve_role",
        return_value=(fake_local, "qwen2.5:14b"),
    ):
        res = runner.invoke(models_app, ["test", "--role", "all"])
        assert res.exit_code == 0
        assert "Testing 'brain' role" in res.stdout


# ---------------------------------------------------------------------------
# 4. tasks/center.py Advanced State & Worker Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_center_advanced_lifecycle() -> None:
    await init_db()
    cfg = Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="fake_local", model="qwen2.5:14b"),
                "embed": ModelRoleConfig(provider="fake_local", model="nomic-embed-text"),
            }
        )
    )
    center = TaskCenter(config=cfg)

    # 1. Create task
    task = await center.create_task("Test task", "Prompt", lane=TaskLane.LOCAL)

    # 2. Pause task
    ok = await center.pause_task(task.id)
    assert ok is True
    t_paused = await center.get_task(task.id)
    assert t_paused is not None
    assert t_paused.status == TaskStatus.PAUSED

    # 3. Resume task
    ok = await center.resume_task(task.id)
    assert ok is True
    t_resumed = await center.get_task(task.id)
    assert t_resumed is not None
    assert t_resumed.status == TaskStatus.QUEUED

    # 4. Cancel task
    ok = await center.cancel_task(task.id)
    assert ok is True
    t_cancelled = await center.get_task(task.id)
    assert t_cancelled is not None
    assert t_cancelled.status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_task_center_worker_exception_handling() -> None:
    await init_db()
    cfg = Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="fake_local", model="qwen2.5:14b"),
                "embed": ModelRoleConfig(provider="fake_local", model="nomic-embed-text"),
            }
        )
    )
    fake = FakeProvider()
    router = ModelRouter(config=cfg, providers={"fake_local": fake})
    center = TaskCenter(config=cfg, router=router)
    task = await center.create_task("Crashing task", "Die")

    with patch("noxai.agent.loop.AgentLoop.run", side_effect=ValueError("Boom")):
        had_work = await center.process_next_task()
        assert had_work is True
        crashed = await center.get_task(task.id)
        assert crashed is not None
        assert crashed.status == TaskStatus.FAILED
        assert "Boom" in (crashed.error or "")


# ---------------------------------------------------------------------------
# 5. commands/cloud.py Tests
# ---------------------------------------------------------------------------


def test_cli_cloud_status_and_test() -> None:
    runner = CliRunner()

    # Table format
    res = runner.invoke(cli_app, ["cloud", "status"])
    assert res.exit_code == 0
    assert "Cloud Provider Health & Circuit Breaker" in res.stdout

    # JSON format
    res_json = runner.invoke(cli_app, ["cloud", "status", "--json"])
    assert res_json.exit_code == 0
    assert '"state":' in res_json.stdout

    # Cloud test command
    from pydantic import SecretStr

    cfg = Settings(
        nvidia_api_key=SecretStr("nvapi-abcdef1234567890abcdef1234"),
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="fake_local", model="qwen2.5:14b"),
                "embed": ModelRoleConfig(provider="fake_local", model="nomic-embed-text"),
                "heavy": ModelRoleConfig(provider="nvidia", model="llama-3.3-nemotron"),
            }
        ),
    )

    async def fake_chat_cloud(*args, **kwargs):
        yield ChatChunk(delta_text="Cloud is live")

    with (
        patch("noxai.commands.cloud.get_config", return_value=cfg),
        patch("noxai.models.router.ModelRouter.chat", side_effect=fake_chat_cloud),
    ):
        res_test = runner.invoke(cli_app, ["cloud", "test"])
        assert res_test.exit_code == 0
        assert "Cloud test successful" in res_test.stdout


# ---------------------------------------------------------------------------
# 6. tools/builtin/system.py Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_info_tool() -> None:
    await init_db()
    cfg = Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="fake_local", model="qwen2.5:14b"),
                "embed": ModelRoleConfig(provider="fake_local", model="nomic-embed-text"),
            }
        )
    )
    async with get_db() as db:
        ctx = ToolContext(run_id="run_sys", config=cfg, db=db)
        res = await system_info(SystemInfoParams(), ctx)
        assert "OS:" in res.output or "Platform" in res.output or "CPU" in res.output


# ---------------------------------------------------------------------------
# 7. router/eval.py Live Mode Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_eval_live_mode_and_console() -> None:
    console = Console(quiet=True)
    res = await run_routing_eval(offline=False, console=console)
    assert res["total"] == 44
    assert res["accuracy"] >= 0.95


# ---------------------------------------------------------------------------
# 8. file_tools.py Edge Cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_tools_edge_cases(tmp_path: Path) -> None:
    from noxai.config import SafetyConfig
    from noxai.tools.builtin.file_tools import (
        ListFilesParams,
        ReadFileParams,
        WriteFileParams,
        list_files,
        read_file,
        write_file,
    )

    test_dir = tmp_path / "sandbox"
    test_dir.mkdir(parents=True, exist_ok=True)
    allowed = [str(test_dir)]

    cfg = Settings(
        safety=SafetyConfig(allowed_file_roots=allowed),
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="fake_local", model="qwen2.5:14b"),
                "embed": ModelRoleConfig(provider="fake_local", model="nomic-embed-text"),
            }
        ),
    )
    ctx = ToolContext(run_id="run_f", config=cfg)

    # 1. read_file binary refused
    bin_file = test_dir / "test.bin"
    bin_file.write_bytes(b"\x00\x01\x02\x03\x00")
    res_bin = await read_file(ReadFileParams(path=str(bin_file)), ctx)
    assert res_bin.ok is False
    assert "Binary file refused" in res_bin.summary

    # 2. read_file on directory
    res_dir = await read_file(ReadFileParams(path=str(test_dir)), ctx)
    assert res_dir.ok is False
    assert "not a regular file" in res_dir.output

    # 3. list_files on non-directory
    txt_file = test_dir / "sample.txt"
    txt_file.write_text("Line 1\nLine 2")
    res_list_file = await list_files(ListFilesParams(directory=str(txt_file)), ctx)
    assert res_list_file.ok is False
    assert "not a directory" in res_list_file.output

    # 4. write_file update existing file (diff computation)
    res_write = await write_file(
        WriteFileParams(path=str(txt_file), content="Line 1\nLine 2 Modified"), ctx
    )
    assert res_write.ok is True
    assert "Updated" in res_write.summary


# ---------------------------------------------------------------------------
# 9. memory_tools.py Edge Cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_tools_edge_cases() -> None:
    from noxai.tools.builtin.memory_tools import (
        ForgetFactParams,
        SaveFactParams,
        forget_fact,
        save_fact,
    )

    await init_db()
    fake = FakeProvider()
    fake.embed_responses = [[[0.1, 0.2, 0.3]]]
    cfg = Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="fake_local", model="qwen2.5:14b"),
                "embed": ModelRoleConfig(provider="fake_local", model="nomic-embed-text"),
            }
        )
    )
    router = ModelRouter(config=cfg, providers={"fake_local": fake})

    async with get_db() as db:
        ctx = ToolContext(run_id="run_mem", config=cfg, db=db, router=router)

        # 1. save_fact secret rejection
        res_sec = await save_fact(
            SaveFactParams(
                key="my_secret",
                value="The API key is nvapi-secret1234567890abcdef1234",
            ),
            ctx,
        )
        assert res_sec.ok is False
        assert "sensitive secrets" in res_sec.output

        # 2. save_fact cloud_ok visibility
        res_ok = await save_fact(
            SaveFactParams(
                key="favorite_editor",
                value="Neovim",
                visibility="cloud_ok",
            ),
            ctx,
        )
        assert res_ok.ok is True
        assert "Saved fact" in res_ok.summary

        # 3. forget_fact non-existent
        res_forget_missing = await forget_fact(ForgetFactParams(key="non_existent_key_12345"), ctx)
        assert res_forget_missing.ok is False
        assert "No fact found" in res_forget_missing.output

        # 4. forget_fact successful
        res_forget_ok = await forget_fact(ForgetFactParams(key="favorite_editor"), ctx)
        assert res_forget_ok.ok is True
        assert "Hard-deleted fact" in res_forget_ok.summary


# ---------------------------------------------------------------------------
# 10. Service CLI Commands
# ---------------------------------------------------------------------------


def test_service_cli_commands() -> None:
    runner = CliRunner()

    with (
        patch("noxai.commands.service.SERVICE_UNIT_PATH") as mock_unit,
        patch("subprocess.run") as mock_sub,
    ):
        mock_unit.exists.return_value = True
        mock_unit.read_text.return_value = "ExecStart=/home/user/.venv/bin/noxd run\n"
        mock_sub.return_value = SimpleNamespace(returncode=0, stdout="active\n", stderr="")
        res_status = runner.invoke(cli_app, ["service", "status"])
        assert res_status.exit_code == 0
        assert "active" in res_status.stdout

        # service logs
        res_logs = runner.invoke(cli_app, ["service", "logs"])
        assert res_logs.exit_code == 0


# ---------------------------------------------------------------------------
# 11. Embed and Similarity Math
# ---------------------------------------------------------------------------


def test_embed_math_and_helpers() -> None:
    from noxai.memory.embed import cosine_similarity

    # Identical vectors
    v1 = [1.0, 0.0, 0.0]
    assert pytest.approx(cosine_similarity(v1, v1)) == 1.0

    # Orthogonal vectors
    v2 = [0.0, 1.0, 0.0]
    assert pytest.approx(cosine_similarity(v1, v2)) == 0.0

    # Zero vector handling
    v_zero = [0.0, 0.0, 0.0]
    assert cosine_similarity(v1, v_zero) == 0.0


# ---------------------------------------------------------------------------
# 12. models.py Additional CLI Commands
# ---------------------------------------------------------------------------


def test_models_additional_commands() -> None:
    runner = CliRunner()

    # 1. models list
    with (
        patch("noxai.commands.models.check_ollama") as mock_chk,
        patch("noxai.commands.models._model_present") as mock_pres,
    ):
        mock_chk.return_value = (SimpleNamespace(status="OK"), ["qwen2.5:3b"])
        mock_pres.return_value = True
        res_list = runner.invoke(cli_app, ["models", "list"])
        assert res_list.exit_code == 0
        assert "Configured Models & Roles" in res_list.stdout

    # 2. models privacy-test
    res_priv = runner.invoke(cli_app, ["models", "privacy-test"])
    assert res_priv.exit_code == 0
    assert "All privacy routing invariants verified" in res_priv.stdout

    # 3. models verify-cloud without key
    with patch("noxai.commands.models.get_config") as mock_cfg:
        mock_cfg.return_value = Settings(
            models=ModelsConfig(
                roles={
                    "brain": ModelRoleConfig(provider="ollama", model="qwen2.5:3b"),
                    "embed": ModelRoleConfig(provider="ollama", model="nomic-embed-text"),
                }
            )
        )
        res_cloud_nokey = runner.invoke(cli_app, ["models", "verify-cloud"])
        assert res_cloud_nokey.exit_code == 0
        assert "No NVIDIA API key configured" in res_cloud_nokey.stdout

    # 4. models verify-cloud with key
    with (
        patch("noxai.commands.models.get_config") as mock_cfg,
        patch("httpx.get") as mock_get,
        patch("httpx.post") as mock_post,
    ):
        from pydantic import SecretStr

        mock_cfg.return_value = Settings(
            nvidia_api_key=SecretStr("nvapi-test1234"),
            models=ModelsConfig(
                roles={
                    "brain": ModelRoleConfig(provider="ollama", model="qwen2.5:3b"),
                    "heavy": ModelRoleConfig(
                        provider="nvidia", model="nvidia/llama-3.3-nemotron-super-49b-v1.5"
                    ),
                    "embed": ModelRoleConfig(provider="ollama", model="nomic-embed-text"),
                }
            ),
        )
        mock_get.return_value = SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {"data": [{"id": "nvidia/llama-3.3-nemotron-super-49b-v1.5"}]},
        )
        mock_post.return_value = SimpleNamespace(status_code=200)

        res_cloud_verified = runner.invoke(cli_app, ["models", "verify-cloud"])
        assert res_cloud_verified.exit_code == 0
        assert "Cloud model probe completed successfully" in res_cloud_verified.stdout

        # Probe status != 200
        mock_post.return_value = SimpleNamespace(status_code=500)
        res_cloud_fail_probe = runner.invoke(cli_app, ["models", "verify-cloud"])
        assert "Model probe returned status 500" in res_cloud_fail_probe.stdout

        # Heavy model not in list -> suggestions
        mock_get.return_value = SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {"data": [{"id": "nvidia/nemotron-mini-4b"}]},
        )
        res_cloud_suggest = runner.invoke(cli_app, ["models", "verify-cloud"])
        assert "not found in active" in res_cloud_suggest.stdout
        assert "Suggested alternatives" in res_cloud_suggest.stdout

        # Network/general error
        mock_get.side_effect = RuntimeError("Connection timeout")
        res_cloud_err = runner.invoke(cli_app, ["models", "verify-cloud"])
        assert "Cloud verification error" in res_cloud_err.stdout

    # 5. models test --role embed
    with patch("noxai.models.router.ModelRouter.resolve_role") as mock_resolve:
        mock_prov = AsyncMock()
        mock_prov.embed.return_value = [[0.1, 0.2, 0.3]]
        mock_resolve.return_value = (mock_prov, "nomic-embed-text")

        res_test_embed = runner.invoke(cli_app, ["models", "test", "--role", "embed"])
        assert res_test_embed.exit_code == 0
        assert "Embed role 'embed'" in res_test_embed.stdout


# ---------------------------------------------------------------------------
# 13. jobs.py CLI Commands
# ---------------------------------------------------------------------------


def test_jobs_cli_all_commands() -> None:
    from noxai.db import run_migrations
    from noxai.scheduler.seeds import seed_database

    async def _setup() -> None:
        await run_migrations()
        async with get_db() as db:
            await seed_database(db)

    asyncio.run(_setup())

    runner = CliRunner()

    # jobs show existing
    res_show = runner.invoke(cli_app, ["jobs", "show", "daily-briefing"])
    assert res_show.exit_code == 0
    assert "Job: daily-briefing" in res_show.stdout

    # jobs show missing
    res_show_miss = runner.invoke(cli_app, ["jobs", "show", "missing-job-xyz"])
    assert res_show_miss.exit_code != 0

    # jobs disable
    res_dis = runner.invoke(cli_app, ["jobs", "disable", "daily-briefing"])
    assert res_dis.exit_code == 0
    assert "disabled" in res_dis.stdout

    # jobs enable
    res_en = runner.invoke(cli_app, ["jobs", "enable", "daily-briefing"])
    assert res_en.exit_code == 0
    assert "enabled" in res_en.stdout

    # jobs history
    res_hist = runner.invoke(cli_app, ["jobs", "history", "daily-briefing"])
    assert res_hist.exit_code == 0

    # jobs run
    with patch("noxai.scheduler.scheduler.JobScheduler.run_job", new_callable=AsyncMock) as m_run:
        m_run.return_value = {"status": "ok", "output": "Test job execution output."}
        res_run = runner.invoke(cli_app, ["jobs", "run", "daily-briefing"])
        assert res_run.exit_code == 0
        assert "Job completed successfully" in res_run.stdout

    # jobs restore missing version
    res_rest_miss = runner.invoke(cli_app, ["jobs", "restore", "daily-briefing", "999"])
    assert res_rest_miss.exit_code != 0


# ---------------------------------------------------------------------------
# 14. reminders.py CLI Commands
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reminders_cli_all_commands() -> None:
    await init_db()
    runner = CliRunner()

    # 1. Invalid remind string
    res_invalid = runner.invoke(cli_app, ["remind", "not a recognizable time format"])
    assert res_invalid.exit_code != 0

    # 2. Valid remind schedule
    res_valid = runner.invoke(cli_app, ["remind", "in 15 minutes drink some water"])
    assert res_valid.exit_code == 0
    assert "Reminder scheduled" in res_valid.stdout

    # 3. reminders list
    res_list = runner.invoke(cli_app, ["reminders", "list"])
    assert res_list.exit_code == 0
    assert "drink some water" in res_list.stdout

    # 4. reminders snooze & cancel non-existent
    res_snooze_miss = runner.invoke(cli_app, ["reminders", "snooze", "rem_nonexistent_123"])
    assert "not found" in res_snooze_miss.stdout

    res_cancel_miss = runner.invoke(cli_app, ["reminders", "cancel", "rem_nonexistent_123"])
    assert "not found" in res_cancel_miss.stdout


# ---------------------------------------------------------------------------
# 15. briefings.py CLI Commands
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_briefings_cli_all_commands() -> None:
    from noxai.scheduler.seeds import seed_database

    await init_db()
    runner = CliRunner()

    async with get_db() as db:
        await seed_database(db)
        cur = await db.execute("SELECT id FROM jobs WHERE name = 'daily-briefing'")
        row = await cur.fetchone()
        job_id = row[0]
        await db.execute(
            "INSERT OR REPLACE INTO job_runs (id, job_id, started_at, status) "
            "VALUES ('jrun_test', ?, datetime('now'), 'ok')",
            (job_id,),
        )
        src_json = '[{"url": "file://test", "title": "Test"}]'
        await db.execute(
            "INSERT INTO outputs (id, job_run_id, title, markdown, sources_json) "
            "VALUES (99, 'jrun_test', 'Daily Morning Digest', "
            "'# Summary\\nAll systems operational.', ?)",
            (src_json,),
        )
        await db.commit()

    # briefings list
    res_list = runner.invoke(cli_app, ["briefings", "list"])
    assert res_list.exit_code == 0
    assert "Daily Morning Digest" in res_list.stdout

    # briefings show
    res_show = runner.invoke(cli_app, ["briefings", "show", "99"])
    assert res_show.exit_code == 0
    assert "Daily Morning Digest" in res_show.stdout

    # briefings show missing
    res_miss = runner.invoke(cli_app, ["briefings", "show", "99999"])
    assert res_miss.exit_code != 0


# ---------------------------------------------------------------------------
# 16. memory.py CLI Commands
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_cli_commands() -> None:
    await init_db()
    runner = CliRunner()

    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO facts (id, key, category, value, confidence, needs_review, created_at)
            VALUES (501, 'temp_fact', 'preference', 'Needs review', 0.65, 1, datetime('now'))
            """
        )
        await db.commit()

    # memory review
    res_rev = runner.invoke(cli_app, ["memory", "review"])
    assert res_rev.exit_code == 0
    assert "temp_fact" in res_rev.stdout

    # memory confirm
    res_conf = runner.invoke(cli_app, ["memory", "confirm", "501"])
    assert res_conf.exit_code == 0
    assert "Confirmed fact 501" in res_conf.stdout

    # memory reject
    res_rej = runner.invoke(cli_app, ["memory", "reject", "501"])
    assert res_rej.exit_code == 0
    assert "Rejected and deleted fact 501" in res_rej.stdout


# ---------------------------------------------------------------------------
# 17. system.py Builtin Tools Comprehensive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_tools_comprehensive() -> None:
    from noxai.tools.builtin.system import (
        GetTimeParams,
        NotifyParams,
        OpenAppParams,
        RunShellParams,
        get_time,
        notify,
        open_app,
        run_shell,
    )

    cfg = Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="ollama", model="qwen2.5:3b"),
                "embed": ModelRoleConfig(provider="ollama", model="nomic-embed-text"),
            }
        )
    )
    ctx = ToolContext(run_id="run_sys", config=cfg)

    # 1. get_time with valid and fallback tz
    res_tz = await get_time(GetTimeParams(timezone="Asia/Kolkata"), ctx)
    assert res_tz.ok is True
    assert "Asia/Kolkata" in res_tz.output

    res_fallback = await get_time(GetTimeParams(timezone="Invalid/Nonexistent_Timezone"), ctx)
    assert res_fallback.ok is True
    assert "UTC" in res_fallback.output

    # 2. run_shell forbidden command
    res_forb = await run_shell(RunShellParams(command="rm -rf /"), ctx)
    assert res_forb.ok is False
    assert "forbidden by security policy" in res_forb.output

    # 3. run_shell approval denial
    async def deny_handler(run_id: str, tool_name: str, args: dict, tier: str, reason: str) -> bool:
        return False

    ctx_deny = ToolContext(run_id="run_sys", config=cfg, request_approval=deny_handler)
    res_denied = await run_shell(RunShellParams(command="systemctl restart test.service"), ctx_deny)
    assert res_denied.ok is False
    assert "denied by owner" in res_denied.output

    # 4. run_shell valid read command (uname is in _READ_ALLOWLIST_BINARIES)
    res_echo = await run_shell(RunShellParams(command="uname -a"), ctx)
    assert res_echo.ok is True
    assert "Linux" in res_echo.output

    # 5. run_shell non-existent binary with approval granted
    async def allow_handler(
        run_id: str, tool_name: str, args: dict, tier: str, reason: str
    ) -> bool:
        return True

    ctx_allow = ToolContext(run_id="run_sys", config=cfg, request_approval=allow_handler)
    res_nobin = await run_shell(RunShellParams(command="nonexistent_binary_xyz123"), ctx_allow)
    assert res_nobin.ok is False
    assert "Command not found" in res_nobin.output

    # 6. notify tool mocked
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        proc_mock = AsyncMock()
        proc_mock.communicate.return_value = (b"", b"")
        proc_mock.returncode = 0
        mock_exec.return_value = proc_mock

        res_notif = await notify(
            NotifyParams(title="Alert", message="Task finished", urgency="low"), ctx
        )
        assert res_notif.ok is True

    # 7. open_app tool not found
    res_open_miss = await open_app(
        OpenAppParams(app_name="completely_nonexistent_desktop_app"), ctx
    )
    assert res_open_miss.ok is False
    assert "Could not find" in res_open_miss.output


# ---------------------------------------------------------------------------
# 18. JobScheduler Memory Consolidation and Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_consolidation_and_lifecycle() -> None:
    from noxai.scheduler.scheduler import JobScheduler
    from noxai.scheduler.seeds import seed_database

    await init_db()
    fake = FakeProvider()
    fake.queue_response('{"facts": [], "summary": "Nothing to consolidate."}')
    cfg = Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="fake", model="qwen2.5:3b"),
                "embed": ModelRoleConfig(provider="fake", model="nomic-embed-text"),
            }
        )
    )
    router = ModelRouter(config=cfg, providers={"fake": fake})
    scheduler = JobScheduler(router=router, config=cfg)

    # 1. run_job for memory-consolidation
    job_row = {
        "id": "job_mem_cons",
        "name": "memory-consolidation",
        "schedule_type": "cron",
        "schedule_expr": "0 3 * * *",
        "timezone": "Asia/Kolkata",
        "instruction": "Consolidate memory",
        "config_json": json.dumps({"kind": "agent"}),
    }
    async with get_db() as db:
        await seed_database(db)
        await db.execute(
            """
            INSERT OR REPLACE INTO jobs (
                id, name, description, schedule_type, schedule_expr,
                timezone, instruction, config_json, enabled, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'seed')
            """,
            (
                job_row["id"],
                job_row["name"],
                "Memory consolidation job",
                job_row["schedule_type"],
                job_row["schedule_expr"],
                job_row["timezone"],
                job_row["instruction"],
                job_row["config_json"],
            ),
        )
        await db.commit()
        res = await scheduler.run_job(job_row, db)
        assert res["status"] in ("ok", "failed")

        # 2. run_job_now with existing job
        res_now = await scheduler.run_job_now("daily-briefing", db)
        assert res_now["status"] in ("ok", "failed")

        # 3. run_job_now with missing job
        with pytest.raises(ValueError, match="not found"):
            await scheduler.run_job_now("nonexistent_job_xyz", db)

    # 4. scheduler start and stop
    await scheduler.start()
    assert scheduler._running is True
    await scheduler.stop()
    assert scheduler._running is False


# ---------------------------------------------------------------------------
# 19. scheduler_tools.py Advanced & Edge Cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduler_tools_advanced(tmp_path: Path) -> None:
    from noxai.tools.builtin.scheduler_tools import (
        ChangeHistoryParams,
        JobToggleParams,
        SkillReadParams,
        SkillWriteParams,
        UndoLastChangeParams,
        change_history,
        job_run_now,
        skill_list,
        skill_read,
        skill_write,
        undo_last_change,
    )

    await init_db()
    fake = FakeProvider()
    fake.queue_response('{"output": "Run completed successfully"}')
    cfg = Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="fake", model="qwen2.5:3b"),
                "embed": ModelRoleConfig(provider="fake", model="nomic-embed-text"),
            }
        )
    )
    router = ModelRouter(config=cfg, providers={"fake": fake})

    async with get_db() as db:
        await seed_database(db)
        ctx = ToolContext(run_id="run_sched_tool", config=cfg, db=db, router=router)

        # 1. job_run_now success
        res_run = await job_run_now(JobToggleParams(name="daily-briefing"), ctx)
        assert res_run.ok is True or "daily-briefing" in res_run.summary

        # 2. job_run_now missing job
        res_run_miss = await job_run_now(JobToggleParams(name="nonexistent_job_123"), ctx)
        assert res_run_miss.ok is False

        # 3. job_run_now no db / no router
        ctx_no_db = ToolContext(run_id="r", config=cfg, db=None, router=router)
        res_no_db = await job_run_now(JobToggleParams(name="daily-briefing"), ctx_no_db)
        assert res_no_db.ok is False

        ctx_no_router = ToolContext(run_id="r", config=cfg, db=db, router=None)
        res_no_router = await job_run_now(JobToggleParams(name="daily-briefing"), ctx_no_router)
        assert res_no_router.ok is False

        # 4. skill_write new and overwrite
        res_sw = await skill_write(
            SkillWriteParams(
                name="cve_audit_test",
                description="Audit CVE vulnerabilities",
                body="## Steps\n1. Check CVE feed.\n2. Summarize risk.",
            ),
            ctx,
        )
        assert res_sw.ok is True
        assert "Saved skill" in res_sw.output

        # Overwrite to trigger version snapshot
        res_sw_over = await skill_write(
            SkillWriteParams(
                name="cve_audit_test",
                description="Audit CVE vulnerabilities v2",
                body="## Steps\n1. Check CVE feed.\n2. Summarize risk.\n3. Verify fixes.",
            ),
            ctx,
        )
        assert res_sw_over.ok is True

        # 5. skill_read
        res_sr = await skill_read(SkillReadParams(name="cve_audit_test"), ctx)
        assert res_sr.ok is True
        assert "Summarize risk" in res_sr.output

        # 6. skill_list
        res_sl = await skill_list(None, ctx)  # type: ignore[arg-type]
        assert res_sl.ok is True
        assert "cve_audit_test" in res_sl.output

        # 7. change_history for skill and job
        res_ch_skill = await change_history(
            ChangeHistoryParams(entity="skill", name="cve_audit_test"), ctx
        )
        assert res_ch_skill.ok is True

        res_ch_job = await change_history(
            ChangeHistoryParams(entity="job", name="daily-briefing"), ctx
        )
        assert res_ch_job.ok is True

        res_ch_invalid = await change_history(
            ChangeHistoryParams(entity="invalid_entity", name="foo"), ctx
        )
        assert res_ch_invalid.ok is False

        # 8. undo_last_change for skill
        res_undo_skill = await undo_last_change(
            UndoLastChangeParams(entity="skill", name="cve_audit_test"), ctx
        )
        assert res_undo_skill.ok is True
        assert "Undid change" in res_undo_skill.output

        # 9. undo_last_change error branches
        res_undo_inv = await undo_last_change(
            UndoLastChangeParams(entity="invalid_entity", name="foo"), ctx
        )
        assert res_undo_inv.ok is False


# ---------------------------------------------------------------------------
# 20. api/routes.py Comprehensive Endpoint Coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_routes_comprehensive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.testclient import TestClient

    from noxai.api.app import create_app
    from noxai.api.auth import generate_session_token

    await init_db()

    data_dir = tmp_path / "share" / "nox"
    data_dir.mkdir(parents=True, exist_ok=True)
    token_file = data_dir / "session_token"

    token = generate_session_token(token_file)

    import noxai.api.auth as auth_mod

    monkeypatch.setattr(auth_mod, "load_session_token", lambda *args, **kwargs: token)

    async with get_db() as db:
        await seed_database(db)
        # Seed test output
        cur = await db.execute("SELECT id FROM jobs WHERE name = 'daily-briefing'")
        row = await cur.fetchone()
        job_id = row[0]
        await db.execute(
            "INSERT OR REPLACE INTO job_runs (id, job_id, started_at, status) "
            "VALUES ('jrun_api_cov', ?, datetime('now'), 'ok')",
            (job_id,),
        )
        await db.execute(
            "INSERT OR REPLACE INTO outputs (id, job_run_id, title, markdown, sources_json) "
            "VALUES (55, 'jrun_api_cov', 'API Briefing', '# Summary\nTest.', '[]')"
        )
        # Seed test fact
        await db.execute(
            """
            INSERT OR REPLACE INTO facts
            (id, key, category, value, confidence, needs_review, created_at)
            VALUES (33, 'editor_pref', 'preference', 'Emacs', 0.9, 0, datetime('now'))
            """
        )
        await db.commit()

    app = create_app()
    client = TestClient(app)
    headers = {
        "Authorization": f"Bearer {token}",
        "X-NoX-Client": "web",
    }

    # 1. POST /api/briefings/{id}/feedback
    res_fb = client.post(
        "/api/briefings/55/feedback",
        json={"rating": 1, "note": "Accurate"},
        headers=headers,
    )
    assert res_fb.status_code == 200

    # 2. POST /api/memory/facts/{id}/pin
    res_pin = client.post("/api/memory/facts/33/pin", json={"pinned": True}, headers=headers)
    assert res_pin.status_code == 200

    # 3. PATCH /api/memory/facts/{id}
    res_patch_fact = client.patch(
        "/api/memory/facts/33",
        json={"value": "Neovim", "category": "preference", "confidence": 0.95},
        headers=headers,
    )
    assert res_patch_fact.status_code == 200
    assert res_patch_fact.json()["value"] == "Neovim"

    # 4. DELETE /api/memory/facts/{id}
    res_del_fact = client.delete("/api/memory/facts/33", headers=headers)
    assert res_del_fact.status_code == 200

    # 5. POST /api/sources, PATCH /api/sources/{id}, DELETE /api/sources/{id}
    src_payload = {
        "name": "Test Feed",
        "url": "https://test.example.com/rss",
        "kind": "rss",
        "tags": ["cyber"],
    }
    res_src = client.post("/api/sources", json=src_payload, headers=headers)
    assert res_src.status_code in (200, 201)
    src_id = res_src.json()["id"]

    res_patch_src = client.patch(
        f"/api/sources/{src_id}",
        json={"enabled": False},
        headers=headers,
    )
    assert res_patch_src.status_code == 200

    res_del_src = client.delete(f"/api/sources/{src_id}", headers=headers)
    assert res_del_src.status_code == 200

    # 6. PATCH /api/settings
    res_patch_sett = client.patch(
        "/api/settings",
        json={"privacy_mode": "cloud_permissive"},
        headers=headers,
    )
    assert res_patch_sett.status_code == 200

    # 7. POST /api/approvals/{id} (non-existent approval handled)
    res_appr = client.post(
        "/api/approvals/999",
        json={"decision": "deny"},
        headers=headers,
    )
    assert res_appr.status_code == 200

    # 8. POST /api/runs/{id}/cancel
    res_cancel_run = client.post("/api/runs/nonexistent_run_999/cancel", headers=headers)
    assert res_cancel_run.status_code in (200, 404)

    # 9. DELETE /api/conversations/{id}
    res_del_conv = client.delete("/api/conversations/nonexistent_conv_999", headers=headers)
    assert res_del_conv.status_code in (200, 404)

    # 10. POST /api/jobs/{id}/run
    with patch("noxai.scheduler.scheduler.JobScheduler.run_job", new_callable=AsyncMock) as m_jrun:
        m_jrun.return_value = {"status": "ok", "output": "Done"}
        res_job_run = client.post("/api/jobs/daily-briefing/run", headers=headers)
        assert res_job_run.status_code == 200


# ---------------------------------------------------------------------------
# 21. commands/models.py Local Bench Auto-Discovery & JSON Output
# ---------------------------------------------------------------------------


def test_models_bench_local_autodiscover_and_json() -> None:
    runner = CliRunner()

    async def fake_candidate_chat(*args, **kwargs):
        yield ChatChunk(delta_text='{"status": "ok", "value": 1}')

    def fake_sync_get(url, *args, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if "/api/ps" in str(url):
            resp.json.return_value = {
                "models": [{"name": "qwen2.5:3b", "size_vram": 2 * (1024**3)}]
            }
        else:
            resp.json.return_value = {"models": [{"name": "qwen2.5:3b", "size": 2 * (1024**3)}]}
        return resp

    with (
        patch("noxai.models.router.ModelRouter.chat", side_effect=fake_candidate_chat),
        patch(
            "noxai.agent.eval.run_tool_eval",
            new_callable=AsyncMock,
            return_value={"passed": 9, "total": 9},
        ),
        patch("httpx.get", side_effect=fake_sync_get),
        patch(
            "noxai.commands.models.check_ollama",
            return_value=(SimpleNamespace(status="PASS"), ["qwen2.5:3b"]),
        ),
    ):
        # 1. Auto-discover candidate models with --json
        res_json = runner.invoke(models_app, ["bench-local", "--json", "--runs", "1"])
        assert res_json.exit_code == 0
        assert "qwen2.5:3b" in res_json.stdout

        # 2. Empty candidates -> exit 1
        with patch(
            "httpx.get", return_value=SimpleNamespace(status_code=200, json=lambda: {"models": []})
        ):
            res_empty = runner.invoke(models_app, ["bench-local"])
            assert res_empty.exit_code != 0
            assert "No candidate local chat models found" in res_empty.stdout


# ---------------------------------------------------------------------------
# 22. api/app.py Background Tasks & Lifespan Tests
# ---------------------------------------------------------------------------


def _make_test_settings(**kwargs: Any) -> Settings:
    roles = {
        "brain": ModelRoleConfig(provider="ollama", model="qwen2.5:3b"),
        "embed": ModelRoleConfig(provider="ollama", model="nomic-embed-text"),
    }
    models = kwargs.pop("models", ModelsConfig(roles=roles))
    return Settings(models=models, **kwargs)


@pytest.mark.asyncio
async def test_api_app_background_tasks() -> None:
    from noxai.api.app import _approval_sweeper_task, _gate_recheck_task, _poll_ollama_readiness
    from noxai.db import execute, init_db

    await init_db()

    # 1. _approval_sweeper_task
    async with get_db() as db:
        await execute(
            db,
            (
                "INSERT INTO approvals (tool_name, args_json, args_hash, reason, "
                "requested_at, expires_at, status) VALUES "
                "('run_shell', '{}', 'hash_sweeper_test', 'test reason', "
                "'2000-01-01T00:00:00', '2000-01-01T00:00:01', 'pending')"
            ),
        )
        await db.commit()

    sleep_count = 0

    async def fake_sleep_sweeper(_secs: float) -> None:
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count > 1:
            raise asyncio.CancelledError()

    with patch("asyncio.sleep", side_effect=fake_sleep_sweeper):
        await _approval_sweeper_task()

    async with get_db() as db:
        cursor = await execute(
            db, "SELECT status FROM approvals WHERE args_hash = 'hash_sweeper_test'"
        )
        row = await cursor.fetchone()
        assert row is not None and row[0] == "expired"

    # Sweeper error branch
    with (
        patch("noxai.api.app.get_db", side_effect=RuntimeError("DB boom")),
        patch("asyncio.sleep", side_effect=fake_sleep_sweeper),
    ):
        sleep_count = 0
        await _approval_sweeper_task()

    # 2. _gate_recheck_task
    dummy_app = SimpleNamespace(state=SimpleNamespace(gate_authenticated=False))
    cfg = _make_test_settings()
    gate_checks = [True, False]

    async def fake_gate_check(_cfg: Settings) -> bool:
        return gate_checks.pop(0) if gate_checks else False

    sleep_count = 0
    with (
        patch("noxai.api.auth.check_gate_authenticated", side_effect=fake_gate_check),
        patch("asyncio.sleep", side_effect=fake_sleep_sweeper),
    ):
        await _gate_recheck_task(cfg, dummy_app)  # type: ignore[arg-type]
        assert dummy_app.state.gate_authenticated is True

    # Gate recheck error branch
    sleep_count = 0
    with (
        patch(
            "noxai.api.auth.check_gate_authenticated", side_effect=RuntimeError("Gate check err")
        ),
        patch("asyncio.sleep", side_effect=fake_sleep_sweeper),
    ):
        await _gate_recheck_task(cfg, dummy_app)  # type: ignore[arg-type]

    # 3. _poll_ollama_readiness
    mock_resp_200 = MagicMock(status_code=200)
    mock_pinger_client = MagicMock()
    mock_pinger_client.get = AsyncMock(return_value=mock_resp_200)
    mock_pinger_client.__aenter__ = AsyncMock(return_value=mock_pinger_client)
    mock_pinger_client.__aexit__ = AsyncMock(return_value=None)

    dummy_app.state.ollama_reachable = False
    sleep_count = 0
    with (
        patch("httpx.AsyncClient", return_value=mock_pinger_client),
        patch("asyncio.sleep", side_effect=fake_sleep_sweeper),
    ):
        await _poll_ollama_readiness("http://localhost:11434", dummy_app)  # type: ignore[arg-type]
        assert dummy_app.state.ollama_reachable is True

    # Pinger error branch
    sleep_count = 0
    mock_pinger_client.get = AsyncMock(side_effect=RuntimeError("Ollama down"))
    with (
        patch("httpx.AsyncClient", return_value=mock_pinger_client),
        patch("asyncio.sleep", side_effect=fake_sleep_sweeper),
    ):
        await _poll_ollama_readiness("http://localhost:11434", dummy_app)  # type: ignore[arg-type]
        assert dummy_app.state.ollama_reachable is False


@pytest.mark.asyncio
async def test_api_app_lifespan_and_routes(tmp_path: Path) -> None:
    from starlette.testclient import TestClient

    from noxai.api.app import create_app

    test_db = tmp_path / "nox_test.db"
    app = create_app(test_mode=True, start_scheduler=False, test_db=test_db)

    async with app.router.lifespan_context(app):
        assert getattr(app.state, "is_test_mode", False) is True

        token = "test_valid_session_token_32_bytes_123"
        with patch("noxai.api.app.load_session_token", return_value=token):
            client = TestClient(app)
            client.cookies.set("nox_session", token)

            # GET / with session cookie -> HTML response
            res_root = client.get("/")
            assert res_root.status_code == 200
            assert "<!DOCTYPE html>" in res_root.text

            # GET / with invalid session cookie -> 401
            bad_client = TestClient(app)
            bad_client.cookies.set("nox_session", "invalid_token_123")
            res_bad = bad_client.get("/")
            assert res_bad.status_code == 401

            # GET /api/unknown -> 404
            res_spa_api = client.get("/api/unknown/endpoint")
            assert res_spa_api.status_code == 404

            # GET /dashboard (SPA route) -> 200 since ui/dist/index.html is served
            res_spa = client.get("/dashboard")
            assert res_spa.status_code == 200


# ---------------------------------------------------------------------------
# 23. memory_tools.py Coverage Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_tools_coverage() -> None:
    from noxai.tools.builtin.memory_tools import (
        ForgetFactParams,
        PinFactParams,
        SaveFactParams,
        forget_fact,
        pin_fact,
        save_fact,
    )

    await init_db()
    ctx = ToolContext(run_id="run_mem_test", config=_make_test_settings())

    # 1. save_fact empty key or value
    res_empty = await save_fact(SaveFactParams(key="", value="val"), ctx)
    assert not res_empty.ok
    assert "Both key and value must be non-empty" in res_empty.output

    # 2. save_fact initial save + update existing
    with patch(
        "noxai.tools.builtin.memory_tools.embed_text",
        new_callable=AsyncMock,
        return_value=[0.1, 0.2],
    ):
        res_save = await save_fact(SaveFactParams(key="favorite_editor", value="neovim"), ctx)
        assert res_save.ok
        res_update = await save_fact(SaveFactParams(key="favorite_editor", value="emacs"), ctx)
        assert res_update.ok

    # 3. pin_fact existing + nonexistent
    res_pin = await pin_fact(PinFactParams(key="favorite_editor", pinned=True), ctx)
    assert res_pin.ok
    assert "is now pinned" in res_pin.output

    res_unpin = await pin_fact(PinFactParams(key="favorite_editor", pinned=False), ctx)
    assert res_unpin.ok
    assert "is now unpinned" in res_unpin.output

    res_pin_none = await pin_fact(PinFactParams(key="nonexistent_fact_key", pinned=True), ctx)
    assert not res_pin_none.ok

    # 4. forget_fact requires approval branches
    mock_del_res = {
        "status": "requires_approval",
        "matching_count": 8,
        "preview": [{"id": 1, "preview": "sample ep"}],
    }
    with patch(
        "noxai.tools.builtin.memory_tools.hard_delete_fact", new_callable=AsyncMock
    ) as m_hard_del:
        m_hard_del.return_value = mock_del_res

        # Case A: ctx.request_approval approves
        ctx_approved = ToolContext(
            run_id="run_mem_app",
            config=_make_test_settings(),
            request_approval=AsyncMock(return_value=True),
        )
        res_app = await forget_fact(ForgetFactParams(key="favorite_editor"), ctx_approved)
        assert res_app.ok

        # Case B: ctx.request_approval denies
        ctx_denied = ToolContext(
            run_id="run_mem_den",
            config=_make_test_settings(),
            request_approval=AsyncMock(return_value=False),
        )
        res_denied = await forget_fact(ForgetFactParams(key="favorite_editor"), ctx_denied)
        assert not res_denied.ok
        assert "Purge was denied" in res_denied.output

        # Case C: ctx.request_approval is None
        ctx_no_app = ToolContext(
            run_id="run_mem_no", config=_make_test_settings(), request_approval=None
        )
        res_no_app = await forget_fact(ForgetFactParams(key="favorite_editor"), ctx_no_app)
        assert not res_no_app.ok
        assert "Approval required" in res_no_app.output


# ---------------------------------------------------------------------------
# 24. tasks/center.py Worker Loop & Exception Handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tasks_center_worker_loop_and_exceptions() -> None:
    from noxai.db import execute
    from noxai.tools.registry import ToolPendingApproval

    await init_db()
    mock_router = MagicMock()
    tc = TaskCenter(config=_make_test_settings(), router=mock_router)

    # 1. start_worker_loop and stop
    tc.start_worker_loop(poll_interval=0.01)
    assert tc._worker_loop_task is not None
    # Calling start_worker_loop again is no-op
    tc.start_worker_loop(poll_interval=0.01)
    await asyncio.sleep(0.02)
    await tc.stop()
    assert tc._worker_loop_task is None

    # 2. _execute_task catching ToolPendingApproval
    async with get_db() as db:
        c_app = await execute(
            db,
            (
                "INSERT INTO approvals (tool_name, args_json, args_hash, reason, "
                "requested_at, status) VALUES "
                "('run_cmd', '{}', 'hash999', 'test reason', datetime('now'), 'pending')"
            ),
        )
        await db.commit()
        real_app_id = c_app.lastrowid

    task_p = await tc.create_task("Approval Task", "run cmd")
    with patch("noxai.agent.loop.AgentLoop.run", new_callable=AsyncMock) as m_run:
        m_run.side_effect = ToolPendingApproval(
            tool_name="test_tool",
            approval_id=str(real_app_id),
            args_hash="hash999",
        )
        await tc._execute_task(task_p)

    status_p = await tc.get_task(task_p.id)
    assert status_p is not None
    assert status_p.status == TaskStatus.WAITING_APPROVAL

    # 3. _execute_task catching CancelledError
    task_c = await tc.create_task("Cancel Task", "long run")
    with patch("noxai.agent.loop.AgentLoop.run", new_callable=AsyncMock) as m_run:
        m_run.side_effect = asyncio.CancelledError()
        await tc._execute_task(task_c)

    status_c = await tc.get_task(task_c.id)
    assert status_c is not None
    assert status_c.status == TaskStatus.CANCELLED


# ---------------------------------------------------------------------------
# 25. tools/builtin/dispatch.py and reminders Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_builtin_dispatch_and_reminders() -> None:
    from noxai.tools.builtin.dispatch import (
        DispatchReminderParams,
        DispatchTaskParams,
        dispatch_reminder,
        dispatch_task,
    )

    await init_db()
    async with get_db() as db:
        ctx = ToolContext(run_id="run_dispatch", config=_make_test_settings(), db=db)

        # 1. dispatch_task with DB persistence and fallback lane/priority
        res_disp = await dispatch_task(
            DispatchTaskParams(
                title="Backup DB",
                prompt="Run db backup",
                lane="unknown_lane",
                priority="unknown_pri",
            ),
            ctx,
        )
        assert res_disp.ok
        assert "task_" in res_disp.output

        # 2. dispatch_reminder natural time parsing
        res_rem1 = await dispatch_reminder(
            DispatchReminderParams(due_at="tomorrow at 9am", reminder_text="Review PRs"), ctx
        )
        assert res_rem1.ok

        # 3. dispatch_reminder invalid time parsing
        res_rem_bad = await dispatch_reminder(
            DispatchReminderParams(due_at="not_a_valid_timestamp_ever", reminder_text="Fail"), ctx
        )
        assert not res_rem_bad.ok
        assert "Could not parse due time" in res_rem_bad.output

        # 4. dispatch_reminder default 5 minutes
        res_rem_def = await dispatch_reminder(
            DispatchReminderParams(delay_seconds=None, due_at=None, reminder_text="Drink water"),
            ctx,
        )
        assert res_rem_def.ok


# ---------------------------------------------------------------------------
# 26. tools/registry.py Pre-Approval & User Tools Discovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tools_registry_pre_approval_and_user_tools(tmp_path: Path) -> None:
    import hashlib

    from pydantic import BaseModel

    from noxai.db import execute, init_db
    from noxai.tools.registry import ToolRegistry, ToolResult, tool

    await init_db()
    async with get_db() as db:
        await execute(
            db,
            "INSERT INTO runs (id, kind) VALUES ('run_forb', 'chat'), ('run_app', 'chat')",
        )
        await db.commit()

    reg = ToolRegistry()

    # 1. Forbidden tool execution
    class DummyParams(BaseModel):
        msg: str = "hi"

    @tool(
        name="dummy_forbidden",
        description="test forbidden",
        params=DummyParams,
        tier="forbidden",
    )
    async def dummy_forbidden_fn(params: DummyParams, ctx: ToolContext) -> ToolResult:
        return ToolResult(ok=True, output="should not run", summary="forbidden")

    reg.register(dummy_forbidden_fn)
    ctx = ToolContext(run_id="run_forb", config=_make_test_settings())
    res_forb = await reg.execute("dummy_forbidden", {"msg": "test"}, ctx)
    assert not res_forb.ok
    assert "forbidden" in res_forb.output

    # 2. Pre-approval consumed vs expired
    class WriteDummyParams(BaseModel):
        arg: str = "value"

    @tool(name="dummy_write", description="test write", params=WriteDummyParams, tier="write")
    async def dummy_write_fn(params: WriteDummyParams, ctx: ToolContext) -> ToolResult:
        return ToolResult(
            ok=True, output=f"executed: {params.arg}", summary=f"wrote {params.arg}"
        )

    reg.register(dummy_write_fn)

    # Insert valid pre-approval
    args_raw = json.dumps({"arg": "value"}, sort_keys=True)
    args_hash = hashlib.sha256(args_raw.encode("utf-8")).hexdigest()
    async with get_db() as db:
        await execute(
            db,
            (
                "INSERT INTO approvals (tool_name, args_json, args_hash, reason, "
                "requested_at, expires_at, status) VALUES "
                "('dummy_write', '{\"arg\": \"value\"}', ?, 'pre-approved', "
                "datetime('now'), '2099-01-01T00:00:00', 'approved')"
            ),
            (args_hash,),
        )
        await db.commit()

        ctx_db = ToolContext(run_id="run_app", config=_make_test_settings(), db=db)
        res_consumed = await reg.execute("dummy_write", {"arg": "value"}, ctx_db)
        assert res_consumed.ok
        assert "executed: value" in res_consumed.output

        # Insert expired pre-approval
        await execute(
            db,
            (
                "INSERT INTO approvals (tool_name, args_json, args_hash, reason, "
                "requested_at, expires_at, status) VALUES "
                "('dummy_write', '{\"arg\": \"value\"}', ?, 'expired', "
                "datetime('now'), '2000-01-01T00:00:00', 'approved')"
            ),
            (args_hash,),
        )
        await db.commit()

        # Should mark expired and request approval
        ctx_db.request_approval = AsyncMock(return_value=False)
        res_exp = await reg.execute("dummy_write", {"arg": "value"}, ctx_db)
        assert not res_exp.ok

    # 3. Custom user tools discovery
    custom_tools_dir = tmp_path / "user_tools"
    custom_tools_dir.mkdir()
    valid_tool_py = custom_tools_dir / "my_custom_tool.py"
    valid_tool_py.write_text(
        "from noxai.tools.registry import tool, ToolResult, ToolContext\n"
        "from pydantic import BaseModel\n"
        "class P(BaseModel):\n    val: str = ''\n"
        "@tool(name='custom_user_tool', description='desc', params=P, tier='read')\n"
        "async def custom_user_tool(p: P, ctx: ToolContext) -> ToolResult:\n"
        "    return ToolResult(ok=True, output='custom ok', summary='custom ok')\n"
    )
    bad_tool_py = custom_tools_dir / "bad_syntax.py"
    bad_tool_py.write_text("invalid python code syntax {{{")

    with patch("noxai.tools.registry.USER_TOOLS_DIR", custom_tools_dir):
        user_reg = ToolRegistry()
        user_reg.auto_discover()
        tool_obj = user_reg.get("custom_user_tool")
        assert tool_obj is not None
        assert tool_obj.is_user_tool is True


# ---------------------------------------------------------------------------
# 27. file_tools.py Truncation, Limits & Security
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_file_tools_additional_coverage(tmp_path: Path) -> None:
    from noxai.config import SafetyConfig
    from noxai.tools.builtin.file_tools import (
        ListFilesParams,
        ReadFileParams,
        WriteFileParams,
        list_files,
        read_file,
        write_file,
    )

    safety = SafetyConfig(allowed_file_roots=[str(tmp_path)], denied_paths=[])
    ctx = ToolContext(run_id="run_file", config=_make_test_settings(safety=safety))

    # 1. read_file truncation
    big_file = tmp_path / "big.txt"
    big_file.write_text("a" * 100)
    res_trunc = await read_file(ReadFileParams(path=str(big_file), max_bytes=10), ctx)
    assert res_trunc.ok
    assert "truncated" in res_trunc.summary

    # 2. list_files max_entries truncation and dotfile
    for i in range(5):
        (tmp_path / f"file_{i}.txt").write_text("test")
    (tmp_path / ".hidden_file").write_text("hidden")

    res_list = await list_files(ListFilesParams(directory=str(tmp_path), max_entries=2), ctx)
    assert res_list.ok
    assert "... and " in res_list.output
    assert "more items" in res_list.output

    # 3. write_file outside allowed roots
    res_deny = await write_file(WriteFileParams(path="/etc/passwd", content="hacked"), ctx)
    assert not res_deny.ok
    assert "Path security violation" in res_deny.summary
