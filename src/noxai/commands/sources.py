"""News and opportunity sources management commands for NoX CLI."""

from __future__ import annotations

import asyncio

import httpx
import typer
from rich.console import Console
from rich.table import Table

from noxai.db import get_db, run_migrations
from noxai.scheduler.seeds import seed_database

sources_app = typer.Typer(help="News and opportunity sources management.")
console = Console()


@sources_app.command(name="check")
def sources_check() -> None:
    """Fetch and parse all news and opportunity feeds, reporting dead ones."""

    async def _check_sources() -> None:
        await run_migrations()
        async with get_db() as db:
            await seed_database(db)
            rows = await db.execute_fetchall(
                "SELECT id, name, url, kind FROM sources ORDER BY id ASC"
            )
            if not rows:
                console.print("[dim]No sources configured in database.[/dim]")
                return

            table = Table(title="News & Opportunity Feeds Health Check")
            table.add_column("ID", style="dim")
            table.add_column("Name", style="bold cyan")
            table.add_column("Type", justify="center")
            table.add_column("Status", justify="center")
            table.add_column("URL", style="dim")

            dead_count = 0
            for r in rows:
                s_id, name, url, kind = r[0], r[1], r[2], r[3]
                try:
                    async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
                        resp = await client.get(url, headers={"User-Agent": "NoX/0.1.0"})
                        if resp.status_code == 200:
                            status_badge = "[green]OK (200)[/]"
                            await db.execute(
                                "UPDATE sources SET last_ok_at = datetime('now'), "
                                "last_error = NULL WHERE id = ?",
                                (s_id,),
                            )
                        else:
                            dead_count += 1
                            status_badge = f"[yellow]HTTP {resp.status_code}[/]"
                            await db.execute(
                                "UPDATE sources SET last_error = ? WHERE id = ?",
                                (f"HTTP {resp.status_code}", s_id),
                            )
                except Exception as exc:
                    dead_count += 1
                    status_badge = "[red]DEAD[/]"
                    await db.execute(
                        "UPDATE sources SET last_error = ? WHERE id = ?",
                        (str(exc)[:100], s_id),
                    )

                table.add_row(str(s_id), name, kind, status_badge, url[:50])

            await db.commit()
            console.print(table)
            if dead_count > 0:
                console.print(
                    f"\n[yellow]Report:[/] {dead_count} source(s) reported errors "
                    "or are unreachable."
                )
            else:
                console.print("\n[green]All sources are healthy and reachable.[/green]")

    asyncio.run(_check_sources())
