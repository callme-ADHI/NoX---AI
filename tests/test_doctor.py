"""Unit tests for noxai.doctor health checks."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from rich.console import Console

from noxai import doctor


def test_check_python_version() -> None:
    with patch.object(sys, "version_info", (3, 10, 2)):
        c = doctor.check_python_version()
        assert c.status == "FAIL"

    with patch.object(sys, "version_info", (3, 12, 1)):
        c = doctor.check_python_version()
        assert c.status == "PASS"

    with patch.object(sys, "version_info", (3, 14, 0)):
        c = doctor.check_python_version()
        assert c.status == "WARN"


def test_check_ollama() -> None:
    # 1. Success
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"models": [{"name": "qwen2.5:14b"}]}
    with patch("httpx.get", return_value=mock_resp):
        c, models = doctor.check_ollama()
        assert c.status == "PASS"
        assert len(models) == 1

    # 2. ConnectError
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        c, models = doctor.check_ollama()
        assert c.status == "FAIL"
        assert "sudo systemctl start ollama" in c.hint

    # 3. Generic Exception
    with patch("httpx.get", side_effect=RuntimeError("boom")):
        c, models = doctor.check_ollama()
        assert c.status == "FAIL"
        assert "boom" in c.hint


def test_check_models() -> None:
    cfg = SimpleNamespace(
        models=SimpleNamespace(
            roles={
                "brain": SimpleNamespace(model="qwen2.5:14b"),
                "embed": SimpleNamespace(model="nomic-embed-text"),
                "vision": SimpleNamespace(model="gemma4:e4b"),
            }
        )
    )

    # All present
    models = [{"name": "qwen2.5:14b"}, {"name": "nomic-embed-text"}, {"name": "gemma4:e4b"}]
    checks = doctor.check_models(cfg, models)
    assert len(checks) == 3
    assert all(c.status == "PASS" for c in checks)

    # All missing
    checks_missing = doctor.check_models(cfg, [])
    assert checks_missing[0].status == "FAIL"  # brain
    assert checks_missing[1].status == "FAIL"  # embed
    assert checks_missing[2].status == "WARN"  # vision is WARN


def test_check_vector_backend() -> None:
    with patch("noxai.db._detect_vector_backend", return_value="numpy"):
        c = doctor.check_vector_backend()
        assert c.status == "PASS"
        assert "numpy" in c.name


def test_check_db_migrations(tmp_path: Path) -> None:
    # Non-existent DB
    c = doctor.check_db_migrations(tmp_path / "absent.db")
    assert c.status == "FAIL"

    db_path = tmp_path / "test.db"
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    # No schema_migrations table -> FAIL
    conn.execute("CREATE TABLE foo (id INT)")
    conn.commit()
    conn.close()
    c = doctor.check_db_migrations(db_path)
    assert c.status == "FAIL"

    # schema_migrations exists but 0 migrations
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE schema_migrations (id INT, filename TEXT)")
    conn.commit()
    conn.close()
    c = doctor.check_db_migrations(db_path)
    assert c.status == "FAIL"

    # schema_migrations has 1 migration (less than total)
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO schema_migrations VALUES (1, '001_init.sql')")
    conn.commit()
    conn.close()
    c = doctor.check_db_migrations(db_path)
    assert c.status == "FAIL"

    # schema_migrations has all migrations (e.g. 10)
    conn = sqlite3.connect(str(db_path))
    for i in range(2, 20):
        conn.execute("INSERT INTO schema_migrations VALUES (?, ?)", (i, f"00{i}_test.sql"))
    conn.commit()
    conn.close()
    c = doctor.check_db_migrations(db_path)
    assert c.status == "PASS"


def test_check_secrets_permissions(tmp_path: Path) -> None:
    # 1. Absent -> WARN
    p = tmp_path / "secrets.env"
    c = doctor.check_secrets_permissions(p)
    assert c.status == "WARN"

    # 2. Perms 0644 -> FAIL
    p.write_text("FOO=BAR")
    p.chmod(0o644)
    c = doctor.check_secrets_permissions(p)
    assert c.status == "FAIL"

    # 3. Perms 0600 -> PASS
    p.chmod(0o600)
    c = doctor.check_secrets_permissions(p)
    assert c.status == "PASS"


def test_check_nvidia_key_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "secrets.env"

    # 1. NOX_NVIDIA_API_KEY in env
    monkeypatch.setenv("NOX_NVIDIA_API_KEY", "nvapi-secret123")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    c, has = doctor.check_nvidia_key_source(p)
    assert has is True
    assert c.status == "PASS"
    assert "NOX_NVIDIA_API_KEY (env)" in c.name

    # 2. NVIDIA_API_KEY in env
    monkeypatch.delenv("NOX_NVIDIA_API_KEY", raising=False)
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-secret123")
    c, has = doctor.check_nvidia_key_source(p)
    assert has is True
    assert c.status == "PASS"
    assert "NVIDIA_API_KEY (env)" in c.name

    # 3. In secrets.env
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    p.write_text("NOX_NVIDIA_API_KEY=nvapi-abc\n")
    c, has = doctor.check_nvidia_key_source(p)
    assert has is True
    assert "NOX_NVIDIA_API_KEY (secrets.env)" in c.name

    p.write_text("NVIDIA_API_KEY=nvapi-xyz\n")
    c, has = doctor.check_nvidia_key_source(p)
    assert has is True
    assert "NVIDIA_API_KEY (secrets.env)" in c.name

    # 4. Neither
    p.unlink()
    c, has = doctor.check_nvidia_key_source(p)
    assert has is False
    assert c.status == "WARN"


def test_check_nvidia_models_endpoint() -> None:
    # 1. has_key is False
    c = doctor.check_nvidia_models_endpoint(None, False)
    assert c.status == "WARN"

    # 2. Key missing in cfg
    c = doctor.check_nvidia_models_endpoint(None, True)
    assert c.status == "WARN"

    cfg = SimpleNamespace(
        nvidia_api_key=SimpleNamespace(get_secret_value=lambda: "dummy-key"),
        models=SimpleNamespace(
            roles={"heavy": SimpleNamespace(model="nvidia/llama-3.3-nemotron-super-49b-v1.5")}
        ),
    )

    # 3. Success with heavy model found
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": [{"id": "nvidia/llama-3.3-nemotron-super-49b-v1.5"}]}
    with patch("httpx.get", return_value=mock_resp):
        c = doctor.check_nvidia_models_endpoint(cfg, True)
        assert c.status == "PASS"

    # 4. Success but heavy model not found
    mock_resp.json.return_value = {"data": [{"id": "other-model"}]}
    with patch("httpx.get", return_value=mock_resp):
        c = doctor.check_nvidia_models_endpoint(cfg, True)
        assert c.status == "WARN"

    # 5. Non-200
    mock_resp.status_code = 500
    with patch("httpx.get", return_value=mock_resp):
        c = doctor.check_nvidia_models_endpoint(cfg, True)
        assert c.status == "WARN"

    # 6. ConnectError
    with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
        c = doctor.check_nvidia_models_endpoint(cfg, True)
        assert c.status == "WARN"


def test_check_server_port() -> None:
    cfg = SimpleNamespace(server=SimpleNamespace(host="127.0.0.1", port=9999))

    # 1. ConnectionRefused -> Free (PASS)
    with patch("socket.socket.connect", side_effect=ConnectionRefusedError):
        c = doctor.check_server_port(cfg)
        assert c.status == "PASS"
        assert "Port 9999 free" in c.name

    # 2. Connects and /api/status is 200 -> Daemon running (PASS)
    with patch("socket.socket.connect", return_value=None):
        mock_resp = MagicMock(status_code=200)
        with patch("httpx.get", return_value=mock_resp):
            c = doctor.check_server_port(cfg)
            assert c.status == "PASS"
            assert "daemon running" in c.name

    # 3. Connects but /api/status fails -> In use (FAIL)
    with patch("socket.socket.connect", return_value=None):
        with patch("httpx.get", side_effect=httpx.HTTPError("failed")):
            c = doctor.check_server_port(cfg)
            assert c.status == "FAIL"
            assert "in use" in c.name


def test_check_binaries_and_paths(tmp_path: Path) -> None:
    with patch(
        "shutil.which", side_effect=lambda b: "/usr/bin/" + b if b == "notify-send" else None
    ):
        c = doctor.check_notify_send()
        assert c.status == "PASS"
        c = doctor.check_node_npm()
        assert c.status == "WARN"

    with patch("shutil.which", return_value="/usr/bin/firefox"):
        c = doctor.check_browser(None)
        assert c.status == "PASS"

    with patch("shutil.which", return_value=None):
        c = doctor.check_browser(None)
        assert c.status == "WARN"

    # UI build
    index_file = tmp_path / "index.html"
    assert doctor.check_ui_build(index_file).status == "WARN"
    index_file.touch()
    assert doctor.check_ui_build(index_file).status == "PASS"


def test_check_systemd_unit(tmp_path: Path) -> None:
    unit_path = tmp_path / "noxd.service"
    # 1. Not installed
    assert doctor.check_systemd_unit(unit_path).status == "WARN"

    # 2. ExecStart binary does not exist -> FAIL (catches D1)
    unit_path.write_text("[Service]\nExecStart=/nonexistent/path/noxd run\n")
    c = doctor.check_systemd_unit(unit_path)
    assert c.status == "FAIL"
    assert "does not exist" in c.hint

    # 3. ExecStart exists and unit enabled -> PASS
    fake_bin = tmp_path / "noxd"
    fake_bin.touch()
    unit_path.write_text(f"[Service]\nExecStart={fake_bin} run\n")
    mock_sub = MagicMock(stdout="enabled\n")
    with patch("subprocess.run", return_value=mock_sub):
        c = doctor.check_systemd_unit(unit_path)
        assert c.status == "PASS"

    # 4. ExecStart exists and unit disabled -> WARN
    mock_sub.stdout = "disabled\n"
    with patch("subprocess.run", return_value=mock_sub):
        c = doctor.check_systemd_unit(unit_path)
        assert c.status == "WARN"


def test_check_gate() -> None:
    # 1. token_only
    cfg = SimpleNamespace(gate=SimpleNamespace(mode="token_only"))
    c = doctor.check_gate(cfg)
    assert c.status == "PASS"

    # 2. command mode with missing binary -> FAIL
    cfg = SimpleNamespace(gate=SimpleNamespace(mode="command", command=["missing_gate_bin"]))
    with patch("shutil.which", return_value=None):
        c = doctor.check_gate(cfg)
        assert c.status == "FAIL"

    # 3. command mode with existing binary -> PASS
    with patch("shutil.which", return_value="/usr/bin/missing_gate_bin"):
        c = doctor.check_gate(cfg)
        assert c.status == "PASS"


def test_check_provider_topology() -> None:
    # 1. Invalid cfg
    assert doctor.check_provider_topology(None).status == "FAIL"

    # 2. Brain not local -> FAIL
    cfg = SimpleNamespace(
        models=SimpleNamespace(
            roles={
                "brain": SimpleNamespace(provider="nvidia"),
                "heavy": SimpleNamespace(provider="nvidia"),
            }
        )
    )
    assert doctor.check_provider_topology(cfg).status == "FAIL"

    # 3. Heavy is local -> FAIL
    cfg.models.roles["brain"].provider = "ollama"
    cfg.models.roles["heavy"].provider = "ollama"
    assert doctor.check_provider_topology(cfg).status == "FAIL"

    # 4. Brain local & Heavy non-local -> PASS
    cfg.models.roles["heavy"].provider = "nvidia"
    assert doctor.check_provider_topology(cfg).status == "PASS"


def test_run_doctor_cli_output(capsys: pytest.CaptureFixture[str]) -> None:
    console = Console()
    # Test JSON output mode when all checks pass
    mock_check = doctor.DoctorCheck("Mock check", "PASS", "")
    with patch("noxai.doctor.run_all_checks", return_value=[mock_check]):
        doctor.run_doctor(console=console, json_output=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["all_pass"] is True
        assert data["passed_count"] == 1

    # Test table output mode
    with patch("noxai.doctor.run_all_checks", return_value=[mock_check]):
        doctor.run_doctor(console=console, json_output=False)

    # Test failure exits with code 1
    mock_fail = doctor.DoctorCheck("Mock check", "FAIL", "fix it")
    with patch("noxai.doctor.run_all_checks", return_value=[mock_fail]):
        with pytest.raises(SystemExit) as exc:
            doctor.run_doctor(console=console, json_output=True)
        assert exc.value.code == 1

    # Test warning branch in table mode
    mock_warn = doctor.DoctorCheck("Mock check", "WARN", "heads up")
    with patch("noxai.doctor.run_all_checks", return_value=[mock_warn]):
        doctor.run_doctor(console=console, json_output=False)


def test_check_lock_state(tmp_path: Path) -> None:
    # Free
    with patch("noxai.doctor.Path.home", return_value=tmp_path):
        c = doctor.check_lock_state()
        assert c.status == "PASS"


def test_check_compositor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "Hyprland")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    c = doctor.check_compositor()
    assert c.status == "PASS"
    assert "Hyprland" in c.name
    assert "wayland" in c.name


def test_check_config_loads() -> None:
    c, cfg = doctor.check_config_loads()
    assert c.status == "PASS"
    assert cfg is not None

    with patch("noxai.config.get_config", side_effect=RuntimeError("bad config")):
        c, cfg = doctor.check_config_loads()
        assert c.status == "FAIL"
        assert cfg is None


def test_run_all_checks() -> None:
    checks = doctor.run_all_checks()
    assert len(checks) >= 15
    assert all(isinstance(c, doctor.DoctorCheck) for c in checks)
