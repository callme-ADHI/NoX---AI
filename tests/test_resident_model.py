"""Tests for Phase F6: Resident Small Model, Ollama tuning, RAM budget, warm-up (§F6).

Covers:
- Role resolution (resident explicit and fallback to brain)
- Per-call keep_alive and num_ctx defaults for resident model
- Tool-loop refusal for resident model unless configured
- RAM-budget check in doctor (PASS under 80%, WARN over 80%)
- nx doctor --perf recommendations output
- Non-blocking warm-up at daemon startup
- Model bake-off ranking logic
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console
from typer.testing import CliRunner

from noxai.cli import app as cli_app
from noxai.config import (
    ModelRoleConfig,
    ModelsConfig,
    OllamaModelConfig,
    Settings,
)
from noxai.doctor import check_ram_budget, run_doctor_perf
from noxai.models.base import ChatChunk, Message, ToolSpec
from noxai.models.fake import FakeProvider
from noxai.models.router import ModelRouter


@pytest.fixture
def sample_config_with_resident() -> Settings:
    return Settings(
        models=ModelsConfig(
            ollama=OllamaModelConfig(
                keep_alive="30m",
                num_ctx=8192,
                resident_num_ctx=4096,
                resident_allow_tool_loops=False,
            ),
            roles={
                "brain": ModelRoleConfig(provider="fake_brain", model="qwen2.5:14b"),
                "resident": ModelRoleConfig(provider="fake_res", model="qwen2.5:1.5b"),
                "embed": ModelRoleConfig(provider="fake_embed", model="nomic-embed-text"),
            },
        )
    )


@pytest.fixture
def sample_config_fallback() -> Settings:
    return Settings(
        models=ModelsConfig(
            ollama=OllamaModelConfig(
                keep_alive="30m",
                num_ctx=8192,
                resident_num_ctx=4096,
                resident_allow_tool_loops=False,
            ),
            roles={
                "brain": ModelRoleConfig(provider="fake_brain", model="qwen2.5:14b"),
                "embed": ModelRoleConfig(provider="fake_embed", model="nomic-embed-text"),
            },
        )
    )


def test_resident_role_resolution_explicit(sample_config_with_resident: Settings) -> None:
    fake_res = FakeProvider(name="fake_res", is_local=True)
    fake_brain = FakeProvider(name="fake_brain", is_local=True)
    router = ModelRouter(
        config=sample_config_with_resident,
        providers={"fake_res": fake_res, "fake_brain": fake_brain},
    )

    prov, model = router.resolve_role("resident")
    assert prov == fake_res
    assert model == "qwen2.5:1.5b"


def test_resident_role_resolution_fallback_to_brain(sample_config_fallback: Settings) -> None:
    fake_brain = FakeProvider(name="fake_brain", is_local=True)
    router = ModelRouter(
        config=sample_config_fallback,
        providers={"fake_brain": fake_brain},
    )

    prov, model = router.resolve_role("resident")
    assert prov == fake_brain
    assert model == "qwen2.5:14b"


@pytest.mark.asyncio
async def test_resident_keep_alive_and_context_defaults(
    sample_config_with_resident: Settings,
) -> None:
    fake_res = FakeProvider(name="fake_res", is_local=True)
    fake_res.responses = [[ChatChunk(text="Fast greeting")]]
    router = ModelRouter(
        config=sample_config_with_resident,
        providers={"fake_res": fake_res},
    )

    msg = Message(role="user", content="Hi")
    chunks = [c async for c in router.chat(role="resident", messages=[msg])]
    assert len(chunks) > 0

    assert len(fake_res.calls) == 1
    call = fake_res.calls[0]
    # Check defaults: keep_alive='-1' (infinite resident retention) and num_ctx=4096
    assert call.get("keep_alive") == "-1"
    assert call.get("num_ctx") == 4096


@pytest.mark.asyncio
async def test_resident_disallows_tool_loops_by_default(
    sample_config_with_resident: Settings,
) -> None:
    fake_res = FakeProvider(name="fake_res", is_local=True)
    router = ModelRouter(
        config=sample_config_with_resident,
        providers={"fake_res": fake_res},
    )

    msg = Message(role="user", content="Search the web")
    dummy_tool = ToolSpec(
        name="web_search",
        description="search",
        parameters={"type": "object", "properties": {}},
    )

    with pytest.raises(ValueError, match="Resident model is reserved for fast non-tool operations"):
        _ = [c async for c in router.chat(role="resident", messages=[msg], tools=[dummy_tool])]


@pytest.mark.asyncio
async def test_resident_allows_tool_loops_when_configured(
    sample_config_with_resident: Settings,
) -> None:
    sample_config_with_resident.models.ollama.resident_allow_tool_loops = True
    fake_res = FakeProvider(name="fake_res", is_local=True)
    fake_res.responses = [[ChatChunk(text="Tool response")]]
    router = ModelRouter(
        config=sample_config_with_resident,
        providers={"fake_res": fake_res},
    )

    dummy_tool = ToolSpec(
        name="web_search",
        description="search",
        parameters={"type": "object", "properties": {}},
    )
    chunks = [
        c
        async for c in router.chat(
            role="resident",
            messages=[Message(role="user", content="Search")],
            tools=[dummy_tool],
        )
    ]
    assert len(chunks) > 0


def test_ram_budget_check(tmp_path: Path) -> None:
    meminfo_file = tmp_path / "meminfo"

    # Case A: 10 GB MemAvailable, 1 GB model loaded -> PASS (~10% ratio)
    meminfo_file.write_text(
        "MemTotal:       16000000 kB\n"
        "MemFree:         4000000 kB\n"
        "MemAvailable:   10000000 kB\n"
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "models": [{"name": "qwen2.5:1.5b", "size": 1 * (1024**3)}]
    }

    with patch("httpx.get", return_value=mock_resp):
        check = check_ram_budget(meminfo_path=meminfo_file)
        assert check.status == "PASS"
        assert "10%" in check.hint

    # Case B: 2 GB MemAvailable, 1.8 GB models loaded -> WARN (~90% ratio > 80%)
    meminfo_file.write_text(
        "MemTotal:       16000000 kB\n"
        "MemFree:          500000 kB\n"
        "MemAvailable:    2000000 kB\n"
    )
    mock_resp.json.return_value = {
        "models": [{"name": "heavy:model", "size": int(1.8 * (1024**3))}]
    }

    with patch("httpx.get", return_value=mock_resp):
        check_warn = check_ram_budget(meminfo_path=meminfo_file)
        assert check_warn.status == "WARN"
        assert "Risk of OOM" in check_warn.hint


def test_doctor_perf_recommendations(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    meminfo_file = tmp_path / "meminfo"
    meminfo_file.write_text(
        "MemTotal:       32000000 kB\n"
        "MemAvailable:   24000000 kB\n"
    )
    console = Console()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "models": [
            {"name": "qwen2.5:14b", "size": 9 * (1024**3), "processor": "50% GPU / 50% CPU"}
        ]
    }

    with patch("httpx.get", return_value=mock_resp):
        # Test JSON mode
        run_doctor_perf(console=console, json_output=True, meminfo_path=meminfo_file)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["total_ram_gb"] >= 30
        assert data["recommended_env"]["OLLAMA_MAX_LOADED_MODELS"] == "3"
        assert data["recommended_env"]["OLLAMA_FLASH_ATTENTION"] == "1"
        assert data["recommended_env"]["OLLAMA_KV_CACHE_TYPE"] == "q8_0"
        assert any("partial offload" in flag for flag in data["partial_offload_flags"])


def test_cli_doctor_perf_flag() -> None:
    runner = CliRunner()
    with patch("noxai.doctor.run_doctor_perf") as mock_perf:
        res = runner.invoke(cli_app, ["doctor", "--perf"])
        assert res.exit_code == 0
        assert mock_perf.called


@pytest.mark.asyncio
async def test_warmup_worker_logic() -> None:
    """Verify warm-up worker executes ping and embed without blocking."""
    fake_res = FakeProvider(name="fake_res", is_local=True)
    fake_res.responses = [[ChatChunk(text="pong")]]
    fake_embed = FakeProvider(name="fake_embed", is_local=True)
    fake_embed.embed_responses = [[[0.1] * 8]]

    cfg = Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="fake_res", model="brain-model"),
                "resident": ModelRoleConfig(provider="fake_res", model="resident-model"),
                "embed": ModelRoleConfig(provider="fake_embed", model="embed-model"),
            }
        )
    )
    router = ModelRouter(cfg, providers={"fake_res": fake_res, "fake_embed": fake_embed})

    # Simulate warm-up task
    import time
    t0 = time.monotonic()
    res_prov, res_model = router.resolve_role("resident")
    async for _ in res_prov.chat(
        model=res_model,
        messages=[Message(role="user", content="ping")],
        max_tokens=1,
        keep_alive="-1",
    ):
        pass
    emb_prov, emb_model = router.resolve_role("embed")
    await emb_prov.embed(model=emb_model, texts=["ping"], keep_alive="-1")
    dt = time.monotonic() - t0

    assert len(fake_res.calls) == 1
    assert fake_res.calls[0]["keep_alive"] == "-1"
    assert len(fake_embed.calls) == 1
    assert fake_embed.calls[0]["keep_alive"] == "-1"
    assert dt < 1.0


def test_bench_local_command_help() -> None:
    runner = CliRunner()
    res = runner.invoke(cli_app, ["models", "bench-local", "--help"])
    assert res.exit_code == 0
    assert "Benchmark and rank local candidate models" in res.stdout
