"""NoX CLI — the ``nx`` command.

Commands implemented in M0:
    nx run      — start the daemon in the foreground
    nx status   — show daemon and system status
    nx doctor   — run all health checks and report PASS/FAIL
    nx token    — print the current session token

All other commands are defined as stubs that error cleanly until their
milestone is reached.

The ``nx run`` command in M0 starts a minimal FastAPI app (enough for
``/api/status``) so that ``nx status`` and ``nx doctor`` can reach it.
The full daemon is wired up in M7.
"""

from __future__ import annotations

import shutil
import socket
import stat
import subprocess
import sys
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="nx",
    help="NoX personal AI agent — command-line interface.",
    no_args_is_help=True,
)

console = Console()

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

TOKEN_PATH = Path("~/.local/share/nox/session_token").expanduser()
CONFIG_DIR = Path("~/.config/nox").expanduser()
SECRETS_FILE = CONFIG_DIR / "secrets.env"
DB_PATH = Path("~/.local/share/nox/nox.db").expanduser()
LOG_DIR = Path("~/.local/state/nox/logs").expanduser()
UI_BUILD = Path(__file__).parent.parent.parent / "ui" / "dist" / "index.html"


# ---------------------------------------------------------------------------
# nx run
# ---------------------------------------------------------------------------

@app.command()
def run(
    host: str = typer.Option("127.0.0.1", help="Bind address."),
    port: int = typer.Option(7777, help="Port."),
    reload: bool = typer.Option(False, "--reload", help="Hot-reload (dev only)."),
) -> None:
    """Start the NoX daemon in the foreground."""
    from noxai.logging_setup import setup_logging
    try:
        from noxai.config import get_config
        cfg = get_config()
        setup_logging(cfg.logging.level)
        _host = cfg.server.host
        _port = cfg.server.port
    except Exception as exc:
        setup_logging("INFO")
        console.print(f"[yellow]Config warning:[/] {exc}")
        _host = host
        _port = port

    import uvicorn
    # Import the API app lazily so import errors surface clearly
    try:
        from noxai.api.app import create_app
        application = create_app()
    except ImportError:
        # M0 minimal app — full API is wired in M7
        application = _create_minimal_app()

    uvicorn.run(
        application,
        host=_host,
        port=_port,
        reload=reload,
        log_config=None,  # we manage logging ourselves
    )


