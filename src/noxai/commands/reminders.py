"""Reminders management commands for NoX CLI (spec §6.7, §10)."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

import typer
from rich.console import Console
from rich.table import Table

from noxai.db import DB_PATH
from noxai.scheduler.time_utils import format_human_time, parse_natural_time

reminders_app = typer.Typer(help="Manage reminders.")
console = Console()


def remind_command(
    text: str = typer.Argument(..., help="Reminder expression (e.g. 'in 2 minutes stretch')"),
) -> None:
    pattern = (
        r"^(in\s+\d+\s+(?:minutes?|hours?|days?|secs?|seconds?)"
        r"|at\s+\d{1,2}(?::\d{2})?(?:\s*[ap]m)?"
        r"|tomorrow(?:\s+at\s+\d{1,2}(?::\d{2})?(?:\s*[ap]m)?)?)"
        r"\s*(?:[:,]|\s+to\s+|\s+)\s*(.+)$"
    )
    m = re.match(pattern, text, re.IGNORECASE)

    dt: datetime | None = None
    msg: str = text
    if m:
        when_part = m.group(1).strip()
        msg = m.group(2).strip()
        dt = parse_natural_time(when_part)

    if dt is None:
        dt = parse_natural_time(text)
        if dt:
            msg = "Reminder"

    if dt is None:
        console.print(
            f"[bold red]Error:[/] Could not parse reminder time from '{text}'.\n"
            "Examples: 'in 2 minutes stretch', 'at 5pm drink water', 'tomorrow at 9am check mail'."
        )
        raise typer.Exit(1)

    rem_id = f"rem_{uuid.uuid4().hex[:8]}"
    iso_time = dt.isoformat()

    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(
            """
            INSERT INTO jobs (
                id, name, description, schedule_type, schedule_expr,
                instruction, config_json, enabled, created_by, next_run_at
            ) VALUES (?, ?, ?, 'once', ?, ?, ?, 1, 'owner', ?)
            """,
            (
                rem_id,
                f"remind-{rem_id}",
                f"Reminder: {msg}",
                iso_time,
                f"Send reminder notification: {msg}",
                json.dumps({"task_type": "reminder", "message": msg}),
                iso_time,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    time_str = format_human_time(dt)
    console.print(
        f"[green]✓[/] Reminder scheduled for [bold cyan]{time_str}[/] "
        f"({iso_time[:19]}): [bold]'{msg}'[/] (ID: {rem_id})"
    )


@reminders_app.command(name="list")
def reminders_list() -> None:
    """List pending reminders."""
    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}[/]")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, name, instruction, next_run_at, enabled, config_json
            FROM jobs
            WHERE schedule_type = 'once'
            ORDER BY next_run_at ASC
            """
        ).fetchall()

        if not rows:
            console.print("[dim]No pending reminders found.[/dim]")
            return

        table = Table(title="Reminders")
        table.add_column("ID", style="dim")
        table.add_column("When", style="cyan")
        table.add_column("Message", style="bold")
        table.add_column("Active", justify="center")

        for r in rows:
            msg = r["instruction"]
            try:
                cfg = json.loads(r["config_json"] or "{}")
                if "message" in cfg:
                    msg = cfg["message"]
            except (json.JSONDecodeError, TypeError):
                pass
            en_badge = "[green]YES[/]" if r["enabled"] else "[dim]cancelled[/]"
            table.add_row(r["id"], (r["next_run_at"] or "")[:19], msg, en_badge)
        console.print(table)
    finally:
        conn.close()


@reminders_app.command(name="cancel")
def reminders_cancel(
    reminder_id: str = typer.Argument(..., help="Reminder ID to cancel."),
) -> None:
    """Cancel a pending reminder."""
    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}[/]")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.execute("UPDATE jobs SET enabled = 0 WHERE id = ?", (reminder_id,))
        conn.commit()
        if cur.rowcount == 0:
            console.print(f"[yellow]Reminder '{reminder_id}' not found.[/yellow]")
        else:
            console.print(f"[red]✓[/] Reminder '{reminder_id}' cancelled.")
    finally:
        conn.close()


@reminders_app.command(name="snooze")
def reminders_snooze(
    reminder_id: str = typer.Argument(..., help="Reminder ID to snooze."),
    minutes: int = typer.Option(10, "--minutes", "-m", help="Minutes to snooze."),
) -> None:
    """Snooze a reminder by N minutes."""
    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}[/]")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    try:
        row = conn.execute("SELECT next_run_at FROM jobs WHERE id = ?", (reminder_id,)).fetchone()
        if not row:
            console.print(f"[yellow]Reminder '{reminder_id}' not found.[/yellow]")
            return

        new_dt = datetime.now(UTC) + timedelta(minutes=minutes)
        iso_new = new_dt.isoformat()
        conn.execute(
            "UPDATE jobs SET next_run_at = ?, enabled = 1 WHERE id = ?", (iso_new, reminder_id)
        )
        conn.commit()
        console.print(
            f"[green]✓[/] Snoozed reminder '{reminder_id}' for {minutes} "
            f"minutes (next: {iso_new[:19]})."
        )
    finally:
        conn.close()
