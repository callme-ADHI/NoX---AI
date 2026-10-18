"""Tests for noxai.config — configuration loading and validation."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

# We need to patch the paths used by the config module
# to avoid polluting the real ~/.config/nox directory.


@pytest.fixture(autouse=True)
def isolate_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator:
    """Redirect all config paths to a temporary directory.

    Patches CONFIG_DIR, CONFIG_FILE, SECRETS_FILE and _EXAMPLE_CONFIG
    in the noxai.config module, and clears the lru_cache between tests.
    """
    cfg_dir = tmp_path / "config" / "nox"
    cfg_dir.mkdir(parents=True)
    cfg_file = cfg_dir / "config.toml"
    secrets_file = cfg_dir / "secrets.env"
    example_file = Path(__file__).parent.parent / "config.example.toml"

    import noxai.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config_mod, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(config_mod, "SECRETS_FILE", secrets_file)
    monkeypatch.setattr(config_mod, "_EXAMPLE_CONFIG", example_file)

    # Clear the lru_cache so each test starts fresh
    config_mod.get_config.cache_clear()
    yield
    config_mod.get_config.cache_clear()


def _write_valid_config(cfg_file: Path) -> None:
    """Write a minimal valid config.toml."""
    cfg_file.write_text(
        """
[server]
host = "127.0.0.1"
port = 7777

[timezone]
name = "Asia/Kolkata"

[models.ollama]
base_url = "http://127.0.0.1:11434"

[models.roles]
brain  = { provider = "ollama", model = "qwen2.5:14b" }
embed  = { provider = "ollama", model = "nomic-embed-text" }
vision = { provider = "ollama", model = "gemma4:e4b" }
heavy  = { provider = "nvidia", model = "nvidia/llama-3.3-nemotron-super-49b-v1.5" }
"""
    )


class TestValidConfig:
    def test_valid_config_loads(self, tmp_path: Path) -> None:
        """A correctly structured config file loads without error."""
        import noxai.config as config_mod

        _write_valid_config(config_mod.CONFIG_FILE)
        cfg = config_mod.load_config()

        assert cfg.server.host == "127.0.0.1"
        assert cfg.server.port == 7777
        assert cfg.timezone.name == "Asia/Kolkata"
        assert "brain" in cfg.models.roles
        assert "embed" in cfg.models.roles

    def test_default_values_populated(self, tmp_path: Path) -> None:
        """Omitted optional keys get their defaults."""
        import noxai.config as config_mod

        _write_valid_config(config_mod.CONFIG_FILE)
        cfg = config_mod.load_config()

        assert cfg.agent.max_steps_per_run == 12
        assert cfg.memory.recall_top_k == 6
        assert cfg.gate.mode == "token_only"

    def test_first_run_creates_config(self) -> None:
        """If config.toml is missing, it is created from the example file."""
        import noxai.config as config_mod

        assert not config_mod.CONFIG_FILE.exists()
        cfg = config_mod.load_config()

        assert config_mod.CONFIG_FILE.exists()
        assert cfg.server.host == "127.0.0.1"


class TestUnknownKeys:
    def test_unknown_top_level_key_raises(self) -> None:
        """An unknown top-level key must produce a clear error."""
        import noxai.config as config_mod

        config_mod.CONFIG_FILE.write_text(
            """
[server]
host = "127.0.0.1"
port = 7777

[models.ollama]
base_url = "http://127.0.0.1:11434"

[models.roles]
brain  = { provider = "ollama", model = "qwen2.5:14b" }
embed  = { provider = "ollama", model = "nomic-embed-text" }

[unknown_section]
foo = "bar"
"""
        )
        with pytest.raises(ValueError, match="unknown_section"):
            config_mod.load_config()

    def test_unknown_nested_key_raises(self) -> None:
        """An unknown key nested inside a known section raises an error."""
        import noxai.config as config_mod

        config_mod.CONFIG_FILE.write_text(
            """
[server]
host = "127.0.0.1"
port = 7777
mystery_option = true

[models.ollama]
base_url = "http://127.0.0.1:11434"

[models.roles]
brain  = { provider = "ollama", model = "qwen2.5:14b" }
embed  = { provider = "ollama", model = "nomic-embed-text" }
"""
        )
        with pytest.raises(ValueError, match="mystery_option"):
            config_mod.load_config()


class TestHostValidation:
    def test_non_loopback_host_rejected(self) -> None:
        """Binding to 0.0.0.0 must be rejected."""
        import noxai.config as config_mod

        config_mod.CONFIG_FILE.write_text(
            """
