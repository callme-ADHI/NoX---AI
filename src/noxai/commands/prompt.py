# Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
# commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
# Modified for NoX: CLI commands for briefing topic prompt versions and rollback safety net.

"""CLI commands for topic prompt version history and rollback (Workstream C)."""

from __future__ import annotations

import asyncio
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
import typer

from noxai.tracing.rewrite import (
    get_topic_prompt_history,
    improve_topic_prompt,
    rollback_topic_prompt,
)

prompt_app = typer.Typer(
    name="prompt",
    help="Manage briefing topic prompts, version history, and rollbacks.",
    no_args_is_help=True,
)
console = Console()


@prompt_app.command(name="history")
def prompt_history_cmd(
    topic: str = typer.Argument(..., help="Topic name or ID (e.g. 'ai', 'cybersecurity')"),
) -> None:
    """Show prompt version history for a briefing topic."""
    history = asyncio.run(get_topic_prompt_history(topic))
    if not history:
        console.print(f"[dim]No prompt versions found for topic '{topic}'.[/]")
        return

    table = Table(title=f"Prompt Version History for '{topic}'", header_style="bold magenta")
    table.add_column("Version", justify="center", style="bold")
    table.add_column("Status")
    table.add_column("Created By", style="dim")
    table.add_column("Created At", style="dim")
    table.add_column("Changelog")
    table.add_column("Prompt Text", max_width=60)

    for r in history:
        st = r.get("status", "")
        if st == "active":
            st_fmt = "[green bold]active[/green bold]"
        elif st == "reverted":
            st_fmt = "[yellow]reverted[/yellow]"
        else:
            st_fmt = f"[dim]{st}[/dim]"

        txt = str(r.get("text", "")).replace("\n", " ")
        if len(txt) > 80:
            txt = txt[:77] + "..."

        table.add_row(
            str(r.get("version")),
            st_fmt,
            str(r.get("created_by")),
            str(r.get("created_at", ""))[:19],
            str(r.get("changelog") or "-"),
            txt,
        )

    console.print(table)


@prompt_app.command(name="rollback")
def prompt_rollback_cmd(
    topic: str = typer.Argument(..., help="Topic name or ID"),
    version: int = typer.Argument(..., help="Target version number to reactivate"),
) -> None:
    """Roll back active prompt for a topic to an earlier version."""
    try:
        res = asyncio.run(rollback_topic_prompt(topic, version))
        console.print(f"[green]✓ Successfully rolled back prompt for '{topic}' to version {version}.[/green]")
        console.print(Panel(res["text"], title=f"Active Prompt (v{version})", border_style="green"))
    except KeyError as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(code=1)
    except Exception as exc:
        console.print(f"[red]Failed to rollback prompt:[/] {exc}")
        raise typer.Exit(code=1)


@prompt_app.command(name="improve")
def prompt_improve_cmd(
    topic: str = typer.Argument(..., help="Topic name or ID to optimize from feedback"),
) -> None:
    """Trigger AI rewrite of topic prompt based on unapplied dislike feedback."""
    try:
        res = asyncio.run(improve_topic_prompt(topic))
        console.print(f"[green]✓ Successfully generated new prompt version {res['version']} for '{topic}'![/green]")
        console.print(Panel(res["old_prompt"], title="Previous Prompt", border_style="dim"))
        console.print(Panel(res["new_prompt"], title=f"New Active Prompt (v{res['version']})", border_style="green"))
    except ValueError as exc:
        console.print(f"[yellow]Notice:[/] {exc}")
    except Exception as exc:
        console.print(f"[red]Failed to improve prompt:[/] {exc}")
        raise typer.Exit(code=1)