def _create_minimal_app() -> object:
    """Minimal FastAPI app for M0 that only serves /api/status."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    mini = FastAPI(title="NoX (M0 skeleton)", docs_url=None, redoc_url=None)

    @mini.get("/api/status")
    async def status() -> JSONResponse:
        return JSONResponse({"status": "ok", "milestone": "M0"})

    return mini


# ---------------------------------------------------------------------------
# nx status
# ---------------------------------------------------------------------------

@app.command()
def status(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(7777),
) -> None:
    """Show the current daemon status (calls GET /api/status)."""
    url = f"http://{host}:{port}/api/status"
    try:
        resp = httpx.get(url, timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        console.print_json(data=data)
    except httpx.ConnectError:
        console.print(
            f"[red]✗[/] Cannot reach daemon at {url}\n"
            "  Start it with: [bold]nx run[/bold]"
        )
        raise typer.Exit(1) from None
    except Exception as exc:
        console.print(f"[red]✗[/] {exc}")
        raise typer.Exit(1) from None


# ---------------------------------------------------------------------------
# nx doctor
# ---------------------------------------------------------------------------

@app.command()
def doctor() -> None:
    """Run health checks and print PASS/FAIL with fix hints."""
    console.rule("[bold blue]NoX Doctor[/bold blue]")

    checks: list[tuple[str, bool, str]] = []  # (label, passed, hint)

    # 1. Python version
    major, minor = sys.version_info[:2]
    py_ok = (major, minor) >= (3, 11)
    checks.append((
        f"Python ≥ 3.11  (found {major}.{minor})",
        py_ok,
        "Install Python 3.11+ — on Arch: pacman -S python",
    ))

    # 2. Ollama reachable
    ollama_ok, ollama_hint, ollama_data = _check_ollama()
    checks.append(("Ollama reachable at :11434", ollama_ok, ollama_hint))

    # 3. qwen2.5:14b model present
    qwen_ok = _model_present("qwen2.5:14b", ollama_data)
    checks.append((
        "qwen2.5:14b model in Ollama",
        qwen_ok,
        "Pull it: ollama pull qwen2.5:14b",
    ))

    # 4. nomic-embed-text present
    embed_ok = _model_present("nomic-embed-text", ollama_data)
    checks.append((
        "nomic-embed-text model in Ollama",
        embed_ok,
        "Pull it: ollama pull nomic-embed-text",
    ))

    # 5. sqlite-vec or numpy fallback
    vec_ok, vec_hint = _check_sqlite_vec()
    checks.append(("sqlite-vec (or numpy fallback active)", vec_ok, vec_hint))

    # 6. DB migrations applied
    db_ok, db_hint = _check_db()
    checks.append(("DB migrations applied", db_ok, db_hint))

    # 7. Secrets file permissions
    sec_ok, sec_hint = _check_secrets()
    checks.append(("Secrets file permissions (0600)", sec_ok, sec_hint))

    # 8. NVIDIA API key present
    key_ok = _check_nvidia_key()
    checks.append((
        "NVIDIA API key configured",
        key_ok,
        "Add NVIDIA_API_KEY=nvapi-... to ~/.config/nox/secrets.env then chmod 600",
    ))

    # 9. NVIDIA endpoint reachable (only if key present)
    if key_ok:
        nv_reach, nv_hint = _check_nvidia_reachable()
        checks.append(("NVIDIA API endpoint reachable", nv_reach, nv_hint))

    # 10. Server port free
    port_ok = _port_free("127.0.0.1", 7777)
    checks.append((
        "Port 7777 free (daemon not yet running)",
        port_ok,
        "Stop any process using port 7777: ss -tlnp | grep 7777",
    ))

    # 11. notify-send present
    ns_ok = shutil.which("notify-send") is not None
    checks.append((
        "notify-send in PATH",
        ns_ok,
        "Install libnotify: sudo pacman -S libnotify",
    ))

    # 12. systemd user unit
    unit_ok, unit_hint = _check_systemd_unit()
    checks.append(("systemd user unit noxd.service", unit_ok, unit_hint))

    # 13. Gate mode
    gate_mode, gate_hint = _check_gate()
    checks.append((f"Gate mode: {gate_mode}", True, gate_hint))  # always pass, just info

    # 14. UI build present
    ui_ok = UI_BUILD.exists()
    checks.append((
        "UI build present (ui/dist/index.html)",
        ui_ok,
        "Build UI: cd ui && npm install && npm run build",
    ))

    # Print results table
    table = Table(show_header=True, header_style="bold")
    table.add_column("Check", style="bold", no_wrap=True)
    table.add_column("Status", justify="center")
    table.add_column("Fix", style="dim")

    all_pass = True
    for label, passed, hint in checks:
        status_str = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
        if not passed:
            all_pass = False
        table.add_row(label, status_str, "" if passed else hint)

    console.print(table)

    if all_pass:
        console.print("\n[bold green]All checks passed.[/bold green] NoX is ready.")
    else:
        n_fail = sum(1 for _, p, _ in checks if not p)
        console.print(
            f"\n[bold red]{n_fail} check(s) failed.[/bold red] "
            "Fix the issues above, then run [bold]nx doctor[/bold] again."
        )
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# nx token
# ---------------------------------------------------------------------------

@app.command()
def token() -> None:
    """Print the current session token path and first/last characters."""
    if not TOKEN_PATH.exists():
        console.print(
            f"[yellow]No token file at {TOKEN_PATH}[/]\n"
            "Start the daemon with [bold]nx run[/bold] to generate one."
        )
        raise typer.Exit(1)
    t = TOKEN_PATH.read_text().strip()
    # Show only first 4 and last 4 characters for safety
    masked = t[:4] + "..." + t[-4:] if len(t) > 8 else "***"
    console.print(f"Token file: [dim]{TOKEN_PATH}[/dim]")
    console.print(f"Token: [bold]{masked}[/bold] ({len(t)} chars)")


# ---------------------------------------------------------------------------
# Stubs for later milestones (honest error messages)
# ---------------------------------------------------------------------------

@app.command()
def open() -> None:
    """Open the NoX web UI (M7)."""
    console.print("[yellow]nx open[/] is implemented in Milestone 7 (API + auth).")
    raise typer.Exit(1)


@app.command(name="chat")
def chat_cmd(message: str = typer.Argument(..., help="Message to send.")) -> None:
    """Send a one-shot chat message (M3)."""
    console.print("[yellow]nx chat[/] is implemented in Milestone 3 (agent loop).")
    raise typer.Exit(1)


jobs_app = typer.Typer(help="Job management commands (M6).")
app.add_typer(jobs_app, name="jobs")


@jobs_app.command(name="list")
def jobs_list() -> None:
    """List all jobs (M6)."""
    console.print("[yellow]nx jobs list[/] is implemented in Milestone 6 (scheduler).")
    raise typer.Exit(1)


@jobs_app.command(name="run")
def jobs_run(name: str = typer.Argument(...)) -> None:
    """Run a job by name (M6)."""
    console.print("[yellow]nx jobs run[/] is implemented in Milestone 6 (scheduler).")
    raise typer.Exit(1)


memory_app = typer.Typer(help="Memory commands (M4).")
app.add_typer(memory_app, name="memory")


@memory_app.command(name="search")
def memory_search(query: str = typer.Argument(...)) -> None:
    """Search memory (M4)."""
    console.print("[yellow]nx memory search[/] is implemented in Milestone 4 (memory).")
    raise typer.Exit(1)


@app.command(name="eval-tools")
def eval_tools(
    models: list[str] = typer.Argument(..., help="Model names to evaluate."),
) -> None:
    """Run the tool-calling evaluation suite (M3)."""
    console.print("[yellow]nx eval-tools[/] is implemented in Milestone 3.")
    raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Doctor check helpers
# ---------------------------------------------------------------------------

def _check_ollama() -> tuple[bool, str, list[dict]]:
    """Check if Ollama is reachable and return its model list."""
    try:
        resp = httpx.get("http://127.0.0.1:11434/api/tags", timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        return True, "", data.get("models", [])
    except httpx.ConnectError:
        return False, "Start Ollama: sudo systemctl start ollama", []
    except Exception as exc:
        return False, f"Ollama error: {exc}", []


def _model_present(model_name: str, ollama_models: list[dict]) -> bool:
    """Return True if a model name appears in Ollama's model list."""
    names = {m.get("name", "") for m in ollama_models}
    # Match either exact or with :latest suffix
    return (
        model_name in names
        or f"{model_name}:latest" in names
        or any(n.startswith(model_name + ":") for n in names)
    )


