# Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
# commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
# Modified for NoX: Typer CLI sub-app for persistent schedule management and history.

"""CLI commands for persistent scheduler inspection and management (Section 6.2)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from rich.console import Console
from rich.table import Table
import typer

from noxai.scheduler.persistent import (
    DEFAULT_TIMEZONE,
    Schedule,
    ScheduleRun,
    get_persistent_scheduler,
)

schedule_app = typer.Typer(
    name="schedule",
    help="Manage persistent schedules, automated tasks, and execution history.",
    no_args_is_help=True,
)
console = Console()


def _format_dt(dt_str: str | None, tz_str: str = DEFAULT_TIMEZONE) -> str:
    """Format ISO datetime string in the target timezone for display."""
    if not dt_str:
        return "-"
    try:
        dt = datetime.fromisoformat(dt_str)
        target_tz = ZoneInfo(tz_str)
        dt_local = dt.astimezone(target_tz)
        return dt_local.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return dt_str[:19]


@schedule_app.command(name="list")
def list_schedules_cmd() -> None:
    """List all configured persistent schedules and their next scheduled run times."""
    scheduler = get_persistent_scheduler()
    schedules = asyncio.run(scheduler.list_schedules())

    if not schedules:
        console.print("[dim]No schedules configured.[/]")
        return

    table = Table(title="NoX Persistent Schedules", header_style="bold magenta")
    table.add_column("Name", style="bold")
    table.add_column("Kind", style="cyan")
    table.add_column("Expression")
    table.add_column("TZ", style="dim")
    table.add_column("Enabled", justify="center")
    table.add_column("Next Run (Local)", style="green")
    table.add_column("Last Run", style="dim")
    table.add_column("Status")
    table.add_column("Catch-up", style="dim")

    for s in schedules:
        en_str = "[green]yes[/green]" if s.enabled else "[dim]no[/dim]"
        st = s.last_status or "-"
        if st in ("success", "ok"):
            st_str = "[green]success[/green]"
        elif st in ("failed", "interrupted"):
            st_str = f"[red]{st}[/red]"
        elif st == "running":
            st_str = "[yellow]running[/yellow]"
        elif st == "missed":
            st_str = "[dim]missed[/dim]"
        else:
            st_str = st

        next_str = _format_dt(s.next_run_at, s.timezone) if s.enabled else "[dim]paused[/dim]"
        last_str = _format_dt(s.last_run_at, s.timezone)

        table.add_row(
            s.name,
            s.kind,
            s.expression,
            s.timezone,
            en_str,
            next_str,
            last_str,
            st_str,
            s.catchup_policy,
        )

    console.print(table)


@schedule_app.command(name="add")
def add_schedule_cmd(
    name: str = typer.Argument(..., help="Unique name for the schedule"),
    expression: str = typer.Argument(..., help="Cron expression (e.g. '0 4 * * *') or interval seconds ('120', '5m')"),
    kind: str = typer.Option("cron", "--kind", "-k", help="Schedule kind: 'cron' or 'interval'"),
    timezone: str = typer.Option(DEFAULT_TIMEZONE, "--tz", help="Timezone for cron evaluation"),
    catchup: str = typer.Option("run_once", "--catchup", "-c", help="Catch-up policy: run_once, skip, run_all"),
    action: str = typer.Option("generic", "--action", "-a", help="Target action handler name"),
    config: str | None = typer.Option(None, "--config", help="Optional JSON config payload"),
    disabled: bool = typer.Option(False, "--disabled", help="Create schedule in disabled state"),
) -> None:
    """Add a new persistent cron or interval schedule."""
    scheduler = get_persistent_scheduler()
    try:
        created = asyncio.run(
            scheduler.add_schedule(
                name=name,
                kind=kind,
                expression=expression,
                timezone=timezone,
                catchup_policy=catchup,
                target_action=action,
                config_json=config,
                enabled=not disabled,
            )
        )
        nxt_str = _format_dt(created.next_run_at, created.timezone)
        console.print(f"[bold green]✓[/] Schedule '[bold]{created.name}[/]' created.")
        console.print(f"  Next run scheduled at: [green]{nxt_str}[/] ({created.timezone})")
    except Exception as exc:
        console.print(f"[bold red]Failed to add schedule:[/] {exc}")
        raise typer.Exit(code=1)


@schedule_app.command(name="remove")
def remove_schedule_cmd(
    name: str = typer.Argument(..., help="Schedule name or ID to remove"),
) -> None:
    """Remove a schedule and cooperatively cancel any running execution."""
    scheduler = get_persistent_scheduler()
    ok = asyncio.run(scheduler.remove_schedule(name))
    if ok:
        console.print(f"[bold green]✓[/] Schedule '[bold]{name}[/]' removed.")
    else:
        console.print(f"[bold red]Schedule '{name}' not found.[/]")
        raise typer.Exit(code=1)


@schedule_app.command(name="enable")
def enable_schedule_cmd(
    name: str = typer.Argument(..., help="Schedule name or ID to enable"),
) -> None:
    """Enable a disabled schedule and compute its next run time."""
    scheduler = get_persistent_scheduler()
    ok = asyncio.run(scheduler.enable_schedule(name))
    if ok:
        sched = asyncio.run(scheduler.get_schedule(name))
        nxt_str = _format_dt(sched.next_run_at, sched.timezone) if sched else ""
        console.print(f"[bold green]✓[/] Schedule '[bold]{name}[/]' enabled.")
        console.print(f"  Next run: [green]{nxt_str}[/]")
    else:
        console.print(f"[bold red]Schedule '{name}' not found.[/]")
        raise typer.Exit(code=1)


@schedule_app.command(name="disable")
def disable_schedule_cmd(
    name: str = typer.Argument(..., help="Schedule name or ID to disable"),
) -> None:
    """Disable a schedule, preventing future automated firings."""
    scheduler = get_persistent_scheduler()
    ok = asyncio.run(scheduler.disable_schedule(name))
    if ok:
        console.print(f"[bold yellow]✓[/] Schedule '[bold]{name}[/]' disabled.")
    else:
        console.print(f"[bold red]Schedule '{name}' not found.[/]")
        raise typer.Exit(code=1)


@schedule_app.command(name="run")
def run_schedule_cmd(
    name: str = typer.Argument(..., help="Schedule name or ID to trigger immediately"),
) -> None:
    """Trigger an immediate run of a schedule in the daemon."""
    scheduler = get_persistent_scheduler()
    try:
        run_id = asyncio.run(scheduler.trigger_run(name))
        console.print(f"[bold green]✓[/] Triggered run for schedule '[bold]{name}[/]'.")
        console.print(f"  Run ID: [cyan]{run_id}[/] (executing in daemon)")
    except Exception as exc:
        console.print(f"[bold red]Failed to trigger schedule:[/] {exc}")
        raise typer.Exit(code=1)


@schedule_app.command(name="history")
def history_schedule_cmd(
    name: str = typer.Argument(..., help="Schedule name or ID"),
    limit: int = typer.Option(20, "--limit", "-n", help="Number of recent runs to show"),
) -> None:
    """Show execution run history for a schedule."""
    scheduler = get_persistent_scheduler()
    history = asyncio.run(scheduler.get_history(name, limit=limit))

    if not history:
        console.print(f"[dim]No execution history found for schedule '{name}'.[/]")
        return

    table = Table(title=f"Run History for '{name}'", header_style="bold cyan")
    table.add_column("Run ID", style="bold")
    table.add_column("Started At")
    table.add_column("Finished At")
    table.add_column("Status")
    table.add_column("Heartbeat")
    table.add_column("Error / Note", style="dim")

    for r in history:
        st = r.status
        if st in ("success", "ok"):
            st_str = "[green]success[/green]"
        elif st in ("failed", "interrupted"):
            st_str = f"[red]{st}[/red]"
        elif st == "running":
            st_str = "[yellow]running[/yellow]"
        elif st == "cancelled":
            st_str = "[dim]cancelled[/dim]"
        else:
            st_str = st

        start_str = _format_dt(r.started_at)
        finish_str = _format_dt(r.finished_at)
        hb_str = _format_dt(r.heartbeat_at)
        err_str = r.error or "-"

        table.add_row(r.id, start_str, finish_str, st_str, hb_str, err_str)

    console.print(table)
