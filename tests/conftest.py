"""Root conftest for NoX test suite: isolates user paths and manages test markers."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

# Record real user paths before any redirection (for verification in test_path_isolation)
REAL_USER_HOME = Path(os.path.expanduser("~"))
REAL_CONFIG_NOX = REAL_USER_HOME / ".config" / "nox"
REAL_DATA_NOX = REAL_USER_HOME / ".local" / "share" / "nox"
REAL_STATE_NOX = REAL_USER_HOME / ".local" / "state" / "nox"

# Set up isolated session sandbox directory before any noxai imports occur
_SESSION_SANDBOX = tempfile.mkdtemp(prefix="nox-test-session-")
os.environ["NOX_TEST_MODE"] = "1"
os.environ["HOME"] = _SESSION_SANDBOX
os.environ["XDG_CONFIG_HOME"] = os.path.join(_SESSION_SANDBOX, ".config")
os.environ["XDG_DATA_HOME"] = os.path.join(_SESSION_SANDBOX, ".local", "share")
os.environ["XDG_STATE_HOME"] = os.path.join(_SESSION_SANDBOX, ".local", "state")

# Ensure sandbox directories exist
Path(os.environ["XDG_CONFIG_HOME"], "nox").mkdir(parents=True, exist_ok=True)
Path(os.environ["XDG_DATA_HOME"], "nox").mkdir(parents=True, exist_ok=True)
Path(os.environ["XDG_STATE_HOME"], "nox", "logs").mkdir(parents=True, exist_ok=True)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add CLI flag to run live external API tests."""
    parser.addoption(
        "--run-live",
        action="store_true",
        default=False,
        help="Run tests marked with @pytest.mark.live that connect to live external APIs",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "live: mark test as requiring live cloud API access (skipped by default unless --run-live)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip live-marked tests unless --run-live is given and NVIDIA_API_KEY is present."""
    run_live = config.getoption("--run-live", False)
    nvidia_key = os.environ.get("NVIDIA_API_KEY", "").strip()

    skip_no_flag = pytest.mark.skip(
        reason="live cloud test (skipped by default; pass --run-live to run)"
    )
    skip_no_key = pytest.mark.skip(
        reason="live cloud test skipped: NVIDIA_API_KEY is not configured in environment"
    )

    for item in items:
        if "live" in item.keywords:
            if not run_live:
                item.add_marker(skip_no_flag)
            elif not nvidia_key:
                item.add_marker(skip_no_key)


@pytest.fixture(autouse=True)
def isolate_user_paths_per_test(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure every individual test runs in a dedicated temporary isolation directory."""
    test_home = tmp_path / "home"
    test_config = test_home / ".config"
    test_data = test_home / ".local" / "share"
    test_state = test_home / ".local" / "state"

    test_config.mkdir(parents=True, exist_ok=True)
    test_data.mkdir(parents=True, exist_ok=True)
    test_state.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(test_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(test_config))
    monkeypatch.setenv("XDG_DATA_HOME", str(test_data))
    monkeypatch.setenv("XDG_STATE_HOME", str(test_state))
    monkeypatch.setenv("NOX_TEST_MODE", "1")


@pytest.fixture(scope="session", autouse=True)
def cleanup_session_sandbox() -> None:
    """Clean up the session sandbox after all tests complete."""
    yield
    if os.path.exists(_SESSION_SANDBOX):
        shutil.rmtree(_SESSION_SANDBOX, ignore_errors=True)
