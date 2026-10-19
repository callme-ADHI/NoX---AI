"""Job management commands for NoX CLI (spec §6.7, §10)."""

from __future__ import annotations

import asyncio
import json
import sqlite3

import typer
from rich.console import Console
from rich.table import Table

from noxai.db import DB_PATH, get_db, run_migrations
from noxai.scheduler.scheduler import JobScheduler
from noxai.scheduler.seeds import seed_database

jobs_app = typer.Typer(help="Job management commands (M6).")
console = Console()


@jobs_app.command(name="list")
def jobs_list() -> None:
    """List all scheduled jobs."""

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


@jobs_app.command(name="show")
def jobs_show(name: str = typer.Argument(..., help="Job name or ID.")) -> None:
    """Show detailed configuration of a scheduled job."""
    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}[/]")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM jobs WHERE name = ? OR id = ?", (name, name)).fetchone()
        if not row:
            console.print(f"[red]Job '{name}' not found.[/]")
            raise typer.Exit(1)

        console.rule(f"[bold cyan]Job: {row['name']} ({row['id']})[/bold cyan]")
        console.print(f"[bold]Description:[/]  {row['description']}")
        console.print(f"[bold]Schedule:[/]     {row['schedule_type']} ({row['schedule_expr']})")
        console.print(f"[bold]Timezone:[/]     {row['timezone']}")
        console.print(
            f"[bold]Enabled:[/]      {'[green]yes[/]' if row['enabled'] else '[red]no[/]'}"
        )
        console.print(f"[bold]Version:[/]      {row['version']}")
        console.print(f"[bold]Next Run:[/]     {row['next_run_at'] or '[dim]none[/]'}")
        console.print(f"[bold]Last Run:[/]     {row['last_run_at'] or '[dim]none[/]'}")
        console.print("\n[bold]Instruction:[/]")
        console.print(row["instruction"])
        if row["config_json"]:
            console.print("\n[bold]Config JSON:[/]")
            console.print(row["config_json"])
    finally:
        conn.close()


@jobs_app.command(name="enable")
def jobs_enable(name: str = typer.Argument(..., help="Job name or ID to enable.")) -> None:
    """Enable a scheduled job."""
    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}[/]")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.execute("UPDATE jobs SET enabled = 1 WHERE name = ? OR id = ?", (name, name))
        conn.commit()
        if cur.rowcount == 0:
            console.print(f"[yellow]Job '{name}' not found.[/yellow]")
        else:
            console.print(f"[green]✓[/] Job '{name}' enabled.")
    finally:
        conn.close()


@jobs_app.command(name="disable")
def jobs_disable(name: str = typer.Argument(..., help="Job name or ID to disable.")) -> None:
    """Disable a scheduled job."""
    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}[/]")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.execute("UPDATE jobs SET enabled = 0 WHERE name = ? OR id = ?", (name, name))
        conn.commit()
        if cur.rowcount == 0:
            console.print(f"[yellow]Job '{name}' not found.[/yellow]")
        else:
            console.print(f"[yellow]✓[/] Job '{name}' disabled.")
    finally:
        conn.close()


@jobs_app.command(name="history")
def jobs_history(
    name: str = typer.Argument(..., help="Job name or ID."),
    limit: int = typer.Option(10, "-n", "--limit", help="Max history runs to show."),
) -> None:
    """Show execution history for a job."""
    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}[/]")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        job = conn.execute(
            "SELECT id, name FROM jobs WHERE name = ? OR id = ?", (name, name)
        ).fetchone()
        if not job:
            console.print(f"[red]Job '{name}' not found.[/]")
            raise typer.Exit(1)

        rows = conn.execute(
            """
            SELECT id, started_at, finished_at, status, error
            FROM job_runs WHERE job_id = ? ORDER BY started_at DESC LIMIT ?
            """,
            (job["id"], limit),
        ).fetchall()

        if not rows:
            console.print(f"[dim]No execution history found for job '{job['name']}'.[/dim]")
            return

        table = Table(title=f"History for {job['name']} (ID: {job['id']})")
        table.add_column("Run ID", style="dim")
        table.add_column("Started At", style="cyan")
        table.add_column("Finished At", style="dim")
        table.add_column("Status", justify="center")
        table.add_column("Error", style="red")

        for r in rows:
            st = "[green]ok[/]" if r["status"] == "ok" else f"[red]{r['status']}[/]"
            table.add_row(
                r["id"], r["started_at"][:19], (r["finished_at"] or "")[:19], st, r["error"] or ""
            )
        console.print(table)
    finally:
        conn.close()


@jobs_app.command(name="restore")
def jobs_restore(
    name: str = typer.Argument(..., help="Job name or ID."),
    version: int = typer.Argument(..., help="Version number to restore."),
) -> None:
    """Restore a previous version of a job definition."""
    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}[/]")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        job = conn.execute(
            "SELECT id, name FROM jobs WHERE name = ? OR id = ?", (name, name)
        ).fetchone()
        if not job:
            console.print(f"[red]Job '{name}' not found.[/]")
            raise typer.Exit(1)

        v_row = conn.execute(
            "SELECT snapshot_json FROM job_versions WHERE job_id = ? AND version = ?",
            (job["id"], version),
        ).fetchone()

        if not v_row:
            console.print(f"[red]Version {version} not found for job '{job['name']}'.[/]")
            raise typer.Exit(1)

        snapshot = json.loads(v_row["snapshot_json"])
        conn.execute(
            """
            UPDATE jobs SET
                schedule_type = ?, schedule_expr = ?, timezone = ?,
                instruction = ?, config_json = ?, enabled = ?,
                version = version + 1, updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                snapshot["schedule_type"],
                snapshot["schedule_expr"],
                snapshot.get("timezone", "Asia/Kolkata"),
                snapshot["instruction"],
                snapshot.get("config_json", "{}"),
                snapshot.get("enabled", 1),
                job["id"],
            ),
        )
        conn.commit()
        console.print(
            f"[green]✓[/] Restored job '{job['name']}' to configuration from version {version}."
        )
    finally:
        conn.close()


@jobs_app.command(name="run")
def jobs_run(name: str = typer.Argument(..., help="Job name or ID to execute.")) -> None:
    """Run a job immediately by name or ID."""

    async def _run() -> None:
        await run_migrations()
        async with get_db() as db:
            await seed_database(db)
            cursor = await db.execute("SELECT * FROM jobs WHERE name = ? OR id = ?", (name, name))
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
