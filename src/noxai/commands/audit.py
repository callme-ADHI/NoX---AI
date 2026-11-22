"""Security audit log inspection and verification commands for NoX CLI (spec §6.5, §10)."""

from __future__ import annotations

import sqlite3

import typer
from rich.console import Console
from rich.table import Table

from noxai.db import DB_PATH, get_db_path
from noxai.security.audit import get_recent_audit_events, verify_audit_chain_sync

audit_app = typer.Typer(help="Security audit log management and cryptographic verification.")
console = Console()


@audit_app.command(name="verify")
def audit_verify() -> None:
    """Verify cryptographic hash-chain integrity of the tamper-evident audit log."""
    db_path = get_db_path()
    if not db_path.exists():
        console.print(f"[red]Database not found at {db_path}[/]")
        raise typer.Exit(1)

    valid, _count, message = verify_audit_chain_sync()
    if valid:
        console.print(f"[bold green]✔ {message}[/bold green]")
    else:
        console.print(f"[bold red]✘ {message}[/bold red]")
        raise typer.Exit(2)


@audit_app.command(name="events")
def audit_events(
    n: int = typer.Option(20, "-n", "--lines", help="Number of audit events to show."),
) -> None:
    """Show recent tamper-evident events from audit_events."""
    import asyncio

    db_path = get_db_path()
    if not db_path.exists():
        console.print(f"[red]Database not found at {db_path}[/]")
        raise typer.Exit(1)

    events = asyncio.run(get_recent_audit_events(limit=n))
    if not events:
        console.print("[dim]No audit events found.[/dim]")
        return

    table = Table(title=f"Tamper-Evident Audit Events (last {len(events)})")
    table.add_column("ID", style="dim")
    table.add_column("Timestamp", style="cyan")
    table.add_column("Actor", style="bold")
    table.add_column("Action", style="yellow")
    table.add_column("Target", style="magenta")
    table.add_column("Decision", justify="center")
    table.add_column("Summary", style="italic")
    table.add_column("Hash (short)", style="dim")

    for e in events:
        decision = str(e.get("decision", ""))
        is_ok = decision in ("allowed", "approved", "executed")
        dec_badge = f"[green]{decision}[/]" if is_ok else f"[red]{decision}[/]"
        h = str(e.get("hash", ""))[:8]
        table.add_row(
            str(e.get("id")),
            str(e.get("ts", ""))[:19],
            str(e.get("actor", "")),
            str(e.get("action", "")),
            str(e.get("target", "")),
            dec_badge,
            str(e.get("summary_redacted", ""))[:35],
            h,
        )
    console.print(table)


@audit_app.command(name="tail")
def audit_tail(
    n: int = typer.Option(20, "-n", "--lines", help="Number of tool audit log entries to show."),
) -> None:
    """Show the most recent entries from the legacy tool audit log."""
    db_path = DB_PATH
    if not db_path.exists():
        console.print(f"[red]Database not found at {db_path}[/]")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(db_path))
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

        table = Table(title=f"Tool Audit Log (last {len(rows)} entries)")
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
