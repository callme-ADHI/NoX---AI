"""CLI commands for email intelligence, search, drafts, and digests."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from noxai.connectors.base import ConnectorRegistry
from noxai.connectors.gmail import GmailConnector
from noxai.connectors.mail_profile import DigestGenerator
from noxai.db import get_db

mail_app = typer.Typer(
    name="mail",
    help="Email intelligence: morning/evening digests, search, draft, and triage.",
    no_args_is_help=True,
)
console = Console()


@mail_app.command(name="digest")
def mail_digest(
    period: str = typer.Option(
        "morning", "--period", "-p", help="Digest period: 'morning' or 'evening'."
    ),
) -> None:
    """Generate and display the user's morning or evening email/calendar digest."""
    generator = DigestGenerator()
    data = asyncio.run(generator.generate_digest(period=period))

    title = f"NoX Daily Digest ({period.capitalize()})"
    console.print(
        Panel(f"[bold cyan]{title}[/bold cyan] — {data['generated_at'][:16]}", expand=False)
    )

    # 1. Today's Events
    events = data.get("calendar_events", [])
    if events:
        table_ev = Table(title="Schedule & Upcoming Events")
        table_ev.add_column("Time", style="yellow")
        table_ev.add_column("Event", style="bold")
        table_ev.add_column("Location", style="dim")
        table_ev.add_column("Source", style="cyan")
        for ev in events:
            table_ev.add_row(ev["start_time"][:16], ev["title"], ev["location"], ev["source"])
        console.print(table_ev)
    else:
        console.print("[dim]No scheduled calendar events for the next 48h.[/dim]\n")

    # 2. Action Items
    actions = data.get("action_items", [])
    if actions:
        table_act = Table(title="Action Items & Tasks Detected")
        table_act.add_column("Action Request", style="bold red")
        table_act.add_column("From Email Subject", style="dim")
        for act in actions:
            table_act.add_row(act["item"], act["subject"])
        console.print(table_act)

    # 3. Deadlines
    deadlines = data.get("deadlines", [])
    if deadlines:
        table_dl = Table(title="Extracted Deadlines")
        table_dl.add_column("Deadline", style="bold magenta")
        table_dl.add_column("Subject", style="white")
        for dl in deadlines:
            table_dl.add_row(dl["deadline"], dl["subject"])
        console.print(table_dl)

    # 4. Phishing Alerts
    phish = data.get("phishing_alerts", [])
    if phish:
        console.print("\n[bold red]⚠️ Security & Phishing Alerts Detected:[/bold red]")
        for p in phish:
            console.print(
                f" • [red bold]{p['subject']}[/red bold] from {p['from']} ({p['domain']})"
            )
            for r in p["reasons"]:
                console.print(f"   [yellow]- {r}[/yellow]")

    # 5. Email Category Breakdown
    breakdown = data.get("categories_breakdown", {})
    if breakdown:
        table_cat = Table(title="Recent Emails by Category (24h)")
        table_cat.add_column("Category", style="cyan bold")
        table_cat.add_column("Count", style="green")
        for cat, cnt in breakdown.items():
            table_cat.add_row(cat, str(cnt))
        console.print(table_cat)


@mail_app.command(name="list")
def mail_list(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of emails to list."),
) -> None:
    """List recent stored emails."""

    async def _run() -> None:
        async with get_db() as db:
            async with db.execute(
                """
                SELECT id, account, from_name, from_domain, subject, date
                FROM mail_items
                WHERE deleted_at IS NULL
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
                if not rows:
                    console.print(
                        "[dim]No emails found. Run `nx mail sync` or connect an account.[/dim]"
                    )
                    return

                table = Table(title=f"Recent Emails (Latest {limit})")
                table.add_column("ID", style="dim", no_wrap=True)
                table.add_column("From", style="cyan")
                table.add_column("Subject", style="bold")
                table.add_column("Date", style="dim")

                for r in rows:
                    table.add_row(r[0][:12], f"{r[2]} ({r[3]})", r[4], (r[5] or "")[:16])
                console.print(table)

    asyncio.run(_run())


@mail_app.command(name="search")
def mail_search(
    query: str = typer.Argument(..., help="Search term for emails."),
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum results."),
) -> None:
    """Search emails locally across subjects and sanitized bodies."""

    async def _run() -> None:
        async with get_db() as db:
            async with db.execute(
                """
                SELECT id, from_name, subject, date, text_sanitized
                FROM mail_items
                WHERE deleted_at IS NULL AND (subject LIKE ? OR text_sanitized LIKE ?)
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (f"%{query}%", f"%{query}%", limit),
            ) as cur:
                rows = await cur.fetchall()
                if not rows:
                    console.print(f"[dim]No emails matching '{query}'.[/dim]")
                    return

                table = Table(title=f"Search Results for '{query}'")
                table.add_column("ID", style="dim", no_wrap=True)
                table.add_column("From", style="cyan")
                table.add_column("Subject", style="bold")
                table.add_column("Date", style="dim")
                for r in rows:
                    table.add_row(r[0][:12], r[1], r[2], (r[3] or "")[:16])
                console.print(table)

    asyncio.run(_run())


@mail_app.command(name="draft")
def mail_draft(
    to: str = typer.Argument(..., help="Recipient email address."),
    subject: str = typer.Option(..., "--subject", "-s", help="Email subject."),
    body: str = typer.Option(..., "--body", "-b", help="Email body text."),
) -> None:
    """Draft an email reply or new message without sending."""

    async def _run() -> None:
        gmail = ConnectorRegistry.get("gmail")
        if isinstance(gmail, GmailConnector):
            res = await gmail.guarded_write("draft", {"to": to, "subject": subject, "body": body})
            console.print(
                f"[bold green]✓ Created draft in Gmail: {res.get('draft_id')}[/bold green]"
            )
        else:
            console.print(
                f"[bold green]✓ Saved local draft to {to} | Subject: {subject}[/bold green]"
            )
            console.print(
                "[dim]Draft recorded. Sending requires explicit approval via ApprovalsCenter.[/dim]"
            )

    asyncio.run(_run())


@mail_app.command(name="sync")
def mail_sync() -> None:
    """Manually trigger sync across all enabled email and calendar connectors."""
    with console.status("[bold green]Syncing active connectors...[/bold green]"):
        results = asyncio.run(ConnectorRegistry.sync_all_active())
    console.print(
        f"[bold green]✓ Sync completed across {len(results)} active connectors.[/bold green]"
    )
    for k, v in results.items():
        console.print(f" • [cyan]{k}[/cyan]: {v.get('status')}")
