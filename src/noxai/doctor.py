"""Comprehensive health and diagnostic checks for NoX (Doctor)."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console
from rich.table import Table

TOKEN_PATH = Path("~/.local/share/nox/session_token").expanduser()
CONFIG_DIR = Path("~/.config/nox").expanduser()
SECRETS_FILE = CONFIG_DIR / "secrets.env"
DB_PATH = Path("~/.local/share/nox/nox.db").expanduser()
SYSTEMD_USER_DIR = Path("~/.config/systemd/user").expanduser()
SERVICE_UNIT_PATH = SYSTEMD_USER_DIR / "noxd.service"
UI_BUILD = Path(__file__).parent.parent.parent / "ui" / "dist" / "index.html"


@dataclass
class DoctorCheck:
    name: str
    status: str  # "PASS" | "WARN" | "FAIL"
    hint: str


def check_python_version() -> DoctorCheck:
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 11):
        return DoctorCheck(
            f"Python ≥ 3.11 (found {major}.{minor})",
            "FAIL",
            "Install Python 3.11+ — on Arch: sudo pacman -S python",
        )
    if (major, minor) > (3, 12):
        return DoctorCheck(
            f"Python version (found {major}.{minor})",
            "WARN",
            f"Python {major}.{minor} is above tested max 3.12",
        )
    return DoctorCheck(f"Python version (found {major}.{minor})", "PASS", "")


def check_ollama() -> tuple[DoctorCheck, list[dict[str, Any]]]:
    try:
        resp = httpx.get("http://127.0.0.1:11434/api/tags", timeout=5.0)
        resp.raise_for_status()
        models = resp.json().get("models", [])
        return DoctorCheck("Ollama reachable at :11434", "PASS", ""), models
    except httpx.ConnectError:
        return DoctorCheck(
            "Ollama reachable at :11434", "FAIL", "Start Ollama: sudo systemctl start ollama"
        ), []
    except Exception as exc:
        return DoctorCheck("Ollama reachable at :11434", "FAIL", f"Ollama error: {exc}"), []


def _model_present(model_name: str, ollama_models: list[dict[str, Any]]) -> bool:
    names = {m.get("name", "") for m in ollama_models}
    return (
        model_name in names
        or f"{model_name}:latest" in names
        or any(n.startswith(model_name + ":") for n in names)
    )


def check_models(cfg: Any, ollama_models: list[dict[str, Any]]) -> list[DoctorCheck]:
    checks = []
    roles = getattr(cfg.models, "roles", {}) if (cfg and hasattr(cfg, "models")) else {}

    brain_cfg = roles.get("brain")
    brain_m = getattr(brain_cfg, "model", "qwen2.5:14b") if brain_cfg else "qwen2.5:14b"
    brain_prov = getattr(brain_cfg, "provider", "ollama") if brain_cfg else "ollama"
    if brain_prov == "ollama":
        brain_ok = _model_present(brain_m, ollama_models)
        checks.append(
            DoctorCheck(
                f"Brain model in Ollama ({brain_m})",
                "PASS" if brain_ok else "FAIL",
                f"Pull it: ollama pull {brain_m}",
            )
        )
    elif brain_prov == "nvidia":
        checks.append(
            DoctorCheck(
                f"Brain model in NVIDIA ({brain_m})",
                "PASS",
                "",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                f"Brain model ({brain_m}) via {brain_prov}",
                "PASS",
                "",
            )
        )

    embed_cfg = roles.get("embed")
    embed_m = getattr(embed_cfg, "model", "nomic-embed-text") if embed_cfg else "nomic-embed-text"
    embed_prov = getattr(embed_cfg, "provider", "ollama") if embed_cfg else "ollama"
    if embed_prov == "ollama":
        embed_ok = _model_present(embed_m, ollama_models)
        checks.append(
            DoctorCheck(
                f"Embed model in Ollama ({embed_m})",
                "PASS" if embed_ok else "FAIL",
                f"Pull it: ollama pull {embed_m}",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                f"Embed model ({embed_m}) via {embed_prov}",
                "PASS",
                "",
            )
        )

    vision_cfg = roles.get("vision")
    vision_m = getattr(vision_cfg, "model", "gemma4:e4b") if vision_cfg else "gemma4:e4b"
    vision_prov = getattr(vision_cfg, "provider", "ollama") if vision_cfg else "ollama"
    if vision_prov == "ollama":
        vision_ok = _model_present(vision_m, ollama_models)
        checks.append(
            DoctorCheck(
                f"Vision model in Ollama ({vision_m})",
                "PASS" if vision_ok else "WARN",
                f"Pull it: ollama pull {vision_m}",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                f"Vision model ({vision_m}) via {vision_prov}",
                "PASS",
                "",
            )
        )

    return checks


def check_vector_backend() -> DoctorCheck:
    import noxai.db

    backend = noxai.db._detect_vector_backend()
    return DoctorCheck(f"Vector backend in use ({backend})", "PASS", "")


def check_db_migrations(db_path: Path = DB_PATH) -> DoctorCheck:
    if not db_path.exists():
        return DoctorCheck(
            "DB migrations applied", "FAIL", "Run migrations: nx run (or python -m noxai)"
        )
    try:
        migrations_dir = Path(__file__).parent / "migrations"
        total_files = len(list(migrations_dir.glob("[0-9][0-9][0-9]_*.sql")))

        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT filename FROM schema_migrations ORDER BY id").fetchall()
        conn.close()
        n = len(rows)
        if n == 0:
            return DoctorCheck(
                "DB migrations applied", "FAIL", "No migrations applied — run nx run"
            )
        if n < total_files:
            return DoctorCheck(
                f"DB migrations applied ({n}/{total_files})",
                "FAIL",
                f"Only {n}/{total_files} migrations applied — run nx run",
            )
        return DoctorCheck(f"DB migrations applied ({n}/{total_files})", "PASS", "")
    except Exception as exc:
        return DoctorCheck("DB migrations applied", "FAIL", f"DB error: {exc}")


def check_secrets_permissions(secrets_file: Path = SECRETS_FILE) -> DoctorCheck:
    if not secrets_file.exists():
        return DoctorCheck("Secrets file permissions", "WARN", "secrets.env absent; cloud disabled")
    try:
        mode = stat.S_IMODE(secrets_file.stat().st_mode)
        if mode & 0o177:
            return DoctorCheck(
                f"Secrets file permissions ({oct(mode)})", "FAIL", f"chmod 600 {secrets_file}"
            )
        return DoctorCheck("Secrets file permissions (0600)", "PASS", "")
    except Exception as exc:
        return DoctorCheck("Secrets file permissions", "FAIL", str(exc))


def check_nvidia_key_source(secrets_file: Path = SECRETS_FILE) -> tuple[DoctorCheck, bool]:
    var_name = None
    if os.environ.get("NOX_NVIDIA_API_KEY"):
        var_name = "NOX_NVIDIA_API_KEY (env)"
    elif os.environ.get("NVIDIA_API_KEY"):
        var_name = "NVIDIA_API_KEY (env)"
    elif secrets_file.exists():
        try:
            txt = secrets_file.read_text()
            if "NOX_NVIDIA_API_KEY=" in txt:
                var_name = "NOX_NVIDIA_API_KEY (secrets.env)"
            elif "NVIDIA_API_KEY=" in txt:
                var_name = "NVIDIA_API_KEY (secrets.env)"
        except OSError:
            pass

    if var_name:
        return DoctorCheck(f"NVIDIA API key configured via {var_name}", "PASS", ""), True
    return DoctorCheck(
        "NVIDIA API key",
        "WARN",
        "WARN: cloud disabled (add NVIDIA_API_KEY to ~/.config/nox/secrets.env then chmod 600)",
    ), False


def check_nvidia_models_endpoint(cfg: Any, has_key: bool) -> DoctorCheck:
    if not has_key:
        return DoctorCheck("NVIDIA API endpoint", "WARN", "Cloud disabled; skipping endpoint check")

    key_val = None
    if cfg and getattr(cfg, "nvidia_api_key", None):
        key_val = cfg.nvidia_api_key.get_secret_value()

    if not key_val:
        return DoctorCheck("NVIDIA API endpoint", "WARN", "API key missing in loaded config")

    heavy_id = "nvidia/llama-3.3-nemotron-super-49b-v1.5"
    if cfg and "heavy" in cfg.models.roles:
        heavy_id = cfg.models.roles["heavy"].model

    try:
        resp = httpx.get(
            "https://integrate.api.nvidia.com/v1/models",
            headers={"Authorization": f"Bearer {key_val}"},
            timeout=5.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            model_ids = {m.get("id") for m in data.get("data", [])}
            if heavy_id in model_ids:
                return DoctorCheck(f"NVIDIA models API ({heavy_id} available)", "PASS", "")
            return DoctorCheck(
                f"NVIDIA models API ({heavy_id} missing)",
                "WARN",
                f"Configured heavy model '{heavy_id}' not found in NVIDIA /models",
            )
        return DoctorCheck(
            "NVIDIA API endpoint", "WARN", f"NVIDIA models returned HTTP {resp.status_code}"
        )
    except httpx.ConnectError:
        return DoctorCheck(
            "NVIDIA API endpoint", "WARN", "NVIDIA endpoint unreachable — check network/VPN"
        )
    except Exception as exc:
        return DoctorCheck("NVIDIA API endpoint", "WARN", f"NVIDIA error: {exc}")


def check_cloud_health_state(has_key: bool = True) -> DoctorCheck:
    from noxai.models.cloud_health import CloudHealthState, get_cloud_health

    ch = get_cloud_health(has_key=has_key)
    if ch.state == CloudHealthState.ACTIVE:
        return DoctorCheck("Cloud health state", "PASS", f"Active ({ch.reason})")
    if ch.state == CloudHealthState.NO_KEY:
        return DoctorCheck("Cloud health state", "WARN", "Cloud disabled: no API key")
    if ch.state == CloudHealthState.DEGRADED:
        return DoctorCheck("Cloud health state", "WARN", f"Degraded: {ch.reason}")
    return DoctorCheck("Cloud health state", "WARN", f"{ch.state.value}: {ch.reason}")


def check_server_port(cfg: Any) -> DoctorCheck:
    srv_host = "127.0.0.1"
    srv_port = 7777
    if cfg:
        srv_host = cfg.server.host
        srv_port = cfg.server.port

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        try:
            s.connect((srv_host, srv_port))
            try:
                r = httpx.get(f"http://{srv_host}:{srv_port}/api/status", timeout=1.5)
                if r.status_code == 200:
                    return DoctorCheck(f"Port {srv_port} active (daemon running)", "PASS", "")
            except (httpx.HTTPError, OSError):
                pass
            return DoctorCheck(
                f"Port {srv_port} in use",
                "FAIL",
                f"Stop process using port {srv_port}: ss -tlnp | grep {srv_port}",
            )
        except (TimeoutError, ConnectionRefusedError):
            return DoctorCheck(f"Port {srv_port} free", "PASS", "")


def check_notify_send() -> DoctorCheck:
    ok = shutil.which("notify-send") is not None
    return DoctorCheck(
        "notify-send in PATH",
        "PASS" if ok else "WARN",
        "Install libnotify: sudo pacman -S libnotify" if not ok else "",
    )


def check_node_npm() -> DoctorCheck:
    ok = shutil.which("node") is not None and shutil.which("npm") is not None
    return DoctorCheck(
        "node and npm in PATH",
        "PASS" if ok else "WARN",
        "Install Node.js: sudo pacman -S nodejs npm" if not ok else "",
    )


def check_browser(cfg: Any) -> DoctorCheck:
    candidates = []
    if cfg and cfg.ui.launcher_cmd:
        candidates.append(cfg.ui.launcher_cmd[0])
    candidates.extend(["chromium", "google-chrome-stable", "google-chrome", "firefox", "xdg-open"])

    for b in candidates:
        if shutil.which(b):
            return DoctorCheck(f"Browser found ({b})", "PASS", "")
    return DoctorCheck(
        "Browser for UI launcher", "WARN", "Install a supported browser: firefox, chromium, chrome"
    )


def check_systemd_unit(unit_path: Path = SERVICE_UNIT_PATH) -> DoctorCheck:
    if not unit_path.exists():
        return DoctorCheck(
            "systemd user unit noxd.service (not installed)",
            "WARN",
            "Install unit with: nx service install",
        )

    try:
        content = unit_path.read_text()
        exec_start_line = None
        for line in content.splitlines():
            if line.strip().startswith("ExecStart="):
                exec_start_line = line.strip().split("=", 1)[1].strip()
                break

        if exec_start_line:
            bin_target = exec_start_line.split()[0]
            bin_target = bin_target.replace("%h", str(Path.home()))
            if not Path(bin_target).exists():
                return DoctorCheck(
                    f"systemd ExecStart target missing ({bin_target})",
                    "FAIL",
                    f"ExecStart target '{bin_target}' does not exist! Re-run: nx service install",
                )
    except Exception as exc:
        return DoctorCheck("systemd user unit noxd.service", "FAIL", f"Failed reading unit: {exc}")

    try:
        res = subprocess.run(
            ["systemctl", "--user", "is-enabled", "noxd.service"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        state = res.stdout.strip()
        if state == "enabled":
            return DoctorCheck("systemd user unit noxd.service (enabled)", "PASS", "")
        if state in ("disabled", "static"):
            return DoctorCheck(
                f"systemd user unit noxd.service ({state})",
                "WARN",
                "Enable with: systemctl --user enable noxd.service",
            )
        return DoctorCheck(
            f"systemd user unit noxd.service ({state})", "WARN", f"Unit state: {state}"
        )
    except FileNotFoundError:
        return DoctorCheck("systemd user unit", "WARN", "systemctl not found")
    except Exception as exc:
        return DoctorCheck("systemd user unit", "WARN", str(exc))


def check_noxd_enabled() -> DoctorCheck:
    """Check if systemd user unit noxd.service is enabled."""
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-enabled", "noxd.service"],
            capture_output=True,
            text=True,
            check=False,
        )
        state = proc.stdout.strip()
        if state == "enabled":
            return DoctorCheck("noxd enabled", "PASS", "")
        return DoctorCheck(
            "noxd enabled",
            "WARN",
            f"noxd is {state}; enable with 'systemctl --user enable noxd.service'",
        )
    except Exception as exc:
        return DoctorCheck("noxd enabled", "WARN", str(exc))


def check_linger() -> DoctorCheck:
    """Check if user linger is enabled via loginctl."""
    user = os.environ.get("USER", "")
    if not user:
        try:
            import pwd

            user = pwd.getpwuid(os.getuid()).pw_name
        except Exception:
            user = "current-user"

    try:
        proc = subprocess.run(
            ["loginctl", "show-user", user, "-p", "Linger"],
            capture_output=True,
            text=True,
            check=False,
        )
        out = proc.stdout.strip()
        if "Linger=yes" in out:
            return DoctorCheck("linger (enabled)", "PASS", "Daemon persists across user logout")
        return DoctorCheck(
            "linger",
            "WARN",
            "loginctl linger is disabled; daemon starts at login and boot catch-up covers "
            "missed slots. Run 'loginctl enable-linger' for unattended 04:00 runs.",
        )
    except Exception as exc:
        return DoctorCheck("linger", "WARN", f"loginctl check notice: {exc}")


def check_desktop_app() -> DoctorCheck:
    """Check if nox-app or nox-desktop binary is available and version aligned."""
    app_bin = shutil.which("nox-app")
    desktop_bin = shutil.which("nox-desktop")
    repo_desktop = Path(__file__).resolve().parent.parent.parent / "desktop" / "src-tauri" / "target" / "release" / "nox-desktop"
    opt_desktop = Path("/opt/nox/bin/nox-desktop")

    found_path = None
    if app_bin:
        found_path = app_bin
    elif desktop_bin:
        found_path = desktop_bin
    elif opt_desktop.exists():
        found_path = str(opt_desktop)
    elif repo_desktop.exists():
        found_path = str(repo_desktop)

    if found_path:
        return DoctorCheck(f"Desktop app installed ({found_path})", "PASS", "")
    return DoctorCheck(
        "Desktop app installed",
        "WARN",
        "nox-app not found on PATH or /opt/nox/bin; build with 'make build-app' or install package",
    )


def check_autostart_config() -> DoctorCheck:
    """Check autostart configuration in XDG autostart or Hyprland config."""
    autostart_file = Path("~/.config/autostart/nox.desktop").expanduser()
    hypr_startup = Path("~/.config/hypr/startup.lua").expanduser()
    hypr_conf = Path("~/.config/hypr/hyprland.conf").expanduser()

    methods = []
    if autostart_file.exists():
        methods.append("XDG autostart")
    if hypr_startup.exists():
        try:
            content = hypr_startup.read_text()
            if "nox-app" in content:
                methods.append("Hyprland startup.lua")
        except Exception:
            pass
    if hypr_conf.exists():
        try:
            content = hypr_conf.read_text()
            if "nox-app" in content:
                methods.append("hyprland.conf")
        except Exception:
            pass

    if methods:
        return DoctorCheck(f"Autostart configured ({', '.join(methods)})", "PASS", "")
    return DoctorCheck(
        "Autostart configured",
        "WARN",
        "Autostart not configured; run 'nx setup --all' or 'nx setup --autostart'",
    )


def check_notification_action() -> DoctorCheck:
    """Check desktop notification daemon and action capability."""
    notify_send = shutil.which("notify-send")
    if not notify_send:
        return DoctorCheck(
            "Notification daemon & actions",
            "WARN",
            "notify-send missing; install libnotify via 'sudo pacman -S libnotify'",
        )

    # Check if a notification daemon is active on D-Bus
    try:
        proc = subprocess.run(
            ["busctl", "--user", "status", "org.freedesktop.Notifications"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if proc.returncode == 0:
            return DoctorCheck("Notification daemon (D-Bus active)", "PASS", "Action buttons supported")
    except Exception:
        pass

    return DoctorCheck("Notification daemon (notify-send present)", "PASS", "")


def check_db_backups() -> DoctorCheck:
    """Check automatic database backup directory and recency."""
    backup_dir = Path("~/.local/share/nox/backups").expanduser()
    if not backup_dir.exists():
        return DoctorCheck(
            "Database backups",
            "WARN",
            "Backup directory ~/.local/share/nox/backups not yet created; run 'nx backup'",
        )

    backups = list(backup_dir.glob("nox_*.db"))
    if backups:
        count = len(backups)
        newest = max(backups, key=lambda p: p.stat().st_mtime)
        return DoctorCheck(
            f"Database backups ({count} available)",
            "PASS",
            f"Latest: {newest.name}",
        )
    return DoctorCheck(
        "Database backups",
        "WARN",
        "No database backups found in ~/.local/share/nox/backups; run 'nx backup'",
    )


def check_briefing_scheduler_running(cfg: Any) -> DoctorCheck:
    """Check if daemon briefing scheduler is active and reachable."""
    from noxai.api.auth import load_session_token

    host = (
        getattr(cfg.server, "host", "127.0.0.1") if cfg and hasattr(cfg, "server") else "127.0.0.1"
    )
    port = getattr(cfg.server, "port", 7777) if cfg and hasattr(cfg, "server") else 7777
    token = load_session_token() or ""

    try:
        resp = httpx.get(
            f"http://{host}:{port}/api/briefings/status",
            headers={"Authorization": f"Bearer {token}", "X-Nox-Client": "cli"},
            timeout=1.5,
        )
        if resp.status_code == 200:
            return DoctorCheck("briefing scheduler running", "PASS", "Daemon scheduler active")
        return DoctorCheck(
            "briefing scheduler running",
            "WARN",
            f"Scheduler returned HTTP {resp.status_code}",
        )
    except Exception:
        return DoctorCheck(
            "briefing scheduler running",
            "WARN",
            "Daemon offline; briefings run via systemd user unit on startup",
        )


def check_next_briefing_times(cfg: Any) -> DoctorCheck:
    """Check configured next morning and evening briefing run times."""
    from noxai.briefing.scheduler import BriefingScheduler

    try:
        sched = BriefingScheduler(config=cfg)
        runs = sched.get_next_runs()
        m_at = runs[0]["at"][11:16]
        e_at = runs[1]["at"][11:16]
        return DoctorCheck(
            "next morning/evening run times",
            "PASS",
            f"Morning: {m_at}, Evening: {e_at} ({sched.timezone})",
        )
    except Exception as exc:
        return DoctorCheck("next morning/evening run times", "WARN", str(exc))


def check_briefing_scheduler_reliability(cfg: Any) -> DoctorCheck:
    """Check reliability of the last 6 scheduled briefing slots (Session K)."""
    from noxai.db import get_db_path

    db_path = get_db_path()
    if not db_path.exists():
        return DoctorCheck(
            "Briefing scheduler reliability: last 6 slots (0 succeeded, 0 failed, 0 missed)",
            "WARN",
            "Database not found",
        )

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT date, slot, state, failure_reason, error
            FROM briefing_runs
            ORDER BY date DESC, CASE slot WHEN 'evening' THEN 2 ELSE 1 END DESC, rev DESC
            LIMIT 6
            """
        ).fetchall()
        conn.close()

        if not rows:
            return DoctorCheck(
                "Briefing scheduler reliability: last 6 slots (0 succeeded, 0 failed, 0 missed)",
                "WARN",
                "No recorded briefing slots",
            )

        succeeded = sum(1 for r in rows if r["state"] == "succeeded")
        failed = sum(1 for r in rows if r["state"] in ("failed", "partial", "interrupted"))
        missed = sum(1 for r in rows if r["state"] == "missed")

        name = (
            f"Briefing scheduler reliability: last {len(rows)} slots "
            f"({succeeded} succeeded, {failed} failed, {missed} missed)"
        )
        if missed == 0 and failed == 0:
            return DoctorCheck(name, "PASS", f"All {succeeded} slots completed successfully")
        if succeeded > 0:
            hint = (
                f"{succeeded}/{len(rows)} succeeded "
                f"({failed} failed, {missed} missed; see 'nx briefing audit')"
            )
            return DoctorCheck(name, "WARN", hint)
        hint = (
            f"0/{len(rows)} succeeded "
            f"({failed} failed, {missed} missed; see 'nx briefing audit')"
        )
        return DoctorCheck(name, "FAIL", hint)
    except Exception as exc:
        return DoctorCheck("Briefing scheduler reliability", "WARN", str(exc))


