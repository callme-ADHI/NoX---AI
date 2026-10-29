"""CLI commands for NoX Briefing Engine (Phase 4 & 5)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from noxai.briefing.engine import BriefingPipeline
from noxai.config import get_config
from noxai.db import fetch_all, fetch_one, get_db

briefing_app = typer.Typer(help="Manage, trigger, and inspect daily intelligence briefings.")
console = Console()
log = logging.getLogger(__name__)


@briefing_app.command(name="run")
def briefing_run(
    slot: str = typer.Option("morning", "--slot", help="Briefing slot: 'morning' or 'evening'"),
    force: bool = typer.Option(False, "--force", help="Force run even if already completed today"),
) -> None:
    """Execute automated briefing pipeline for the specified slot."""
    cfg = get_config()
    pipeline = BriefingPipeline(config=cfg)

    console.print(
        f"[bold cyan]Running NoX Briefing Pipeline (slot={slot}, force={force})...[/bold cyan]"
    )
    t0 = datetime.now()
    try:
        run_id = asyncio.run(pipeline.execute_run(slot=slot, force=force))
        elapsed = (datetime.now() - t0).total_seconds()
        console.print(f"[green]SUCCESS: Briefing run completed: {run_id} ({elapsed:.1f}s)[/green]")
    except Exception as exc:
        console.print(f"[red]FAILED: Briefing run failed: {exc}[/red]")
        raise typer.Exit(code=1) from exc


@briefing_app.command(name="list")
def briefing_list(
    limit: int = typer.Option(10, "--limit", "-n", help="Max runs to display"),
) -> None:
    """List recent briefing runs from the database."""

    async def _fetch() -> Any:
        async with get_db() as db:
            return await fetch_all(
                db,
                "SELECT * FROM briefing_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )

    rows = asyncio.run(_fetch())
    if not rows:
        console.print("[dim]No briefings generated yet.[/dim]")
        return

    table = Table(title="NoX Intelligence Briefings Archive")
    table.add_column("Run ID", style="cyan")
    table.add_column("Date", style="white")
    table.add_column("Slot", style="magenta")
    table.add_column("State", style="bold")
    table.add_column("Sources", justify="right")
    table.add_column("Time (s)", justify="right")
    table.add_column("Markdown Path", style="dim")

    for r in rows:
        st_color = (
            "green"
            if r["state"] == "succeeded"
            else ("yellow" if r["state"] == "running" else "red")
        )
        table.add_row(
            str(r["id"]),
            str(r["date"]),
            str(r["slot"]),
            f"[{st_color}]{r['state']}[/{st_color}]",
            str(r["sources_count"]),
            f"{float(r['generation_seconds'] or 0.0):.1f}",
            str(r["markdown_path"] or ""),
        )

    console.print(table)


@briefing_app.command(name="show")
def briefing_show(
    briefing_id: str = typer.Argument(..., help="Briefing ID or date (YYYY-MM-DD-slot)"),
) -> None:
    """Display the full markdown briefing."""

    async def _fetch() -> Any:
        async with get_db() as db:
            return await fetch_one(
                db,
                "SELECT * FROM briefing_runs WHERE id = ? OR date = ?",
                (briefing_id, briefing_id),
            )

    row = asyncio.run(_fetch())
    if not row or not row["markdown_path"]:
        console.print(f"[red]Briefing {briefing_id} not found.[/red]")
        return

    md_path = Path(str(row["markdown_path"]))
    if md_path.exists():
        console.print(md_path.read_text(encoding="utf-8"))
    else:
        console.print(f"[red]Markdown file not found at {md_path}[/red]")
