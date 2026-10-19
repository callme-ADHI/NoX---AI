"""Briefings and digests archive commands for NoX CLI."""

from __future__ import annotations

import json
import sqlite3

import typer
from rich.console import Console
from rich.table import Table

from noxai.db import DB_PATH

briefings_app = typer.Typer(help="Browse briefings and digests.")
console = Console()


@briefings_app.command(name="list")
def briefings_list(
    limit: int = typer.Option(10, "-n", "--limit", help="Max briefings to display."),
) -> None:
    """List generated briefings and digests."""
    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}[/]")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, title, created_at, sources_json
            FROM outputs ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()

        if not rows:
            console.print("[dim]No briefings stored yet.[/dim]")
            return

        table = Table(title="Briefings & Digests Archive")
        table.add_column("ID", style="dim")
        table.add_column("Date", style="cyan")
        table.add_column("Title", style="bold")
        table.add_column("Sources", justify="right")

        for r in rows:
            src_count = 0
            try:
                src_count = len(json.loads(r["sources_json"] or "[]"))
            except (json.JSONDecodeError, TypeError):
                pass
            title_str = r["title"] or "[dim]Untitled[/]"
            table.add_row(str(r["id"]), r["created_at"][:19], title_str, str(src_count))
        console.print(table)
    finally:
        conn.close()


@briefings_app.command(name="show")
def briefings_show(
    briefing_id: int = typer.Argument(..., help="Briefing ID to view."),
) -> None:
    """Display the full content of a stored briefing."""
    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}[/]")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM outputs WHERE id = ?", (briefing_id,)).fetchone()
        if not row:
            console.print(f"[red]Briefing {briefing_id} not found.[/]")
            raise typer.Exit(1)

        console.rule(f"[bold cyan]{row['title']}[/bold cyan] ({row['created_at'][:19]})")
        console.print(row["markdown"])

        try:
            sources = json.loads(row["sources_json"] or "[]")
            if sources:
                console.print("\n[bold]Sources Cited:[/bold]")
                for s in sources:
                    u = s.get("url", "")
                    t = s.get("title", u)
                    console.print(f"  - [{t}]({u})")
        except (json.JSONDecodeError, TypeError):
            pass
    finally:
        conn.close()