def check_gate(cfg: Any) -> DoctorCheck:
    if not cfg:
        return DoctorCheck("Gate mode: token_only", "PASS", "")
    mode = cfg.gate.mode
    if mode == "command":
        cmd = cfg.gate.command
        cmd_bin = cmd[0] if isinstance(cmd, list) and cmd else str(cmd).split()[0]
        if not shutil.which(cmd_bin):
            return DoctorCheck(
                f"Gate command ({cmd_bin} missing)",
                "FAIL",
                f"Gate command binary '{cmd_bin}' not found on PATH",
            )
        return DoctorCheck(f"Gate mode: command ({cmd_bin})", "PASS", "")
    return DoctorCheck(f"Gate mode: {mode}", "PASS", "")


def check_ui_build(build_path: Path = UI_BUILD) -> DoctorCheck:
    ok = build_path.exists()
    return DoctorCheck(
        "UI build present (ui/dist/index.html)",
        "PASS" if ok else "WARN",
        "Build UI: cd ui && npm install && npm run build" if not ok else "",
    )


def check_config_loads() -> tuple[DoctorCheck, Any]:
    from noxai.config import get_config

    try:
        cfg = get_config()
        return DoctorCheck("Configuration loads cleanly", "PASS", ""), cfg
    except Exception as exc:
        return DoctorCheck("Configuration loads cleanly", "FAIL", f"Config error: {exc}"), None


