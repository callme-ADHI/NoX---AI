"""CLI commands for managing configuration presets (`nx preset ...`)."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from noxai.agents.presets import apply_preset, get_current_preset, list_presets

preset_app = typer.Typer(
    name="preset",
    help="Apply and manage NoX operational presets (morning-digest, deep-research, etc.).",
    no_args_is_help=True,
)
console = Console()


@preset_app.command(name="list")
def preset_list() -> None:
    """List all available operational presets."""
    async def _run() -> None:
        current = await get_current_preset()
        presets = list_presets()

        table = Table(title="Available System Presets")
        table.add_column("Preset", style="cyan bold")
        table.add_column("Active", style="bold")
        table.add_column("Target Agents", style="magenta")
        table.add_column("Connectors", style="yellow")
        table.add_column("Description", style="dim")

        for p in presets:
            is_active = p["name"] == current
            active_badge = "[bold green]YES[/bold green]" if is_active else "[dim]no[/dim]"
            table.add_row(
                p["name"],
                active_badge,
                ", ".join(p["active_agents"]),
                ", ".join(p.get("active_connectors", [])) or "none",
                p["description"],
            )
        console.print(table)

    asyncio.run(_run())


@preset_app.command(name="apply")
def preset_apply(
    name: str = typer.Argument(..., help="Name of preset to activate"),
) -> None:
    """Apply and activate a system preset."""
    async def _run() -> None:
        try:
            res = await apply_preset(name)
            console.print(f"[bold green]✓ Activated preset '{name}'[/bold green]")
            console.print(f"  • Description: {res['description']}")
            console.print(f"  • Enabled Agents: {', '.join(res['active_agents'])}")
            if res.get("active_connectors"):
                console.print(f"  • Required Connectors: {', '.join(res['active_connectors'])}")
        except Exception as exc:
            console.print(f"[bold red]✗ Failed to apply preset: {exc}[/bold red]")
            raise typer.Exit(1) from exc

    asyncio.run(_run())


@preset_app.command(name="current")
def preset_current() -> None:
    """Display the currently active preset."""
    async def _run() -> None:
        current = await get_current_preset()
        if current:
            console.print(f"Currently active preset: [bold cyan]{current}[/bold cyan]")
        else:
            console.print("[dim]No specific preset active (custom configuration).[/dim]")

    asyncio.run(_run())