def _check_sqlite_vec() -> tuple[bool, str]:
    """Return (ok, hint) for sqlite-vec availability."""
    try:
        import sqlite3

        import sqlite_vec  # type: ignore[import-untyped]
        conn = sqlite3.connect(":memory:")
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.close()
        return True, ""
    except Exception:
        try:
            import numpy  # noqa: F401
            return True, "sqlite-vec missing; numpy fallback is active (PASS with note)"
        except ImportError:
            return False, "Install numpy: pip install numpy (or install sqlite-vec)"


def _check_db() -> tuple[bool, str]:
    """Return (ok, hint) for DB migration status."""
    if not DB_PATH.exists():
        return False, "Run migrations: python -m noxai (or start the daemon)"
    try:
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute(
            "SELECT filename FROM schema_migrations ORDER BY id"
        ).fetchall()
        conn.close()
        n = len(rows)
        if n == 0:
            return False, "No migrations applied — start the daemon to apply them"
        return True, f"{n} migration(s) applied"
    except sqlite3.OperationalError:
        return False, "schema_migrations table missing — start the daemon"
    except Exception as exc:
        return False, f"DB error: {exc}"


def _check_secrets() -> tuple[bool, str]:
    """Return (ok, hint) for secrets file permissions."""
    if not SECRETS_FILE.exists():
        return True, "No secrets file (NVIDIA disabled — OK)"
    try:
        file_stat = SECRETS_FILE.stat()
        mode = stat.S_IMODE(file_stat.st_mode)
        if mode & 0o177:
            return False, f"chmod 600 {SECRETS_FILE}"
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _check_nvidia_key() -> bool:
    """Return True if an NVIDIA API key is configured in secrets."""
    if not SECRETS_FILE.exists():
        return False
    try:
        text = SECRETS_FILE.read_text()
        return "NVIDIA_API_KEY=" in text and "nvapi-" in text
    except Exception:
        return False


def _check_nvidia_reachable() -> tuple[bool, str]:
    """Check if the NVIDIA API endpoint responds."""
    try:
        # A quick HEAD to the base URL — we don't have a key-less endpoint
        # so we just check DNS/TCP connectivity to the host.
        httpx.head("https://integrate.api.nvidia.com", timeout=5.0)
        return True, ""
    except httpx.ConnectError:
        return False, "NVIDIA endpoint unreachable — check network / VPN"
    except Exception as exc:
        return True, f"Partial: {exc}"  # HTTP errors mean the host is up


def _port_free(host: str, port: int) -> bool:
    """Return True if the port is not in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        try:
            s.connect((host, port))
            return False  # something is listening
        except (TimeoutError, ConnectionRefusedError):
            return True


def _check_systemd_unit() -> tuple[bool, str]:
    """Check the status of the noxd.service systemd user unit."""
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-enabled", "noxd.service"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        state = result.stdout.strip()
        if state == "enabled":
            return True, ""
        elif state in ("disabled", "static"):
            return False, "Enable with: systemctl --user enable noxd.service"
        else:
            return False, f"Unit state: {state!r} — install systemd unit first"
    except FileNotFoundError:
        return False, "systemd not found — are you on a systemd system?"
    except Exception as exc:
        return False, str(exc)


def _check_gate() -> tuple[str, str]:
    """Return the configured gate mode."""
    try:
        from noxai.config import get_config
        cfg = get_config()
        mode = cfg.gate.mode
        return mode, ""
    except Exception:
        return "unknown", "Could not read config"