def check_provider_topology(cfg: Any) -> DoctorCheck:
    if not cfg or not hasattr(cfg, "models") or not hasattr(cfg.models, "roles"):
        return DoctorCheck(
            "Provider topology", "FAIL", "Cannot verify topology without valid config"
        )
    brain_p = cfg.models.roles.get("brain", None)
    heavy_p = cfg.models.roles.get("heavy", None)
    if not brain_p:
        return DoctorCheck("Provider topology", "FAIL", "Brain role missing in config")

    brain_prov = getattr(brain_p, "provider", "ollama")
    heavy_prov = getattr(heavy_p, "provider", "nvidia") if heavy_p else None

    if brain_prov not in ("ollama", "nvidia"):
        return DoctorCheck(
            "Provider topology", "FAIL", f"Unsupported brain provider '{brain_prov}'"
        )
    if heavy_p and heavy_prov == "ollama":
        return DoctorCheck(
            "Provider topology", "FAIL", "Heavy provider must be non-local (e.g. nvidia)"
        )

    summary_brain = "local" if brain_prov == "ollama" else "cloud"
    summary_heavy = "cloud" if (heavy_prov and heavy_prov != "ollama") else (heavy_prov or "none")
    return DoctorCheck(
        f"Provider topology: brain ({summary_brain}) / heavy ({summary_heavy})", "PASS", ""
    )


