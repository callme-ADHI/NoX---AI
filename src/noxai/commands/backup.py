"""CLI commands for database backup and restore (Session K, A13)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from noxai.db import backup_db, get_backup_dir, get_db_path, list_backups, restore_db

backup_app = typer.Typer(help="Manage database backups and restore points.")
console = Console()


@backup_app.callback(invoke_without_command=True)
def backup_default(ctx: typer.Context) -> None:
    """Run an on-demand database backup if no subcommand specified."""
    if ctx.invoked_subcommand is None:
        do_backup()


@backup_app.command(name="create")
def do_backup() -> None:
    """Create a new timestamped database backup."""
    db_path = get_db_path()
    if not db_path.exists():
        console.print(f"[red]Database not found at {db_path}[/red]")
        raise typer.Exit(1)

    backup_path = backup_db(db_path, keep=10)
    if not backup_path:
        console.print("[red]Backup failed: database is empty or inaccessible.[/red]")
        raise typer.Exit(1)

    size_kb = backup_path.stat().st_size / 1024
    console.print(
        f"[green]✓ Database backup created successfully:[/green] {backup_path} ({size_kb:.1f} KB)"
    )


@backup_app.command(name="list")
def list_backups_cmd() -> None:
    """List available database backups."""
    backups = list_backups()
    if not backups:
        console.print(f"[dim]No backups found in {get_backup_dir()}[/dim]")
        return

    table = Table(title="NoX Database Backups (Last 10 Kept)", show_header=True)
    table.add_column("Filename", style="cyan")
    table.add_column("Size", style="green")
    table.add_column("Created At", style="white")
    table.add_column("Path", style="dim")

    from datetime import datetime

    for b in backups:
        mtime = datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        size_kb = f"{b.stat().st_size / 1024:.1f} KB"
        table.add_row(b.name, size_kb, mtime, str(b))

    console.print(table)


@backup_app.command(name="restore")
def restore_backup(
    file_path: Annotated[Path, typer.Argument(help="Path to backup .db file to restore from")],
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Bypass confirmation prompt")
    ] = False,
) -> None:
    """Restore database from a backup file (creates safety backup first)."""
    if not file_path.exists():
        # Check if user passed just the filename
        candidate = get_backup_dir() / file_path.name
        if candidate.exists():
            file_path = candidate
        else:
            console.print(f"[red]Backup file not found: {file_path}[/red]")
            raise typer.Exit(1)

    if not force:
        confirm = typer.confirm(
            f"Are you sure you want to restore {get_db_path()} from {file_path}?\n"
            "A safety backup of the current database will be saved first."
        )
        if not confirm:
            console.print("[yellow]Restore aborted.[/yellow]")
            return

    try:
        restored = restore_db(file_path)
        console.print(f"[bold green]✓ Database successfully restored from {file_path}[/bold green]")
        console.print(f"[dim]Target: {restored}[/dim]")
    except Exception as exc:
        console.print(f"[bold red]Failed to restore database: {exc}[/bold red]")
        raise typer.Exit(1) from exc
