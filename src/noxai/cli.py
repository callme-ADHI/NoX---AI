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
import sqlite3
import stat
import subprocess
import sys
from datetime import UTC
from pathlib import Path
from typing import Any

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
    from noxai.config import SECRETS_FILE, get_config
    from noxai.lock import DaemonLock
    from noxai.logging_setup import setup_logging

    # 1. Check secrets permissions before doing anything
    if SECRETS_FILE.exists():
        mode = stat.S_IMODE(SECRETS_FILE.stat().st_mode)
        if mode & 0o177:
            console.print(
                f"[bold red]Error:[/] Secrets file {SECRETS_FILE} has unsafe permissions "
                f"({oct(mode)}). Expected 0600. Refusing to start daemon.\n"
                f"Fix with: [bold]chmod 600 {SECRETS_FILE}[/bold]"
            )
            raise typer.Exit(1)

    try:
        cfg = get_config()
        setup_logging(cfg.logging.level)
        _host = cfg.server.host
        _port = cfg.server.port
    except PermissionError as exc:
        console.print(f"[bold red]Error:[/] {exc}")
        raise typer.Exit(1) from None
    except Exception as exc:
        setup_logging("INFO")
        console.print(f"[yellow]Config warning:[/] {exc}")
        _host = host
        _port = port

    # 2. Acquire single-instance process lock
    daemon_lock = DaemonLock()
    try:
        daemon_lock.acquire()
    except RuntimeError as exc:
        console.print(f"[bold red]{exc}[/bold red]")
        raise typer.Exit(1) from None

    import uvicorn

    try:
        from noxai.api.app import create_app

        application = create_app(manage_lock=False)
    except ImportError:
        application = _create_minimal_app()

    try:
        uvicorn.run(
            application,
            host=_host,
            port=_port,
            reload=reload,
            log_config=None,  # we manage logging ourselves
        )
    finally:
        daemon_lock.release()


def _create_minimal_app() -> Any:
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
            f"[red]✗[/] Cannot reach daemon at {url}\n  Start it with: [bold]nx run[/bold]"
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
    checks.append(
        (
            f"Python ≥ 3.11  (found {major}.{minor})",
            py_ok,
            "Install Python 3.11+ — on Arch: pacman -S python",
        )
    )

    # 2. Ollama reachable
    ollama_ok, ollama_hint, ollama_data = _check_ollama()
    checks.append(("Ollama reachable at :11434", ollama_ok, ollama_hint))

    # 3. qwen2.5:14b model present
    qwen_ok = _model_present("qwen2.5:14b", ollama_data)
    checks.append(
        (
            "qwen2.5:14b model in Ollama",
            qwen_ok,
            "Pull it: ollama pull qwen2.5:14b",
        )
    )

    # 4. nomic-embed-text present
    embed_ok = _model_present("nomic-embed-text", ollama_data)
    checks.append(
        (
            "nomic-embed-text model in Ollama",
            embed_ok,
            "Pull it: ollama pull nomic-embed-text",
        )
    )

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
    checks.append(
        (
            "NVIDIA API key configured",
            key_ok,
            "Add NVIDIA_API_KEY=nvapi-... to ~/.config/nox/secrets.env then chmod 600",
        )
    )

    # 9. NVIDIA endpoint reachable (only if key present)
    if key_ok:
        nv_reach, nv_hint = _check_nvidia_reachable()
        checks.append(("NVIDIA API endpoint reachable", nv_reach, nv_hint))

    # 10. Server port free
    try:
        from noxai.config import get_config

        cfg = get_config()
        srv_host = cfg.server.host
        srv_port = cfg.server.port
    except Exception:
        srv_host = "127.0.0.1"
        srv_port = 7777
    port_ok = _port_free(srv_host, srv_port)
    checks.append(
        (
            f"Port {srv_port} free (daemon not yet running)",
            port_ok,
            f"Stop any process using port {srv_port}: ss -tlnp | grep {srv_port}",
        )
    )

    # 11. notify-send present
    ns_ok = shutil.which("notify-send") is not None
    checks.append(
        (
            "notify-send in PATH",
            ns_ok,
            "Install libnotify: sudo pacman -S libnotify",
        )
    )

    # 12. systemd user unit
    unit_ok, unit_hint = _check_systemd_unit()
    checks.append(("systemd user unit noxd.service", unit_ok, unit_hint))

    # 13. Gate mode
    gate_mode, gate_hint = _check_gate()
    checks.append((f"Gate mode: {gate_mode}", True, gate_hint))  # always pass, just info

    # 14. UI build present
    ui_ok = UI_BUILD.exists()
    checks.append(
        (
            "UI build present (ui/dist/index.html)",
            ui_ok,
            "Build UI: cd ui && npm install && npm run build",
        )
    )

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
# Client Commands
# ---------------------------------------------------------------------------