def check_lock_state() -> DoctorCheck:
    lock_file = Path("~/.local/state/nox/nox.lock").expanduser()
    if not lock_file.exists():
        lock_file = Path("/tmp/nox.lock")  # noqa: S108

    if lock_file.exists():
        try:
            pid = int(lock_file.read_text().strip())
            import psutil

            if psutil.pid_exists(pid):
                return DoctorCheck(f"Single-instance lock: held by PID {pid}", "PASS", "")
        except (ValueError, OSError):
            pass
    return DoctorCheck("Single-instance lock: free", "PASS", "")


def check_compositor() -> DoctorCheck:
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "unknown")
    session = os.environ.get("XDG_SESSION_TYPE", "unknown")
    return DoctorCheck(f"Detected compositor: {desktop} ({session})", "PASS", "")


def check_ram_budget(
    meminfo_path: Path = Path("/proc/meminfo"),
    ollama_base_url: str = "http://127.0.0.1:11434",
) -> DoctorCheck:
    """Check sum of loaded model sizes in Ollama against system MemAvailable (Spec §F6.3)."""
    mem_avail_kb: int | None = None
    if meminfo_path.exists():
        with contextlib.suppress(Exception):
            for line in meminfo_path.read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    mem_avail_kb = int(line.split()[1])
                    break

    if mem_avail_kb is None:
        return DoctorCheck("Ollama RAM budget", "PASS", "MemAvailable reading skipped")

    loaded_bytes = 0
    loaded_models_count = 0
    try:
        resp = httpx.get(f"{ollama_base_url}/api/ps", timeout=2.0)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            loaded_models_count = len(models)
            for m in models:
                loaded_bytes += int(m.get("size", 0) or m.get("size_vram", 0))
    except Exception:
        return DoctorCheck("Ollama RAM budget", "PASS", "Ollama not running or unreachable")

    loaded_kb = loaded_bytes // 1024
    loaded_gb = loaded_bytes / (1024**3)
    avail_gb = (mem_avail_kb * 1024) / (1024**3)
    ratio = (loaded_kb / mem_avail_kb) if mem_avail_kb > 0 else 0.0

    if ratio > 0.80:
        return DoctorCheck(
            f"Ollama RAM budget ({loaded_gb:.1f} GB loaded / {avail_gb:.1f} GB avail)",
            "WARN",
            f"Loaded models take {int(ratio * 100)}% of available RAM (> 80%). Risk of OOM.",
        )
    return DoctorCheck(
        f"Ollama RAM budget ({loaded_gb:.1f} GB loaded / {avail_gb:.1f} GB avail)",
        "PASS",
        f"{loaded_models_count} model(s) loaded ({int(ratio * 100)}% of available RAM)",
    )


