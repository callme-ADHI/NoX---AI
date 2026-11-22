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
    # Synthetic test fixture used in tests/test_security_foundation.py:127
    "ghp_123456789012345678901234567890123456",
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
        # Ignore binary files, reports, or test fixtures where fake test patterns are defined
        if (
            rel_path == "tests/test_repo_hygiene.py"
            or rel_path.startswith("docs/reports/")
            or rel_path in ("tests/test_tracing.py", "tests/test_planned_jobs_steps.py")
        ):
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


def test_no_credential_fill_in_codebase() -> None:
    """Ensure forbidden strings like credential fill or token extraction do not appear."""
    forbidden = ["credential " + "fill", "git credential " + "fill"]
    found = []
    for ext in ["*.py", "*.sh", "*.js", "*.ts", "*.tsx"]:
        for p in REPO_ROOT.glob(f"src/**/{ext}"):
            text = p.read_text(errors="ignore")
            for f in forbidden:
                if f in text:
                    found.append((str(p.relative_to(REPO_ROOT)), f))
        for p in REPO_ROOT.glob(f"tests/**/{ext}"):
            if p.name == "test_repo_hygiene.py":
                continue
            text = p.read_text(errors="ignore")
            for f in forbidden:
                if f in text:
                    found.append((str(p.relative_to(REPO_ROOT)), f))
        for p in REPO_ROOT.glob(f"scripts/**/{ext}"):
            if p.name == "verify_all.py":
                continue
            text = p.read_text(errors="ignore")
            for f in forbidden:
                if f in text:
                    found.append((str(p.relative_to(REPO_ROOT)), f))

    assert not found, f"Forbidden credential extraction patterns found in codebase: {found}"


def test_no_github_token_in_secret_store() -> None:
    """Ensure github:token is not stored in SecretStore by default or extracted."""
    from noxai.security.secrets import SecretStore

    assert (
        SecretStore.get("github:token") is None
    ), "github:token must not be automatically stored in SecretStore."


@pytest.mark.asyncio
async def test_honest_connector_and_channel_states_when_unconfigured() -> None:
    """Verify unconfigured connectors and channels report NOT_CONFIGURED, never HEALTHY."""
    from noxai.channels.discord import DiscordChannel
    from noxai.channels.slack import SlackChannel
    from noxai.channels.telegram import TelegramChannel
    from noxai.connectors.calendar import CalendarConnector
    from noxai.connectors.feeds import FeedsConnector
    from noxai.connectors.gmail import GmailConnector
    from noxai.connectors.imap import IMAPConnector

    g_health = await GmailConnector("unconf_gmail").health()
    assert g_health["status"] == "NOT_CONFIGURED"

    i_health = await IMAPConnector("unconf_imap").health()
    assert i_health["status"] == "NOT_CONFIGURED"

    c_health = await CalendarConnector("unconf_cal").health()
    assert c_health["status"] == "NOT_CONFIGURED"

    f_health = await FeedsConnector("unconf_feeds").health()
    assert f_health["status"] == "NOT_CONFIGURED"

    t_health = await TelegramChannel("unconf_tg").health()
    assert t_health["status"] == "NOT_CONFIGURED"

    s_health = await SlackChannel("unconf_slack").health()
    assert s_health["status"] == "NOT_CONFIGURED"

    d_health = await DiscordChannel("unconf_discord").health()
    assert d_health["status"] == "NOT_CONFIGURED"