@app.command()
def open() -> None:
    """Open the NoX web UI."""
    import subprocess
    import webbrowser

    from noxai.api.auth import generate_session_token, load_session_token
    from noxai.config import get_config

    cfg = get_config()
    token = load_session_token()
    if not token:
        token = generate_session_token()

    port = cfg.server.port
    host = cfg.server.host
    url = f"http://{host}:{port}/?t={token}"

    launcher_cmd = getattr(cfg.ui, "launcher_cmd", ["chromium", "--app={url}"])
    cmd = [c.replace("{url}", url) for c in launcher_cmd]

    console.print(f"[bold green]Opening NoX Web UI:[/] {url}")
    try:
        subprocess.Popen(cmd)
    except Exception:
        webbrowser.open(url)


@app.command(name="chat")
def chat_cmd(
    message: str = typer.Argument(..., help="Message to send to NoX."),
    allow_cloud: bool = typer.Option(False, "--allow-cloud", help="Allow cloud model if eligible."),
) -> None:
    """Send a one-shot chat message to NoX."""
    import asyncio

    from noxai.agent.loop import AgentLoop
    from noxai.config import get_config
    from noxai.db import get_db, run_migrations
    from noxai.events import global_event_bus
    from noxai.models.router import ModelRouter
    from noxai.tools.registry import default_registry

    async def _run() -> None:
        cfg = get_config()
        await run_migrations()
        router = ModelRouter(config=cfg)

        async with get_db() as db:
            loop = AgentLoop(
                config=cfg,
                router=router,
                registry=default_registry,
                event_bus=global_event_bus,
            )

            console.print(f"[bold cyan]You:[/] {message}\n")
            console.print("[bold green]NoX:[/] ", end="")

            def on_token(t: str) -> None:
                console.print(t, end="")

            result = await loop.run(
                user_message=message,
                allow_cloud=allow_cloud,
                db=db,
                on_token=on_token,
            )

            console.print("\n")
            if result.citations:
                console.print("\n[bold dim]Sources / Citations:[/]")
                for c in result.citations:
                    console.print(f"  [dim]- {c}[/]")
            if result.tool_calls_count > 0:
                console.print(
                    f"\n[dim]({result.tool_calls_count} tool call(s), "
                    f"{result.steps} step(s), {result.duration_seconds:.2f}s)[/dim]"
                )

    asyncio.run(_run())


jobs_app = typer.Typer(help="Job management commands (M6).")
app.add_typer(jobs_app, name="jobs")


@jobs_app.command(name="list")
def jobs_list() -> None:
    """List all scheduled jobs."""
    import asyncio

    from rich.table import Table

    from noxai.db import get_db, run_migrations
    from noxai.scheduler.seeds import seed_database

    async def _list() -> None:
        await run_migrations()
        async with get_db() as db:
            await seed_database(db)
            rows = await db.execute_fetchall(
                "SELECT id, name, schedule_type, schedule_expr, timezone, enabled, next_run_at "
                "FROM jobs ORDER BY name ASC"
            )
            if not rows:
                console.print("[dim]No jobs found.[/dim]")
                return

            table = Table(title="NoX Scheduled Jobs")
            table.add_column("ID", style="dim")
            table.add_column("Name", style="bold cyan")
            table.add_column("Schedule", style="green")
            table.add_column("Timezone", style="dim")
            table.add_column("Enabled", style="bold")
            table.add_column("Next Run", style="yellow")

            for r in rows:
                en_str = "[green]yes[/]" if r[5] else "[red]no[/]"
                next_str = str(r[6]) if r[6] else "[dim]none[/]"
                table.add_row(
                    str(r[0]),
                    str(r[1]),
                    f"{r[2]}: {r[3]}",
                    str(r[4] or "local"),
                    en_str,
                    next_str,
                )
            console.print(table)

    asyncio.run(_list())


