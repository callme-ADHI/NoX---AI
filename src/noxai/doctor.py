"""Comprehensive health and diagnostic checks for NoX (Doctor)."""

from __future__ import annotations

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
    brain_m = (
        cfg.models.roles["brain"].model if (cfg and "brain" in cfg.models.roles) else "qwen2.5:14b"
    )
    brain_ok = _model_present(brain_m, ollama_models)
    checks.append(
        DoctorCheck(
            f"Brain model in Ollama ({brain_m})",
            "PASS" if brain_ok else "FAIL",
            f"Pull it: ollama pull {brain_m}",
        )
    )

    embed_m = (
        cfg.models.roles["embed"].model
        if (cfg and "embed" in cfg.models.roles)
        else "nomic-embed-text"
    )
    embed_ok = _model_present(embed_m, ollama_models)
    checks.append(
        DoctorCheck(
            f"Embed model in Ollama ({embed_m})",
            "PASS" if embed_ok else "FAIL",
            f"Pull it: ollama pull {embed_m}",
        )
    )

    vision_m = (
        cfg.models.roles["vision"].model if (cfg and "vision" in cfg.models.roles) else "gemma4:e4b"
    )
    vision_ok = _model_present(vision_m, ollama_models)
    checks.append(
        DoctorCheck(
            f"Vision model in Ollama ({vision_m})",
            "PASS" if vision_ok else "WARN",
            f"Pull it: ollama pull {vision_m}",
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
    if not cfg:
        return DoctorCheck(
            "Provider topology", "FAIL", "Cannot verify topology without valid config"
        )
    brain_p = cfg.models.roles.get("brain", None)
    heavy_p = cfg.models.roles.get("heavy", None)
    if not brain_p or brain_p.provider != "ollama":
        return DoctorCheck("Provider topology", "FAIL", "Brain provider must be local (ollama)")
    if heavy_p and heavy_p.provider == "ollama":
        return DoctorCheck(
            "Provider topology", "FAIL", "Heavy provider must be non-local (e.g. nvidia)"
        )
    return DoctorCheck("Provider topology: brain (local) / heavy (cloud)", "PASS", "")


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
