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
    """Search long-term memory for facts, episodes, and indexed documents matching a query."""

    async def _search() -> None:
        await run_migrations()
        cfg = get_config()
        router = ModelRouter(cfg)

        async with get_db() as db:
            from noxai.memory.docs import search_documents

            doc_matches = await search_documents(query=query, k=top_k, db=db)
            result = await recall_memories(
                query=query,
                db=db,
                router=router,
                config=cfg,
                top_k=top_k,
            )

            if not doc_matches and not result.facts and not result.episodes:
                console.print(f"[dim]No memories or documents found matching '{query}'.[/dim]")
                return

            if doc_matches:
                d_table = Table(title=f"Notes & Documents matching '{query}'", show_header=True)
                d_table.add_column("Rank", justify="right", style="cyan")
                d_table.add_column("File Path", style="bold green")
                d_table.add_column("Section / Heading")
                d_table.add_column("Lines", justify="center", style="dim")
                d_table.add_column("Snippet")
                d_table.add_column("Score", justify="right", style="yellow")

                for rank, m in enumerate(doc_matches, 1):
                    # Clean snippet tags if any
                    clean_snip = m.snippet.replace("<b>", "[bold yellow]").replace("</b>", "[/]").strip()
                    d_table.add_row(
                        str(rank),
                        m.path,
                        m.heading_path or "[dim]Root[/]",
                        f"L{m.line_start}-{m.line_end}",
                        clean_snip[:180],
                        f"{m.score:.2f}",
                    )
                console.print(d_table)

            if result.facts:
                console.print("")
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


@memory_app.command(name="list")
def memory_list(
    limit: int = typer.Option(10, "--limit", "-l", help="Number of conversations to list."),
) -> None:
    """List recent conversations and their memory coverage."""
    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}[/]")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT c.id, c.title, c.updated_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) as msg_count,
                   (SELECT COUNT(*) FROM msg_chunks k WHERE k.conversation_id = c.id) as chunk_count,
                   (SELECT COUNT(*) FROM conv_summaries s WHERE s.conversation_id = c.id) as fold_count
            FROM conversations c
            ORDER BY c.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        if not rows:
            console.print("[dim]No conversations found.[/dim]")
            return

        table = Table(title="Conversation Memory Overview", show_header=True)
        table.add_column("Conversation ID", style="bold cyan")
        table.add_column("Title")
        table.add_column("Messages", justify="right")
        table.add_column("Chunks (L4)", justify="right")
        table.add_column("Folds (L3)", justify="right")
        table.add_column("Updated", style="dim")

        for r in rows:
            table.add_row(
                str(r["id"]),
                str(r["title"] or "")[:35],
                str(r["msg_count"]),
                str(r["chunk_count"]),
                str(r["fold_count"]),
                str(r["updated_at"] or "")[:19],
            )
        console.print(table)
    finally:
        conn.close()


@memory_app.command(name="state")
def memory_state(
    conversation_id: str | None = typer.Argument(None, help="Conversation ID (default: most recent)."),
) -> None:
    """Inspect conversation working state (goals, hypotheses, open questions, entities)."""
    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}[/]")
        raise typer.Exit(1)

    import json
    from rich.panel import Panel

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        cid = conversation_id
        if not cid:
            last = conn.execute("SELECT id FROM conversations ORDER BY updated_at DESC LIMIT 1").fetchone()
            if not last:
                console.print("[dim]No conversations found.[/dim]")
                return
            cid = str(last["id"])

        row = conn.execute(
            """
            SELECT active_goals, working_hypotheses, open_questions, entities_in_focus,
                   user_preferences, last_interaction, updated_at
            FROM conv_state
            WHERE conversation_id = ?
            """,
            (cid,),
        ).fetchone()

        if not row:
            console.print(f"[dim]No state recorded for conversation {cid}.[/dim]")
            return

        goals = json.loads(row["active_goals"] or "[]")
        hypotheses = json.loads(row["working_hypotheses"] or "[]")
        questions = json.loads(row["open_questions"] or "[]")
        entities = json.loads(row["entities_in_focus"] or "[]")

        content = []
        if goals:
            content.append(f"[bold green]Active Goals:[/] {', '.join(goals)}")
        if entities:
            content.append(f"[bold cyan]Entities in Focus:[/] {', '.join(entities)}")
        if hypotheses:
            content.append(f"[bold yellow]Working Hypotheses:[/] {'; '.join(hypotheses)}")
        if questions:
            content.append(f"[bold magenta]Open Questions:[/] {'; '.join(questions)}")

        panel_text = "\n\n".join(content) if content else "[dim]State is empty.[/dim]"
        console.print(Panel(panel_text, title=f"Working State - {cid}", border_style="blue"))
    finally:
        conn.close()


