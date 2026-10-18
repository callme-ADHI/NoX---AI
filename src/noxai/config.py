"""Configuration loading and validation for NoX.

Loads ``~/.config/nox/config.toml`` via stdlib ``tomllib``, validates it
with Pydantic v2, and exposes a cached ``get_config()`` accessor.

Secrets are loaded from ``~/.config/nox/secrets.env`` (must be mode 0600).
The file format is simple KEY=VALUE lines; no shell expansion is performed.
"""

from __future__ import annotations

import re
import stat
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError, field_validator, model_validator

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CONFIG_DIR = Path("~/.config/nox").expanduser()
CONFIG_FILE = CONFIG_DIR / "config.toml"
SECRETS_FILE = CONFIG_DIR / "secrets.env"

_EXAMPLE_CONFIG = Path(__file__).parent.parent.parent / "config.example.toml"


# ---------------------------------------------------------------------------
# Base model — all config sections reject unknown keys
# ---------------------------------------------------------------------------


class _StrictModel(BaseModel):
    """Base for all config section models.

    Rejects extra keys so that typos in config.toml surface as clear errors
    rather than being silently ignored.
    """

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Config section models
# ---------------------------------------------------------------------------


class ServerConfig(_StrictModel):
    """HTTP server settings."""

    host: str = "127.0.0.1"
    port: int = 7777

    @field_validator("host")
    @classmethod
    def host_must_be_loopback(cls, v: str) -> str:
        """Enforce that the server only binds to localhost in Phase 1."""
        if v != "127.0.0.1":
            raise ValueError(
                f"server.host must be '127.0.0.1' in Phase 1, got '{v}'. "
                "Binding to external interfaces is not supported."
            )
        return v


class TimezoneConfig(_StrictModel):
    """Timezone settings."""

    name: str = "Asia/Kolkata"


