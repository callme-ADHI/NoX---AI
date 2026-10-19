"""Memory management commands for NoX CLI (spec §6.4, §10)."""

from __future__ import annotations

import asyncio
import sqlite3

import typer
from rich.console import Console
from rich.table import Table

from noxai.config import get_config
from noxai.db import DB_PATH, get_db, run_migrations
from noxai.memory.recall import recall_memories
from noxai.models.router import ModelRouter

memory_app = typer.Typer(help="Memory commands (M4).")
console = Console()


@memory_app.command(name="search")
def memory_search(
    query: str = typer.Argument(..., help="Query string to search memory."),
    top_k: int = typer.Option(10, "--top-k", "-k", help="Maximum results to return."),
) -> None:
    """Search long-term memory for facts and episodes matching a query."""

    async def _search() -> None:
        await run_migrations()
        cfg = get_config()
        router = ModelRouter(cfg)

        async with get_db() as db:
            result = await recall_memories(
                query=query,
                db=db,
                router=router,
                config=cfg,
                top_k=top_k,
            )

            if not result.facts and not result.episodes:
                console.print(f"[dim]No memories found matching '{query}'.[/dim]")
                return

            if result.facts:
                f_table = Table(title=f"Facts matching '{query}'", show_header=True)
                f_table.add_column("Key", style="bold")
                f_table.add_column("Category")
                f_table.add_column("Value")
                f_table.add_column("Pinned", justify="center")
                f_table.add_column("Confidence", justify="right")

                for f in result.facts:
                    pinned_badge = "[bold green]YES[/]" if f.pinned else "[dim]no[/]"
                    f_table.add_row(f.key, f.category, f.value, pinned_badge, f"{f.confidence:.2f}")
                console.print(f_table)

            if result.episodes:
                console.print("")
                e_table = Table(title=f"Episodes matching '{query}'", show_header=True)
                e_table.add_column("Date", style="dim")
                e_table.add_column("Summary")
                e_table.add_column("Importance", justify="right")

                for ep in result.episodes:
                    e_table.add_row(
                        ep.ts[:16] if len(ep.ts) >= 16 else ep.ts,
                        ep.summary,
                        f"{ep.importance:.2f}",
                    )
                console.print(e_table)

    asyncio.run(_search())


@memory_app.command(name="review")
def memory_review() -> None:
    """Review memory facts awaiting owner confirmation (needs_review=1)."""
    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}[/]")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, key, category, value, confidence, created_at
            FROM facts WHERE needs_review = 1 AND deleted_at IS NULL
            ORDER BY id ASC
            """
        ).fetchall()

        if not rows:
            console.print("[green]No facts require review.[/green]")
            return

        table = Table(title="Facts Awaiting Review")
        table.add_column("ID", style="dim")
        table.add_column("Key", style="bold cyan")
        table.add_column("Category")
        table.add_column("Value")
        table.add_column("Confidence", justify="right")

        for r in rows:
            table.add_row(
                str(r["id"]), r["key"], r["category"], r["value"], f"{r['confidence']:.2f}"
            )
        console.print(table)
        console.print(
            "\n[dim]Run 'nx memory confirm <id>' or 'nx memory reject <id>' to action.[/dim]"
        )
    finally:
        conn.close()


@memory_app.command(name="confirm")
def memory_confirm(
    fact_id: int = typer.Argument(..., help="Fact ID to confirm."),
) -> None:
    """Confirm a memory fact, clearing its needs_review flag."""
    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}[/]")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.execute("UPDATE facts SET needs_review = 0 WHERE id = ?", (fact_id,))
        conn.commit()
        if cur.rowcount == 0:
            console.print(f"[yellow]Fact {fact_id} not found.[/yellow]")
        else:
            console.print(f"[green]✓[/] Confirmed fact {fact_id}.")
    finally:
        conn.close()


@memory_app.command(name="reject")
def memory_reject(
    fact_id: int = typer.Argument(..., help="Fact ID to reject."),
) -> None:
    """Reject and soft-delete a memory fact."""
    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}[/]")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.execute("UPDATE facts SET deleted_at = datetime('now') WHERE id = ?", (fact_id,))
        conn.commit()
        if cur.rowcount == 0:
            console.print(f"[yellow]Fact {fact_id} not found.[/yellow]")
        else:
            console.print(f"[red]✓[/] Rejected and deleted fact {fact_id}.")
    finally:
        conn.close()
