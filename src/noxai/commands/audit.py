"""Security audit log inspection commands for NoX CLI (spec §6.5, §10)."""

from __future__ import annotations

import sqlite3

import typer
from rich.console import Console
from rich.table import Table

from noxai.db import DB_PATH

audit_app = typer.Typer(help="Security audit log management.")
console = Console()


@audit_app.command(name="tail")
def audit_tail(
    n: int = typer.Option(20, "-n", "--lines", help="Number of audit log entries to show."),
) -> None:
    """Show the most recent entries from the security audit log."""
    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}[/]")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, ts, tool, tier, decision, approver, ok, result_summary
            FROM audit_log ORDER BY id DESC LIMIT ?
            """,
            (n,),
        ).fetchall()

        if not rows:
            console.print("[dim]No audit log entries found.[/dim]")
            return

        table = Table(title=f"Audit Log (last {len(rows)} entries)")
        table.add_column("ID", style="dim")
        table.add_column("Timestamp", style="cyan")
        table.add_column("Tool", style="bold")
        table.add_column("Tier", justify="center")
        table.add_column("Decision", justify="center")
        table.add_column("Approver", style="dim")
        table.add_column("Status", justify="center")
        table.add_column("Summary", style="italic")

        for r in rows:
            status_badge = "[green]OK[/]" if r["ok"] else "[red]FAIL[/]"
            table.add_row(
                str(r["id"]),
                r["ts"][:19] if r["ts"] else "",
                r["tool"],
                r["tier"],
                r["decision"],
                r["approver"],
                status_badge,
                (r["result_summary"] or "")[:40],
            )
        console.print(table)
    finally:
        conn.close()