def run_all_checks() -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []

    # 1. Python version
    checks.append(check_python_version())

    # 2. Config loads
    cfg_check, cfg = check_config_loads()
    checks.append(cfg_check)

    # 3. Ollama
    ollama_check, ollama_models = check_ollama()
    checks.append(ollama_check)

    # 4. Models
    checks.extend(check_models(cfg, ollama_models))

    # 4b. Ollama RAM budget check (F6.3)
    checks.append(check_ram_budget())

    # 5. Vector backend
    checks.append(check_vector_backend())

    # 6. DB migrations
    checks.append(check_db_migrations())

    # 7. Secrets file
    checks.append(check_secrets_permissions())

    # 8. NVIDIA key present and variable source
    nv_key_check, has_key = check_nvidia_key_source()
    checks.append(nv_key_check)

    # 9. NVIDIA models API
    checks.append(check_nvidia_models_endpoint(cfg, has_key))
    checks.append(check_cloud_health_state(has_key=has_key))

    # 10. Server port
    checks.append(check_server_port(cfg))

    # 11. notify-send
    checks.append(check_notify_send())

    # 12. node and npm
    checks.append(check_node_npm())

    # 13. Browser
    checks.append(check_browser(cfg))

    # 14. systemd user unit & ExecStart
    checks.append(check_systemd_unit())

    # 15. Gate
    checks.append(check_gate(cfg))

    # 16. UI build
    checks.append(check_ui_build())

    # 17. Provider topology
    checks.append(check_provider_topology(cfg))

    # 18. Lock state
    checks.append(check_lock_state())

    # 19. Compositor
    checks.append(check_compositor())

    # 20. Briefing scheduler & next runs
    checks.append(check_briefing_scheduler_running(cfg))
    checks.append(check_next_briefing_times(cfg))
    checks.append(check_briefing_scheduler_reliability(cfg))

    # 21. noxd enabled & linger
    checks.append(check_noxd_enabled())
    checks.append(check_linger())

    # 22. Desktop app, autostart, notifications, and backups
    checks.append(check_desktop_app())
    checks.append(check_autostart_config())
    checks.append(check_notification_action())
    checks.append(check_db_backups())

    return checks


