"""CLI commands for public syndicated feeds (RSS, Bluesky, Mastodon, Reddit)."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from noxai.connectors.feeds import FeedsConnector
from noxai.db import get_db

feeds_app = typer.Typer(
    name="feeds",
    help="Public feeds management: RSS/Atom, Bluesky, Mastodon, and Reddit.",
    no_args_is_help=True,
)
console = Console()


@feeds_app.command(name="list")
def feeds_list() -> None:
    """List configured feeds and current social items count."""
    async def _run() -> None:
        async with get_db() as db:
            async with db.execute(
                """
                SELECT source, count(*), max(published_at)
                FROM social_items
                WHERE deleted_at IS NULL
                GROUP BY source
                """
            ) as cur:
                rows = await cur.fetchall()
                if not rows:
                    console.print(
                        "[dim]No social items found. Run `nx feeds sync` or add feeds.[/dim]"
                    )
                    return

                table = Table(title="Stored Feed Items by Source")
                table.add_column("Source", style="cyan bold")
                table.add_column("Items Count", style="green")
                table.add_column("Latest Published", style="dim")

                for r in rows:
                    table.add_row(r[0], str(r[1]), (r[2] or "")[:16])
                console.print(table)

    asyncio.run(_run())


@feeds_app.command(name="digest")
def feeds_digest(
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum items to display."),
) -> None:
    """Display recent social and syndicated feed digest."""
    async def _run() -> None:
        conn = FeedsConnector()
        data = await conn.generate_social_digest(limit=limit)

        if not data["total_items"]:
            console.print("[dim]No social feed items available. Run `nx feeds sync` first.[/dim]")
            return

        table = Table(title=f"Social Digest ({data['total_items']} items)")
        table.add_column("Source", style="cyan bold")
        table.add_column("Feed / Channel", style="magenta")
        table.add_column("Author", style="yellow")
        table.add_column("Content / Title", style="white")
        table.add_column("Time", style="dim")

        for _src, items in data.get("items_by_source", {}).items():
            for it in items:
                title_or_content = it.get("title") or it.get("content", "")
                table.add_row(
                    it["source"],
                    it["channel_or_feed"],
                    it["author"],
                    title_or_content[:70] + ("..." if len(title_or_content) > 70 else ""),
                    (it["published_at"] or "")[:16],
                )
        console.print(table)

    asyncio.run(_run())


@feeds_app.command(name="sync")
def feeds_sync() -> None:
    """Sync all configured RSS, Bluesky, Mastodon, and Reddit feeds."""
    async def _run() -> None:
        conn = FeedsConnector()
        with console.status("[bold green]Syncing public feeds...[/bold green]"):
            res = await conn.sync()
        count = res.get("saved_count", 0)
        console.print(f"[bold green]✓ Feeds synchronized: {count} items stored.[/bold green]")

    asyncio.run(_run())