[server]
host = "0.0.0.0"
port = 7777

[models.ollama]
base_url = "http://127.0.0.1:11434"

[models.roles]
brain  = { provider = "ollama", model = "qwen2.5:14b" }
embed  = { provider = "ollama", model = "nomic-embed-text" }
"""
        )
        with pytest.raises(ValueError, match=r"127\.0\.0\.1"):
            config_mod.load_config()


class TestSecrets:
    def test_secrets_loaded_when_present(self, tmp_path: Path) -> None:
        """NVIDIA_API_KEY from secrets.env is injected into settings."""
        import noxai.config as config_mod

        _write_valid_config(config_mod.CONFIG_FILE)
        config_mod.SECRETS_FILE.write_text("NVIDIA_API_KEY=nvapi-test123\n")
        config_mod.SECRETS_FILE.chmod(0o600)

        cfg = config_mod.load_config()
        assert cfg.nvidia_api_key is not None
        assert cfg.nvidia_api_key.get_secret_value() == "nvapi-test123"

    def test_secrets_alias_nox_nvidia_api_key(self, tmp_path: Path) -> None:
        """NOX_NVIDIA_API_KEY is preferred over NVIDIA_API_KEY."""
        import noxai.config as config_mod

        _write_valid_config(config_mod.CONFIG_FILE)
        config_mod.SECRETS_FILE.write_text(
            "NVIDIA_API_KEY=nvapi-fallback\nNOX_NVIDIA_API_KEY=nvapi-preferred\n"
        )
        config_mod.SECRETS_FILE.chmod(0o600)

        cfg = config_mod.load_config()
        assert cfg.nvidia_api_key is not None
        assert cfg.nvidia_api_key.get_secret_value() == "nvapi-preferred"

    def test_missing_secrets_file_ok(self) -> None:
        """Missing secrets file is fine — NVIDIA just stays disabled."""
        import noxai.config as config_mod

        _write_valid_config(config_mod.CONFIG_FILE)
        # No secrets file
        cfg = config_mod.load_config()
        assert cfg.nvidia_api_key is None

    def test_secrets_file_wrong_permissions_rejected(self) -> None:
        """Secrets file with permissions looser than 0600 must be rejected."""
        import noxai.config as config_mod

        _write_valid_config(config_mod.CONFIG_FILE)
        config_mod.SECRETS_FILE.write_text("NVIDIA_API_KEY=nvapi-test\n")
        config_mod.SECRETS_FILE.chmod(0o644)  # too permissive

        with pytest.raises(PermissionError, match="0o644"):
            config_mod.load_config()

    def test_secrets_file_with_comments_and_blanks(self) -> None:
        """Comments (#) and blank lines in secrets.env are ignored."""
        import noxai.config as config_mod

        _write_valid_config(config_mod.CONFIG_FILE)
        config_mod.SECRETS_FILE.write_text("# This is a comment\n\nNVIDIA_API_KEY=nvapi-abc\n")
        config_mod.SECRETS_FILE.chmod(0o600)

        cfg = config_mod.load_config()
        assert cfg.nvidia_api_key is not None
        assert cfg.nvidia_api_key.get_secret_value() == "nvapi-abc"

    def test_malformed_secrets_line_raises(self) -> None:
        """A non-KEY=VALUE line that is not a comment must raise."""
        import noxai.config as config_mod

        _write_valid_config(config_mod.CONFIG_FILE)
        config_mod.SECRETS_FILE.write_text("this is not valid\n")
        config_mod.SECRETS_FILE.chmod(0o600)

        with pytest.raises(ValueError, match="KEY=VALUE"):
            config_mod.load_config()


class TestMissingRequiredRoles:
    def test_missing_brain_role_raises(self) -> None:
        """Missing 'brain' role in models.roles must raise."""
        import noxai.config as config_mod

        config_mod.CONFIG_FILE.write_text(
            """
[server]
host = "127.0.0.1"
port = 7777

[models.ollama]
base_url = "http://127.0.0.1:11434"

[models.roles]
embed = { provider = "ollama", model = "nomic-embed-text" }
"""
        )
        with pytest.raises(ValueError, match="brain"):
            config_mod.load_config()