@jobs_app.command(name="run")
def jobs_run(name: str = typer.Argument(..., help="Job name or ID to execute.")) -> None:
    """Run a job immediately by name or ID."""
    import asyncio

    from noxai.db import get_db, run_migrations
    from noxai.scheduler.scheduler import JobScheduler
    from noxai.scheduler.seeds import seed_database

    async def _run() -> None:
        await run_migrations()
        async with get_db() as db:
            await seed_database(db)
            cursor = await db.execute(
                "SELECT * FROM jobs WHERE name = ? OR id = ?",
                (name, name),
            )
            row = await cursor.fetchone()
            if not row:
                console.print(f"[red]Error:[/] Job '{name}' not found.")
                raise typer.Exit(1)

            col_names = [d[0] for d in cursor.description]
            job_dict = dict(zip(col_names, row, strict=False))

            console.print(f"[bold cyan]Running job:[/] {job_dict['name']} ({job_dict['id']})...")
            scheduler = JobScheduler()
            res = await scheduler.run_job(job_dict, db, run_context="cli")
            status = res.get("status", "unknown")
            if status == "ok":
                console.print("[green]Job completed successfully![/]")
                if res.get("output"):
                    console.print(f"\n[dim]--- Output ---[/dim]\n{res['output'][:500]}")
            else:
                console.print(f"[red]Job failed with status: {status}[/]")
                if res.get("error"):
                    console.print(f"[red]Error:[/] {res['error']}")
                raise typer.Exit(1)

    asyncio.run(_run())


memory_app = typer.Typer(help="Memory commands (M4).")
app.add_typer(memory_app, name="memory")


@memory_app.command(name="search")
def memory_search(
    query: str = typer.Argument(..., help="Query string to search memory."),
    top_k: int = typer.Option(10, "--top-k", "-k", help="Maximum results to return."),
) -> None:
    """Search long-term memory for facts and episodes matching a query."""
    import asyncio

    from rich.table import Table

    from noxai.config import get_config
    from noxai.db import get_db, run_migrations
    from noxai.memory.recall import recall_memories
    from noxai.models.router import ModelRouter

    async def _search() -> None:
        await run_migrations()
        cfg = get_config()
        router = ModelRouter(cfg)

        async with get_db() as db:
            result = await recall_memories(
                query=query,
                db=db,
                router=router,
                config=cfg,
                top_k=top_k,
            )

            if not result.facts and not result.episodes:
                console.print(f"[dim]No memories found matching '{query}'.[/dim]")
                return

            if result.facts:
                f_table = Table(
                    title=f"Facts matching '{query}'",
                    show_header=True,
                    header_style="bold cyan",
                )
                f_table.add_column("Key", style="bold")
                f_table.add_column("Category")
                f_table.add_column("Value")
                f_table.add_column("Pinned", justify="center")
                f_table.add_column("Confidence", justify="right")

                for f in result.facts:
                    pinned_badge = "[bold green]YES[/]" if f.pinned else "[dim]no[/]"
                    f_table.add_row(
                        f.key,
                        f.category,
                        f.value,
                        pinned_badge,
                        f"{f.confidence:.2f}",
                    )
                console.print(f_table)

            if result.episodes:
                console.print("")
                e_table = Table(
                    title=f"Episodes matching '{query}'",
                    show_header=True,
                    header_style="bold magenta",
                )
                e_table.add_column("Date", style="dim")
                e_table.add_column("Summary")
                e_table.add_column("Importance", justify="right")

                for ep in result.episodes:
                    e_table.add_row(
                        ep.ts[:16] if len(ep.ts) >= 16 else ep.ts,
                        ep.summary,
                        f"{ep.importance:.2f}",
                    )
                console.print(e_table)

    asyncio.run(_search())