class LoggingConfig(_StrictModel):
    """Logging configuration."""

    level: str = "INFO"

    @field_validator("level")
    @classmethod
    def level_must_be_valid(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v.upper() not in valid:
            raise ValueError(f"logging.level must be one of {valid}, got '{v}'")
        return v.upper()


class ModelRoleConfig(_StrictModel):
    """Binding of a named role to a provider and model."""

    provider: str
    model: str


class OllamaModelConfig(_StrictModel):
    """Ollama-specific model settings."""

    base_url: str = "http://127.0.0.1:11434"
    keep_alive: str = "30m"
    num_ctx: int = 8192


class NvidiaModelConfig(_StrictModel):
    """NVIDIA API settings."""

    enabled: bool = True
    base_url: str = "https://integrate.api.nvidia.com/v1"
    requests_per_minute: int = 30
    max_retries: int = 4


class ModelsConfig(_StrictModel):
    """All model-related configuration."""

    ollama: OllamaModelConfig = OllamaModelConfig()
    nvidia: NvidiaModelConfig = NvidiaModelConfig()
    roles: dict[str, ModelRoleConfig] = {}
    # Note: roles validation is done in Settings.validate_models_roles so that
    # the default empty dict used by Settings' field default does not trigger
    # the validator during class body execution.


class AgentConfig(_StrictModel):
    """Agent loop parameters."""

    max_steps_per_run: int = 12
    max_subtasks: int = 8
    max_depth: int = 2
    worker_concurrency: int = 2
    run_timeout_seconds: int = 900
    tool_timeout_seconds: int = 60


class MemoryConfig(_StrictModel):
    """Memory system parameters."""

    recall_top_k: int = 6
    recall_token_budget: int = 800
    episode_archive_days: int = 90


class SafetyConfig(_StrictModel):
    """Safety settings — these cannot be changed via chat or the UI."""

    allowed_file_roots: list[str] = ["~"]
    denied_paths: list[str] = [
        "~/.ssh",
        "~/.gnupg",
        "~/.config/nox/secrets.env",
        "~/.mozilla",
        "~/.config/chromium",
        "~/.config/google-chrome",
    ]
    approval_timeout_seconds: int = 300


class GateConfig(_StrictModel):
    """Authentication gate configuration."""

    mode: str = "token_only"
    command: list[str] = []

    @field_validator("mode")
    @classmethod
    def mode_must_be_valid(cls, v: str) -> str:
        valid = {"off", "token_only", "command"}
        if v not in valid:
            raise ValueError(f"gate.mode must be one of {sorted(valid)}, got '{v}'")
        return v


class UIConfig(_StrictModel):
    """UI launcher configuration."""

    launcher_cmd: list[str] = ["chromium", "--app={url}"]


# ---------------------------------------------------------------------------
# Root config model
# ---------------------------------------------------------------------------


class Settings(BaseModel):
    """Root configuration object. Unknown keys are rejected."""

    model_config = {"extra": "forbid"}

    server: ServerConfig = ServerConfig()
    timezone: TimezoneConfig = TimezoneConfig()
    logging: LoggingConfig = LoggingConfig()
    models: ModelsConfig = ModelsConfig()
    agent: AgentConfig = AgentConfig()
    memory: MemoryConfig = MemoryConfig()
    safety: SafetyConfig = SafetyConfig()
    gate: GateConfig = GateConfig()
    ui: UIConfig = UIConfig()

    # Secrets (loaded separately, not from TOML)
    nvidia_api_key: str | None = None

    @model_validator(mode="after")
    def _validate_model_roles(self) -> Settings:
        """Validate that required model roles are present."""
        required = {"brain", "embed"}
        missing = required - set(self.models.roles)
        if missing:
            raise ValueError(
                f"models.roles is missing required role(s): {sorted(missing)}. "
                "Add them to config.toml."
            )
        return self


# ---------------------------------------------------------------------------
# Secrets loader
# ---------------------------------------------------------------------------

_SECRET_PATTERN = re.compile(r"^([A-Z_][A-Z0-9_]*)=(.*)$")


def _load_secrets() -> dict[str, str]:
    """Load KEY=VALUE pairs from the secrets file.

    Returns an empty dict if the file does not exist.
    Raises ``PermissionError`` if the file has permissions looser than 0600.
    Never logs secret values.
    """
    if not SECRETS_FILE.exists():
        return {}

    # Check file permissions — must be exactly 0o600 (owner r/w, nothing else)
    file_stat = SECRETS_FILE.stat()
    mode = stat.S_IMODE(file_stat.st_mode)
    if mode & 0o177:  # any bits beyond owner rw (0o600)
        raise PermissionError(
            f"Secrets file {SECRETS_FILE} has unsafe permissions "
            f"({oct(mode)}). Fix with: chmod 600 {SECRETS_FILE}"
        )

    secrets: dict[str, str] = {}
    for line_no, line in enumerate(SECRETS_FILE.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _SECRET_PATTERN.match(line)
        if not match:
            raise ValueError(
                f"Secrets file {SECRETS_FILE} line {line_no}: "
                f"expected KEY=VALUE format, got: {line!r}"
            )
        secrets[match.group(1)] = match.group(2)
    return secrets


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def _ensure_config_exists() -> None:
    """Create config directory and copy example config if not present."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        if _EXAMPLE_CONFIG.exists():
            CONFIG_FILE.write_text(_EXAMPLE_CONFIG.read_text())
        else:
            # Minimal bootstrap config
            CONFIG_FILE.write_text(
                '[server]\nhost = "127.0.0.1"\nport = 7777\n\n'
                '[timezone]\nname = "Asia/Kolkata"\n\n'
                '[models.ollama]\nbase_url = "http://127.0.0.1:11434"\n\n'
                "[models.roles]\n"
                'brain  = { provider = "ollama", model = "qwen2.5:14b" }\n'
                'embed  = { provider = "ollama", model = "nomic-embed-text" }\n'
                'vision = { provider = "ollama", model = "gemma4:e4b" }\n'
                'heavy  = { provider = "nvidia",'
                ' model = "nvidia/llama-3.3-nemotron-super-49b-v1.5" }\n'
            )
        print(f"Created default config at {CONFIG_FILE}")


def _load_toml() -> dict[str, Any]:
    """Read and parse the TOML config file."""
    _ensure_config_exists()
    with open(CONFIG_FILE, "rb") as f:
        return tomllib.load(f)


def load_config() -> Settings:
    """Load, validate and return the full configuration.

    On validation failure, raises a ``ValueError`` with a human-readable
    description of what is wrong and how to fix it.
    """
    raw = _load_toml()

    # Load secrets
    secrets = _load_secrets()

    # Inject secrets into raw dict so Pydantic can validate them
    # (they are not written back to disk)
    if "NVIDIA_API_KEY" in secrets:
        raw["nvidia_api_key"] = secrets["NVIDIA_API_KEY"]

    try:
        settings = Settings(**raw)
    except ValidationError as exc:
        lines = [f"Configuration error in {CONFIG_FILE}:"]
        for err in exc.errors():
            loc = " → ".join(str(part) for part in err["loc"])
            lines.append(f"  [{loc}] {err['msg']}")
        raise ValueError("\n".join(lines)) from exc

    return settings


@lru_cache(maxsize=1)
def get_config() -> Settings:
    """Return the cached configuration singleton.

    Calls ``load_config()`` on first access. Subsequent calls return the
    cached instance. Call ``get_config.cache_clear()`` in tests to reset.
    """
    return load_config()
