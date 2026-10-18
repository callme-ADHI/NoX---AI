"""Tests for CLI commands and vector abstraction layer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from noxai.cli import app
from noxai.memory.vector import VectorIndex


class DummyVectorIndex:
    async def upsert(self, row_id: int, embedding: list[float]) -> None:
        pass

    async def query(self, embedding: list[float], k: int = 10) -> list[tuple[int, float]]:
        return [(1, 0.99)]

    async def delete(self, row_id: int) -> None:
        pass


def test_vector_index_protocol_runtime_checkable() -> None:
    """VectorIndex satisfies runtime_checkable protocol checks."""
    dummy = DummyVectorIndex()
    assert isinstance(dummy, VectorIndex)


def test_force_numpy_vectors_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting NOX_FORCE_NUMPY_VECTORS=1 forces numpy backend."""
    from noxai import db as db_mod

    monkeypatch.setenv("NOX_FORCE_NUMPY_VECTORS", "1")
    backend = db_mod._detect_vector_backend()
    assert backend == "numpy"


def test_doctor_reads_port_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """nx doctor reflects the port configured in settings."""
    runner = CliRunner()
    mock_cfg = MagicMock()
    mock_cfg.server.host = "127.0.0.1"
    mock_cfg.server.port = 8888

    with patch("noxai.config.get_config", return_value=mock_cfg):
        result = runner.invoke(app, ["doctor"])
        assert "Port 8888 free" in result.output


def test_test_mode_switch_requires_both_env_and_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify test-only switch requires BOTH NOX_TEST_MODE=1 and --test-db."""
    runner = CliRunner()
    fake_db = tmp_path / "fake.db"
    fake_db.touch()

    # Case 1: NOX_TEST_MODE=1 but no --test-db flag -> ignored
    monkeypatch.setenv("NOX_TEST_MODE", "1")
    with patch("noxai.api.app.create_app") as mock_create_app, patch("uvicorn.run"), patch(
        "noxai.lock.DaemonLock.acquire"
    ), patch("noxai.lock.DaemonLock.release"):
        result = runner.invoke(app, ["run"])
        assert "Warning" in result.output or "--test-db flag was not provided" in result.output
        mock_create_app.assert_called_once_with(manage_lock=False, test_mode=False, test_db=None)

    # Case 2: --test-db passed but NOX_TEST_MODE not set -> ignored
    monkeypatch.delenv("NOX_TEST_MODE", raising=False)
    with patch("noxai.api.app.create_app") as mock_create_app, patch("uvicorn.run"), patch(
        "noxai.lock.DaemonLock.acquire"
    ), patch("noxai.lock.DaemonLock.release"):
        result = runner.invoke(app, ["run", "--test-db", str(fake_db)])
        assert (
            "Warning" in result.output
            or "ignored because NOX_TEST_MODE is not set" in result.output
        )
        mock_create_app.assert_called_once_with(manage_lock=False, test_mode=False, test_db=None)

    # Case 3: BOTH NOX_TEST_MODE=1 and --test-db passed -> test mode activated!
    monkeypatch.setenv("NOX_TEST_MODE", "1")
    with patch("noxai.api.app.create_app") as mock_create_app, patch("uvicorn.run"), patch(
        "noxai.lock.DaemonLock.acquire"
    ), patch("noxai.lock.DaemonLock.release"):
        runner.invoke(app, ["run", "--test-db", str(fake_db)])
        mock_create_app.assert_called_once_with(
            manage_lock=False, test_mode=True, test_db=str(fake_db)
        )
