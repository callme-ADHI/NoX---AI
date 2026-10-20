"""Tests for repository hygiene, secret leak prevention, and network egress allowlist."""

from __future__ import annotations

import re
import socket
import subprocess
from pathlib import Path

import pytest

from noxai.agent.loop import AgentLoop
from noxai.config import ModelRoleConfig, ModelsConfig, Settings
from noxai.models.base import ChatChunk
from noxai.models.fake import FakeProvider
from noxai.models.router import ModelRouter

REPO_ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_PATH_PATTERN = re.compile(
    r"(\.venv|__pycache__|\.db($|-)|secrets\.env|(^|/)config\.toml$|\.env$|node_modules|ui/dist)"
)

SECRET_PATTERNS = [
    re.compile(r"nvapi-[A-Za-z0-9_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN (?:[A-Z0-9_-]+ )?PRIVATE KEY-----"),
]

# Explicit allowlist of known fake fixtures and in-code detection regexes
EXPLICIT_KEY_ALLOWLIST = {
    "nvapi-abcdef1234567890abcdef1234",
    "sk-abcdef1234567890abcdef1234",
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN PRIVATE KEY-----",
    "nvapi-secret1234567890abcdef1234",
    "nvapi-1234567890abcdef12345678",
    "nvapi-abc12345678901234567890xyz",
    "-----BEGIN (?:[A-Z0-9_-]+ )?PRIVATE KEY-----",
    "ghp_abcdefghijklmnopqrstuvwxyz123456",
    # Synthetic test fixture used in tests/test_providers_mock.py:366 to verify repr masking
    "nvapi-secret1234567890abcdefghijklmnopqrstuvwxyz",
    # Synthetic fixtures in tests/test_privacy_v2.py to verify sensitivity detection & redaction
    "nvapi-1234567890abcdefghijklmnopqrstuvwxyz",
    "nvapi-randomkey1234567890abcdef12345",
    "ghp_1234567890abcdefghijklmnopqrstuvwxyz12",
    # Synthetic test fixture used in tests/test_m10_integration.py and eval.py
    "-----BEGIN OPENSSH PRIVATE KEY-----",
}


def test_no_forbidden_files_tracked() -> None:
    """Ensure no forbidden directories, database files, or secrets files are tracked in git."""
    res = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    tracked_files = [line.strip() for line in res.stdout.splitlines() if line.strip()]

    violating_paths = [path for path in tracked_files if FORBIDDEN_PATH_PATTERN.search(path)]
    assert not violating_paths, f"Forbidden paths tracked in git: {violating_paths}"


def test_no_unmasked_real_keys_tracked() -> None:
    """Ensure no tracked file contains unmasked secrets matching credential regexes."""
    res = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    tracked_files = [line.strip() for line in res.stdout.splitlines() if line.strip()]

    violations = []
    for rel_path in tracked_files:
        # Ignore binary files or test_repo_hygiene itself where patterns/allowlist are defined
        if rel_path == "tests/test_repo_hygiene.py":
            continue
        file_path = REPO_ROOT / rel_path
        if not file_path.is_file():
            continue
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        for pat in SECRET_PATTERNS:
            matches = pat.findall(content)
            for m in matches:
                if m not in EXPLICIT_KEY_ALLOWLIST:
                    violations.append((rel_path, m))

    assert not violations, f"Potential real keys found in tracked files: {violations}"


@pytest.mark.asyncio
async def test_no_unallowed_outbound_sockets_during_fake_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assert that running an agent turn with FakeProvider opens zero external network sockets."""
    contacted_hosts: set[str] = set()

    orig_getaddrinfo = socket.getaddrinfo

    def patched_getaddrinfo(host: str, port: int | str, *args, **kwargs):
        contacted_hosts.add(str(host))
        return orig_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", patched_getaddrinfo)

    orig_connect = socket.socket.connect

    def patched_connect(self, address):
        if isinstance(address, tuple) and len(address) >= 1:
            contacted_hosts.add(str(address[0]))
        return orig_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", patched_connect)

    cfg = Settings(
        models=ModelsConfig(
            roles={
                "brain": ModelRoleConfig(provider="fake_local", model="fake-brain"),
                "embed": ModelRoleConfig(provider="fake_local", model="fake-embed"),
                "heavy": ModelRoleConfig(provider="fake_local", model="fake-heavy"),
            }
        )
    )

    fake_brain = FakeProvider(
        name="fake_local",
        is_local=True,
    )
    fake_brain.queue_response([ChatChunk(text="All local execution, no egress.")])

    router = ModelRouter(
        config=cfg,
        providers={"fake_local": fake_brain},
    )

    loop = AgentLoop(
        config=cfg,
        router=router,
        registry=None,
        event_bus=None,
    )

    result = await loop.run(
        user_message="hello test local only",
        run_id="run-egress-test",
        allow_cloud=False,
    )

    assert result.text == "All local execution, no egress."

    # Validate that no non-localhost socket was contacted
    ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
    disallowed = contacted_hosts - ALLOWED_HOSTS
    assert not disallowed, f"Unexpected network egress contacted hosts: {disallowed}"
