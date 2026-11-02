"""Configuration loading and validation for NoX.

Loads ``~/.config/nox/config.toml`` via stdlib ``tomllib``, validates it
with Pydantic v2, and exposes a cached ``get_config()`` accessor.

Secrets are loaded from ``~/.config/nox/secrets.env`` (must be mode 0600).
The file format is simple KEY=VALUE lines; no shell expansion is performed.
"""

from __future__ import annotations

import os
import re
import stat
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import (
    BaseModel,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
def get_config_dir() -> Path:
    """Return active config directory, honoring NOX_CONFIG_DIR."""
    if "NOX_CONFIG_DIR" in os.environ:
        return Path(os.environ["NOX_CONFIG_DIR"]).expanduser().resolve()
    return Path("~/.config/nox").expanduser().resolve()


def get_config_file() -> Path:
    """Return active config file path, honoring NOX_CONFIG_FILE."""
    if "NOX_CONFIG_FILE" in os.environ:
        return Path(os.environ["NOX_CONFIG_FILE"]).expanduser().resolve()
    return get_config_dir() / "config.toml"


def get_secrets_file() -> Path:
    """Return active secrets file path, honoring NOX_SECRETS_FILE."""
    if "NOX_SECRETS_FILE" in os.environ:
        return Path(os.environ["NOX_SECRETS_FILE"]).expanduser().resolve()
    return get_config_dir() / "secrets.env"


CONFIG_DIR = get_config_dir()
CONFIG_FILE = get_config_file()
SECRETS_FILE = get_secrets_file()

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
    resident_num_ctx: int = 4096
    resident_allow_tool_loops: bool = False


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
    token_budget: int = 1200
    half_life_days: float = 30.0
    archive_after_days: int = 90


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
    stars: str = "calm"


class PrivacyConfig(_StrictModel):
    """Privacy configuration for Milestone 10 (§1.2, §1.3)."""

    chat_to_cloud: bool = True
    cloud_profile_card: bool = False
    private_mode: bool = False


class KeypoolConfig(_StrictModel):
    """NVIDIA key pool allocation and rate parameters (Phase 3)."""

    rpm_per_key: int = 35
    concurrency_per_key: int = 2
    reserve_interactive: bool = True
    cooldown_base_s: float = 5.0
    cooldown_max_s: float = 120.0
    wide_mode: bool = True


class BriefingQuotasConfig(_StrictModel):
    """Briefing item category targets and limits (Session J)."""

    internships_target: int = 10
    hackathons_target: int = 8
    competitions_target: int = 5
    ctf_max: int = 3
    startup_ideas: int = 3
    news_per_topic: int = 4


class BriefingEligibilityProfile(_StrictModel):
    """Local user eligibility profile for program matching (never sent to cloud)."""

    nationality: str = "India"
    level: str = "undergraduate"
    field: str = "computer science"


class BriefingValueWeights(_StrictModel):
    """Value scoring weights for internships and opportunities."""

    accommodation_provided: int = 30
    accommodation_partial: int = 15
    stipend_yes: int = 25
    travel_covered: int = 15
    travel_partial: int = 7
    abroad: int = 15
    visa_support: int = 5
    known_reputable_org: int = 5
    eligibility_yes: int = 5
    eligibility_unknown: int = -5
    deadline_open_gt_7d: int = 5
    deadline_closing_lte_7d: int = 8
    rolling: int = 2


class BriefingConfig(_StrictModel):
    """Automated briefing engine settings (Phase 4)."""

    enabled: bool = True
    timezone: str = "Asia/Kolkata"
    morning_start: str = "04:00"
    evening_start: str = "18:00"
    catchup_on_boot: bool = True
    max_items_news_per_topic: int = 6
    max_items_opportunities: int = 10
    min_items_total: int = 15
    freshness_hours_news: int = 48
    freshness_days_papers: int = 7
    notify_on_start: bool = False
    notify_on_done: bool = True
    staging_retention_days: int = 3
    run_timeout_minutes: int = 15
    topic_timeout_minutes: int = 5
    quotas: BriefingQuotasConfig = Field(default_factory=BriefingQuotasConfig)
    eligibility_profile: BriefingEligibilityProfile = Field(
        default_factory=BriefingEligibilityProfile
    )
    value_weights: BriefingValueWeights = Field(default_factory=BriefingValueWeights)


class ResearchConfig(_StrictModel):
    """Web research module configuration (Phase 8)."""

    user_agent: str = "NoX-Research/1.0 (+https://github.com/callme-ADHI/NoX---AI)"
    per_domain_rps: float = 1.0
    fetch_timeout_s: float = 12.0
    max_bytes: int = 1_000_000
    cache_ttl_h: int = 24
    deny_domains: list[str] = ["pinterest.com", "quora.com"]
    boost_domains: list[str] = [
        "arxiv.org",
        "github.com",
        "news.ycombinator.com",
        "thehackernews.com",
    ]


class NotesConfig(_StrictModel):
    """Notes Vault configuration (Phase 5)."""

    vault_path: str = "~/Documents/NoX-Vault"
    export_threads: bool = True


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
    privacy: PrivacyConfig = PrivacyConfig()
    keypool: KeypoolConfig = KeypoolConfig()
    briefing: BriefingConfig = BriefingConfig()
    research: ResearchConfig = ResearchConfig()
    notes: NotesConfig = NotesConfig()

    # Secrets (loaded separately, not from TOML)
    nvidia_api_key: SecretStr | None = None
    nvidia_api_keys: list[SecretStr] = Field(default_factory=list)

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

_SECRET_PATTERN = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$")


def _load_secrets() -> dict[str, str]:
    """Load KEY=VALUE pairs from the secrets file.

    Returns an empty dict if the file does not exist.
    Raises ``PermissionError`` if the file has permissions looser than 0600.
    Never logs secret values.
    """
    sec_file = get_secrets_file()
    if not sec_file.exists():
        return {}

    # Check file permissions — must be exactly 0o600 (owner r/w, nothing else)
    file_stat = sec_file.stat()
    mode = stat.S_IMODE(file_stat.st_mode)
    if mode & 0o177:  # any bits beyond owner rw (0o600)
        raise PermissionError(
            f"Secrets file {sec_file} has unsafe permissions "
            f"({oct(mode)}). Fix with: chmod 600 {sec_file}"
        )

    secrets: dict[str, str] = {}
    for line_no, line in enumerate(sec_file.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _SECRET_PATTERN.match(line)
        if not match:
            raise ValueError(
                f"Secrets file {sec_file} line {line_no}: expected KEY=VALUE format"
            )
        val = match.group(2).strip()
        is_double_quoted = val.startswith('"') and val.endswith('"')
        is_single_quoted = val.startswith("'") and val.endswith("'")
        if is_double_quoted or is_single_quoted:
            val = val[1:-1]
        secrets[match.group(1)] = val
    return secrets


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def _ensure_config_exists() -> None:
    """Create config directory and copy example config if not present."""
    cfg_dir = get_config_dir()
    cfg_file = get_config_file()
    cfg_dir.mkdir(parents=True, exist_ok=True)
    if not cfg_file.exists():
        if _EXAMPLE_CONFIG.exists():
            cfg_file.write_text(_EXAMPLE_CONFIG.read_text())
        else:
            # Minimal bootstrap config
            cfg_file.write_text(
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
        print(f"Created default config at {cfg_file}")


def _load_toml() -> dict[str, Any]:
    """Read and parse the TOML config file."""
    _ensure_config_exists()
    with open(get_config_file(), "rb") as f:
        return tomllib.load(f)


def load_config() -> Settings:
    """Load, validate and return the full configuration.

    On validation failure, raises a ``ValueError`` with a human-readable
    description of what is wrong and how to fix it.
    """
    raw = _load_toml()

    # Load secrets
    secrets = _load_secrets()

    # If NVIDIA_API_KEY was placed in [models.nvidia], safely extract it
    if isinstance(raw.get("models"), dict) and isinstance(raw["models"].get("nvidia"), dict):
        nvidia_map = raw["models"]["nvidia"]
        legacy_key = nvidia_map.pop("NVIDIA_API_KEY", None) or nvidia_map.pop("api_key", None)
        if legacy_key and "NVIDIA_API_KEY" not in secrets:
            secrets["NVIDIA_API_KEY"] = legacy_key

    # Inject secrets into raw dict so Pydantic can validate them
    # (they are not written back to disk)
    collected_keys: list[str] = []
    if "NOX_NVIDIA_API_KEY" in secrets:
        k_clean = secrets["NOX_NVIDIA_API_KEY"].strip()
        if k_clean and k_clean not in collected_keys:
            collected_keys.append(k_clean)
    if "NVIDIA_API_KEYS" in secrets:
        for k in secrets["NVIDIA_API_KEYS"].split(","):
            k_clean = k.strip()
            if k_clean and k_clean not in collected_keys:
                collected_keys.append(k_clean)
    for k_name, k_val in sorted(secrets.items()):
        if k_name.startswith("NVIDIA_API_KEY") and k_name != "NVIDIA_API_KEYS":
            k_clean = k_val.strip()
            if k_clean and k_clean not in collected_keys:
                collected_keys.append(k_clean)

    if collected_keys:
        raw["nvidia_api_keys"] = collected_keys
        raw["nvidia_api_key"] = collected_keys[0]
    else:
        key = secrets.get("NOX_NVIDIA_API_KEY") or secrets.get("NVIDIA_API_KEY")
        if key:
            raw["nvidia_api_key"] = key
            raw["nvidia_api_keys"] = [key]

    # Environment overrides for briefing slot start times
    if "NOX_BRIEFING_MORNING_START" in os.environ:
        raw.setdefault("briefing", {})["morning_start"] = os.environ[
            "NOX_BRIEFING_MORNING_START"
        ].strip()
    if "NOX_BRIEFING_EVENING_START" in os.environ:
        raw.setdefault("briefing", {})["evening_start"] = os.environ[
            "NOX_BRIEFING_EVENING_START"
        ].strip()
    if "NOX_SERVER_PORT" in os.environ:
        raw.setdefault("server", {})["port"] = int(os.environ["NOX_SERVER_PORT"].strip())

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