@app.command(name="eval-tools")
def eval_tools(
    models: list[str] = typer.Argument(..., help="Model names to evaluate."),
) -> None:
    """Run the tool-calling evaluation suite (M3)."""
    import asyncio

    from noxai.agent.eval import run_tool_eval

    for m in models:
        asyncio.run(run_tool_eval(m, console=console))


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
        rows = conn.execute("SELECT filename FROM schema_migrations ORDER BY id").fetchall()
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


# ---------------------------------------------------------------------------
# nx tools (approvals and tool management)
# ---------------------------------------------------------------------------

tools_app = typer.Typer(
    name="tools",
    help="Manage tools, approvals, and execution policies.",
    no_args_is_help=True,
)
app.add_typer(tools_app, name="tools")


@tools_app.command(name="approve")
def tools_approve(
    approval_id: int = typer.Argument(..., help="ID of the pending approval to approve"),
) -> None:
    """Approve a pending tool execution by ID."""
    from datetime import datetime

    if not DB_PATH.exists():
        console.print(f"[red]✗[/] Database not found at {DB_PATH}")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if not row:
            console.print(f"[red]✗[/] Approval {approval_id} not found.")
            raise typer.Exit(1)
        if row["status"] != "pending":
            console.print(f"[yellow]![/] Approval {approval_id} is already '{row['status']}'.")
            return
        now_iso = datetime.now(UTC).isoformat()
        if row["expires_at"] and row["expires_at"] < now_iso:
            conn.execute("UPDATE approvals SET status = 'expired' WHERE id = ?", (approval_id,))
            conn.commit()
            console.print(f"[red]✗[/] Approval {approval_id} has expired.")
            raise typer.Exit(1)

        conn.execute(
            "UPDATE approvals SET status = 'approved', decided_at = ?, decided_by = 'owner' "
            "WHERE id = ?",
            (now_iso, approval_id),
        )
        conn.commit()
        console.print(f"[green]✓[/] Approved tool execution {approval_id} ({row['tool_name']})")
    finally:
        conn.close()


@tools_app.command(name="deny")
def tools_deny(
    approval_id: int = typer.Argument(..., help="ID of the pending approval to deny"),
) -> None:
    """Deny a pending tool execution by ID."""
    from datetime import datetime

    if not DB_PATH.exists():
        console.print(f"[red]✗[/] Database not found at {DB_PATH}")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if not row:
            console.print(f"[red]✗[/] Approval {approval_id} not found.")
            raise typer.Exit(1)
        if row["status"] != "pending":
            console.print(f"[yellow]![/] Approval {approval_id} is already '{row['status']}'.")
            return
        now_iso = datetime.now(UTC).isoformat()
        conn.execute(
            "UPDATE approvals SET status = 'denied', decided_at = ?, decided_by = 'owner' "
            "WHERE id = ?",
            (now_iso, approval_id),
        )
        conn.commit()
        console.print(f"[red]✗[/] Denied tool execution {approval_id} ({row['tool_name']})")
    finally:
        conn.close()


@tools_app.command(name="list")
def tools_list() -> None:
    """List pending tool execution approvals."""
    if not DB_PATH.exists():
        console.print(f"[red]✗[/] Database not found at {DB_PATH}")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, tool_name, args_hash, reason, requested_at, expires_at, status "
            "FROM approvals WHERE status = 'pending' ORDER BY id ASC"
        ).fetchall()
        if not rows:
            console.print("[dim]No pending tool approvals.[/dim]")
            return

        table = Table(title="Pending Tool Approvals")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Tool", style="bold")
        table.add_column("Hash", style="dim")
        table.add_column("Reason", style="yellow")
        table.add_column("Expires", style="dim")

        for r in rows:
            h = (r["args_hash"] or "")[:8]
            table.add_row(
                str(r["id"]),
                r["tool_name"],
                h,
                r["reason"] or "",
                r["expires_at"] or "",
            )
        console.print(table)
    finally:
        conn.close()