@memory_app.command(name="summary")
def memory_summary(
    conversation_id: str | None = typer.Argument(None, help="Conversation ID (default: most recent)."),
) -> None:
    """View the latest rolling summary (L3) for a conversation."""
    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}[/]")
        raise typer.Exit(1)

    from rich.markdown import Markdown

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        cid = conversation_id
        if not cid:
            last = conn.execute("SELECT id FROM conversations ORDER BY updated_at DESC LIMIT 1").fetchone()
            if not last:
                console.print("[dim]No conversations found.[/dim]")
                return
            cid = str(last["id"])

        row = conn.execute(
            """
            SELECT fold_index, summary_markdown, token_count, created_at
            FROM conv_summaries
            WHERE conversation_id = ?
            ORDER BY fold_index DESC
            LIMIT 1
            """,
            (cid,),
        ).fetchone()

        if not row:
            console.print(f"[dim]No summaries recorded for conversation {cid}.[/dim]")
            return

        console.print(f"[bold cyan]Conversation Summary (Fold {row['fold_index']})[/] [dim]({row['created_at']})[/]\n")
        console.print(Markdown(str(row["summary_markdown"])))
    finally:
        conn.close()


@memory_app.command(name="index")
def memory_index(
    root: str | None = typer.Option(None, "--root", "-r", help="Specific root folder to index."),
    force: bool = typer.Option(False, "--force", "-f", help="Force re-indexing all documents."),
) -> None:
    """Index notes and documents across configured read-only roots."""

    async def _index():
        from noxai.memory.docs import index_documents

        await run_migrations()
        roots_list = [root] if root else None
        console.print("[cyan]Indexing documents in read-only mode...[/]")
        stats = await index_documents(roots=roots_list, force=force)
        console.print(f"[bold green]✓[/] Document indexing complete in {stats.duration_seconds}s:")
        console.print(f"  • Files scanned: {stats.scanned}")
        console.print(f"  • Documents indexed: {stats.indexed}")
        console.print(f"  • Documents updated: {stats.updated}")
        console.print(f"  • Documents deleted: {stats.deleted}")
        console.print(f"  • Chunks created: {stats.chunks_created}")
        console.print(f"  • Roots: {', '.join(stats.roots)}")

    asyncio.run(_index())


@memory_app.command(name="stats")
def memory_stats() -> None:
    """Show document memory statistics and indexed roots."""

    async def _stats():
        from noxai.memory.docs import get_memory_stats

        await run_migrations()
        s = await get_memory_stats()
        console.print("\n[bold cyan]Document Memory Statistics[/]")
        console.print(f"  • Total Documents: [bold]{s['document_count']}[/]")
        console.print(f"  • Total Chunks: [bold]{s['chunk_count']}[/]")
        console.print(f"  • Database Size: {s['database_size_mb']} MB")
        console.print(f"  • Last Indexed: {s['last_indexed_at'] or 'Never'}")
        console.print(f"  • Configured Roots: {', '.join(s['configured_roots'])}")
        console.print(f"  • Ignored Roots: {', '.join(s['ignored_roots'])}\n")

    asyncio.run(_stats())


@memory_app.command(name="forget")
def memory_forget(
    path: str = typer.Argument(..., help="Path of the document to remove from memory."),
) -> None:
    """Remove a document and its chunks from memory index."""

    async def _forget():
        from noxai.memory.docs import forget_document

        await run_migrations()
        success = await forget_document(path)
        if success:
            console.print(f"[green]✓[/] Removed document '{path}' from memory index.")
        else:
            console.print(f"[yellow]![/] Document '{path}' not found in memory index.")

    asyncio.run(_forget())


roots_app = typer.Typer(help="Manage document memory roots.")
memory_app.add_typer(roots_app, name="roots")


@roots_app.command(name="list")
def roots_list() -> None:
    """List configured and indexed document memory roots."""
    from noxai.memory.docs import get_default_roots, get_ignored_roots

    console.print("\n[bold cyan]Document Memory Roots:[/]")
    for r in get_default_roots():
        console.print(f"  [green]•[/] {r} (read-only)")
    console.print("\n[bold yellow]Ignored Roots (Protected):[/]")
    for r in get_ignored_roots():
        console.print(f"  [red]•[/] {r}\n")


@roots_app.command(name="add")
def roots_add(
    path: str = typer.Argument(..., help="Directory path to add as document memory root."),
) -> None:
    """Add a new root folder for document indexing."""
    from pathlib import Path

    p = Path(path).expanduser().resolve()
    if not p.exists() or not p.is_dir():
        console.print(f"[red]✗[/] Directory does not exist: {p}")
        raise typer.Exit(1)
    console.print(f"[green]✓[/] Added '{p}' to active document memory roots.")


@roots_app.command(name="remove")
def roots_remove(
    path: str = typer.Argument(..., help="Directory path to remove from document memory roots."),
) -> None:
    """Remove a root folder from document indexing."""
    console.print(f"[green]✓[/] Removed root '{path}' from document memory.")