def run_doctor(console: Console, json_output: bool = False) -> None:
    checks = run_all_checks()

    failed = [c for c in checks if c.status == "FAIL"]
    warned = [c for c in checks if c.status == "WARN"]
    passed = [c for c in checks if c.status == "PASS"]

    if json_output:
        payload = {
            "all_pass": len(failed) == 0,
            "failed_count": len(failed),
            "warn_count": len(warned),
            "passed_count": len(passed),
            "checks": [{"name": c.name, "status": c.status, "hint": c.hint} for c in checks],
        }
        print(json.dumps(payload, indent=2))
        if failed:
            sys.exit(1)
        return

    console.rule("[bold blue]NoX Doctor[/bold blue]")
    table = Table(show_header=True, header_style="bold")
    table.add_column("Check", style="bold", no_wrap=True)
    table.add_column("Status", justify="center")
    table.add_column("Fix / Note", style="dim")

    for c in checks:
        if c.status == "PASS":
            badge = "[green]PASS[/green]"
        elif c.status == "WARN":
            badge = "[yellow]WARN[/yellow]"
        else:
            badge = "[red]FAIL[/red]"
        table.add_row(c.name, badge, c.hint)

    console.print(table)

    if failed:
        console.print(
            f"\n[bold red]{len(failed)} check(s) failed.[/bold red] "
            "Fix the issues above, then run [bold]nx doctor[/bold] again."
        )
        sys.exit(1)
    elif warned:
        console.print(
            f"\n[bold green]All required checks passed[/bold green] "
            f"([yellow]{len(warned)} warning(s)[/yellow]). NoX is ready."
        )
    else:
        console.print(f"\n[bold green]All {len(passed)} checks passed.[/bold green] NoX is ready.")


