"""Test asserting that tests and operations never touch real user paths (Ground Rule 5)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from noxai.config import get_config
from noxai.logging_setup import setup_logging
from tests.conftest import REAL_CONFIG_NOX, REAL_DATA_NOX, REAL_STATE_NOX


def test_no_real_path_writes_during_test(tmp_path: Path) -> None:
    """Verify that configuration, database, and logging use isolated temporary paths."""
    # Ensure current environment points to temp sandbox, not real user paths
    xdg_config = os.environ.get("XDG_CONFIG_HOME", "")
    xdg_data = os.environ.get("XDG_DATA_HOME", "")
    xdg_state = os.environ.get("XDG_STATE_HOME", "")

    for p in (xdg_config, xdg_data, xdg_state):
        assert "tmp" in p or "nox-test" in p or "pytest" in p

    # Record snapshot of real directories if they exist
    real_paths = [REAL_CONFIG_NOX, REAL_DATA_NOX, REAL_STATE_NOX]
    before_snapshots = {}
    for real_p in real_paths:
        if real_p.exists():
            for f in real_p.rglob("*"):
                if f.is_file():
                    before_snapshots[str(f)] = f.stat().st_mtime

    # Perform standard operations that previously leaked: logging setup, config read
    setup_logging(level="DEBUG", log_dir=tmp_path / "logs")
    log = logging.getLogger("test_isolation")
    log.info("Testing path isolation")

    cfg = get_config()
    assert cfg is not None

    # Verify no file under real paths was modified
    for real_p in real_paths:
        if real_p.exists():
            for f in real_p.rglob("*"):
                if f.is_file():
                    path_str = str(f)
                    if path_str in before_snapshots:
                        assert f.stat().st_mtime == before_snapshots[path_str], (
                            f"Real user file was modified during test: {f}"
                        )
                    else:
                        raise AssertionError(f"New file created under real user path: {f}")
