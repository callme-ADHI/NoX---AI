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

import os
import sqlite3
import stat
import subprocess
from datetime import UTC
from pathlib import Path
from typing import Any

import httpx
import typer
from rich.console import Console
from rich.table import Table

from noxai.commands.audit import audit_app
from noxai.commands.briefings import briefings_app
from noxai.commands.cloud import cloud_app
from noxai.commands.jobs import jobs_app
from noxai.commands.memory import memory_app
from noxai.commands.models import models_app
from noxai.commands.reminders import remind_command, reminders_app
from noxai.commands.service import service_app, session_env_command
from noxai.commands.sources import sources_app

app = typer.Typer(
    name="nx",
    help="NoX personal AI agent — command-line interface.",
    no_args_is_help=True,
)

console = Console()

app.add_typer(models_app, name="models")
app.add_typer(cloud_app, name="cloud")
app.add_typer(jobs_app, name="jobs")
app.add_typer(memory_app, name="memory")
app.add_typer(reminders_app, name="reminders")
app.add_typer(sources_app, name="sources")
app.add_typer(briefings_app, name="briefings")
app.add_typer(audit_app, name="audit")
app.add_typer(service_app, name="service")

bench_app = typer.Typer(name="bench", help="Benchmark latency and throughput commands.")
app.add_typer(bench_app, name="bench")

app.command(name="remind")(remind_command)
app.command(name="session-env")(session_env_command)

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
    test_db: str | None = typer.Option(
        None,
        "--test-db",
        help="Path to test database (only active when NOX_TEST_MODE=1).",
    ),
) -> None:
    """Start the NoX daemon in the foreground."""
    from noxai.config import SECRETS_FILE, get_config
    from noxai.lock import DaemonLock
    from noxai.logging_setup import setup_logging

    is_test_mode = os.environ.get("NOX_TEST_MODE") == "1" and bool(test_db)
    if test_db and os.environ.get("NOX_TEST_MODE") != "1":
        console.print(
            "[yellow]Warning:[/] --test-db ignored because NOX_TEST_MODE is not set to '1'."
        )
    elif os.environ.get("NOX_TEST_MODE") == "1" and not test_db:
        console.print(
            "[yellow]Warning:[/] NOX_TEST_MODE=1 ignored because --test-db flag was not provided."
        )

    # 1. Check secrets permissions before doing anything (skip in test mode if no secrets)
    if not is_test_mode and SECRETS_FILE.exists():
        mode = stat.S_IMODE(SECRETS_FILE.stat().st_mode)
        if mode & 0o177:
            console.print(
                f"[bold red]Error:[/] Secrets file {SECRETS_FILE} has unsafe permissions "
                f"({oct(mode)}). Expected 0600. Refusing to start daemon.\n"
                f"Fix with: [bold]chmod 600 {SECRETS_FILE}[/bold]"
            )
            raise typer.Exit(1) from None

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

        application = create_app(
            manage_lock=False,
            test_mode=is_test_mode,
            test_db=test_db if is_test_mode else None,
        )
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
def doctor(
    json_output: bool = typer.Option(False, "--json", "-j", help="Output doctor results as JSON."),
) -> None:
    """Run all health checks and print PASS/WARN/FAIL with fix hints."""
    from noxai.doctor import run_doctor

    try:
        run_doctor(console=console, json_output=json_output)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (1 if exc.code else 0)
        raise typer.Exit(code) from exc


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
    trace: bool = typer.Option(False, "--trace", help="Print detailed latency stage trace."),
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

            if trace and result.trace:
                from rich.table import Table

                t = Table(title="Latency Trace Breakdown", show_header=True)
                t.add_column("Stage", style="cyan")
                t.add_column("Duration (ms)", justify="right", style="green")

                t.add_row("route", f"{result.trace.get('route', 0.0):.1f}")
                t.add_row("memory_recall", f"{result.trace.get('memory_recall', 0.0):.1f}")
                t.add_row("prompt_build", f"{result.trace.get('prompt_build', 0.0):.1f}")
                t.add_row("provider_connect", f"{result.trace.get('provider_connect', 0.0):.1f}")
                t.add_row(
                    "time_to_first_token", f"{result.trace.get('time_to_first_token', 0.0):.1f}"
                )
                t.add_row("generation", f"{result.trace.get('generation', 0.0):.1f}")

                tool_times = result.trace.get("tool_time", {})
                for tool_name, ms in tool_times.items():
                    t.add_row(f"tool_time[{tool_name}]", f"{ms:.1f}")

                t.add_row(
                    "post_run_extraction", f"{result.trace.get('post_run_extraction', 0.0):.1f}"
                )
                t.add_row("[bold]total[/]", f"[bold]{result.trace.get('total', 0.0):.1f}[/]")
                t.add_row("prompt_tokens_est", str(result.trace.get("prompt_tokens_est", 0)))
                t.add_row("model_used", str(result.trace.get("model_used", "")))
                console.print()
                console.print(t)

    asyncio.run(_run())


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


@bench_app.command(name="chat")
def bench_chat_cmd(
    runs: int = typer.Option(3, "--runs", "-r", help="Number of benchmark runs per prompt."),
) -> None:
    """Benchmark chat latency across raw Ollama, NoX local, and NoX cloud paths."""
    import asyncio

    from noxai.commands.bench_chat import run_chat_bench

    asyncio.run(run_chat_bench(console=console, runs_per_case=runs))


@bench_app.command(name="cloud")
def bench_cloud_cmd(
    runs: int = typer.Option(3, "--runs", "-r", help="Number of benchmark runs per prompt."),
) -> None:
    """Benchmark cloud provider TTFT, throughput, and total latency across standard prompts."""
    from noxai.commands.models import models_bench_cloud

    models_bench_cloud(runs=runs)