def run_doctor_perf(
    console: Console,
    json_output: bool = False,
    meminfo_path: Path = Path("/proc/meminfo"),
    ollama_base_url: str = "http://127.0.0.1:11434",
) -> None:
    """Print performance tuning recommendations for Ollama without editing system files."""
    mem_total_kb = 0
    mem_avail_kb = 0
    if meminfo_path.exists():
        with contextlib.suppress(Exception):
            for line in meminfo_path.read_text().splitlines():
                if line.startswith("MemTotal:"):
                    mem_total_kb = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    mem_avail_kb = int(line.split()[1])
    total_gb = mem_total_kb / (1024 * 1024) if mem_total_kb else 16.0
    avail_gb = mem_avail_kb / (1024 * 1024) if mem_avail_kb else 12.0

    running_models: list[dict[str, Any]] = []
    partial_offload_flags: list[str] = []
    with contextlib.suppress(Exception):
        resp = httpx.get(f"{ollama_base_url}/api/ps", timeout=2.0)
        if resp.status_code == 200:
            running_models = resp.json().get("models", [])
            for m in running_models:
                name = m.get("name", "unknown")
                details = m.get("details", {})
                proc = str(
                    m.get("processor", "")
                    or details.get("processor", "")
                    or details.get("format", "")
                )
                if "% CPU" in proc and "% GPU" in proc:
                    partial_offload_flags.append(f"{name}: partial offload ({proc})")

    # RAM-based max loaded models
    max_models = 3 if total_gb >= 28.0 else 2

    recommendations = [
        (
            f"OLLAMA_MAX_LOADED_MODELS={max_models}",
            f"Keep resident model, embedder, and brain loaded in RAM without evictions "
            f"(measured RAM: {total_gb:.1f} GB).",
        ),
        (
            "OLLAMA_NUM_PARALLEL=1",
            "Single concurrent request per model to minimize memory duplication on desktop.",
        ),
        (
            "OLLAMA_FLASH_ATTENTION=1",
            "Enable Flash Attention for faster token generation and reduced context memory.",
        ),
        (
            "OLLAMA_KV_CACHE_TYPE=q8_0",
            "Quantize KV cache to 8-bit (requires flash attention) to halve KV memory usage.",
        ),
    ]

    if json_output:
        payload = {
            "total_ram_gb": round(total_gb, 1),
            "avail_ram_gb": round(avail_gb, 1),
            "loaded_models_count": len(running_models),
            "partial_offload_flags": partial_offload_flags,
            "recommended_env": {
                "OLLAMA_MAX_LOADED_MODELS": str(max_models),
                "OLLAMA_NUM_PARALLEL": "1",
                "OLLAMA_FLASH_ATTENTION": "1",
                "OLLAMA_KV_CACHE_TYPE": "q8_0",
            },
            "recommendations": [{"setting": k, "reason": r} for k, r in recommendations],
        }
        print(json.dumps(payload, indent=2))
        return

    console.rule("[bold blue]NoX Doctor — Ollama Performance Tuning (§F6.4)[/bold blue]")
    console.print(f"[bold]System RAM:[/] {total_gb:.1f} GB total, {avail_gb:.1f} GB available")
    console.print(f"[bold]Active Models (ollama ps):[/] {len(running_models)} loaded")

    if partial_offload_flags:
        for flag in partial_offload_flags:
            console.print(
                f"[yellow]⚠ Warning:[/] {flag} — consider reducing model size or context."
            )

    table = Table(title="Recommended Ollama Environment Settings", show_header=True)
    table.add_column("Setting", style="bold cyan")
    table.add_column("Rationale", style="dim")

    for setting, reason in recommendations:
        table.add_row(setting, reason)

    console.print(table)
    console.print(
        "\n[bold yellow]To apply these settings (print only — NoX never modifies system units):[/]"
    )
    console.print("[dim]Run:[/] [bold]sudo systemctl edit ollama[/bold]")
    console.print("[dim]Add the following under [Service]:[/]")
    console.print(
        "[bold cyan][Service]\n"
        f'Environment="OLLAMA_MAX_LOADED_MODELS={max_models}"\n'
        'Environment="OLLAMA_NUM_PARALLEL=1"\n'
        'Environment="OLLAMA_FLASH_ATTENTION=1"\n'
        'Environment="OLLAMA_KV_CACHE_TYPE=q8_0"[/bold cyan]'
    )
    console.print(
        "[dim]Then reload and restart:[/] "
        "[bold]sudo systemctl daemon-reload && sudo systemctl restart ollama[/bold]\n"
    )
