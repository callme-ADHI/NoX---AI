# Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
# commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
# Modified for NoX: CLI commands for MCP server registration, inspection, and safety toggles (Workstream E).

"""CLI commands for NoX Model Context Protocol (MCP) server management."""

from __future__ import annotations

import asyncio
import time
from typing import List

import typer
from rich.console import Console
from rich.table import Table

from noxai.mcp.client import MCPServerConfig, get_mcp_registry

mcp_app = typer.Typer(help="Manage external MCP tools and servers (Workstream E).")
console = Console()


@mcp_app.command(name="list")
def mcp_list() -> None:
    """List configured MCP servers and their current status."""
    registry = get_mcp_registry()
    servers = registry.list_servers()

    if not servers:
        console.print("[dim]No MCP servers registered. Add one with 'nx mcp add'.[/dim]")
        return

    table = Table(title="Configured MCP Servers", show_header=True)
    table.add_column("Server", style="bold cyan")
    table.add_column("Command", style="dim")
    table.add_column("Args")
    table.add_column("Enabled", justify="center")
    table.add_column("Allowlist", style="yellow")
    table.add_column("Timeout", justify="right")

    for s in servers:
        en_badge = "[bold green]YES[/]" if s.enabled else "[bold red]NO[/]"
        table.add_row(
            s.name,
            s.command,
            " ".join(s.args) if s.args else "[dim]none[/]",
            en_badge,
            ", ".join(s.allowlist),
            f"{s.timeout_seconds}s",
        )
    console.print(table)


@mcp_app.command(name="show")
def mcp_show(
    name: str = typer.Argument(..., help="Name of the MCP server."),
) -> None:
    """Show details and available tools for an MCP server."""
    registry = get_mcp_registry()
    server = registry.get_server(name)

    if not server:
        console.print(f"[red]✗[/] MCP server '{name}' not found.")
        raise typer.Exit(1)

    console.print(f"\n[bold cyan]MCP Server:[/] [bold]{server.name}[/]")
    console.print(f"  • Command: {server.command} {' '.join(server.args)}")
    console.print(f"  • Enabled: {'[green]YES[/]' if server.enabled else '[red]NO[/]'}")
    console.print(f"  • Allowlist: {', '.join(server.allowlist)}")
    console.print(f"  • Timeout: {server.timeout_seconds}s")

    if not server.enabled:
        console.print("\n[yellow]Note: Server is disabled. Enable with 'nx mcp enable " + name + "' to query live tools.[/]")
        return

    async def _fetch_tools():
        client = await registry.get_client(name)
        tools = await client.list_tools()
        if not tools:
            console.print("\n[dim]No tools advertised by server.[/dim]")
            return

        table = Table(title=f"Tools offered by '{name}'", show_header=True)
        table.add_column("Tool Name", style="bold green")
        table.add_column("Description")
        for t in tools:
            table.add_row(t.name, t.description or "[dim]none[/]")
        console.print("\n")
        console.print(table)

    try:
        asyncio.run(_fetch_tools())
    except Exception as exc:
        console.print(f"[red]✗[/] Could not connect to MCP server: {exc}")


@mcp_app.command(name="enable")
def mcp_enable(
    name: str = typer.Argument(..., help="Name of the MCP server to enable."),
) -> None:
    """Enable an MCP server."""
    registry = get_mcp_registry()
    if registry.enable_server(name):
        console.print(f"[bold green]✓[/] MCP server '{name}' is now [bold green]enabled[/].")
    else:
        console.print(f"[red]✗[/] MCP server '{name}' not found.")
        raise typer.Exit(1)


@mcp_app.command(name="disable")
def mcp_disable(
    name: str = typer.Argument(..., help="Name of the MCP server to disable."),
) -> None:
    """Disable an MCP server."""
    registry = get_mcp_registry()
    if registry.disable_server(name):
        console.print(f"[yellow]![/] MCP server '{name}' is now [dim]disabled[/].")
    else:
        console.print(f"[red]✗[/] MCP server '{name}' not found.")
        raise typer.Exit(1)


@mcp_app.command(name="add")
def mcp_add(
    name: str = typer.Argument(..., help="Unique identifier for this MCP server."),
    command: str = typer.Argument(..., help="Executable command (e.g. npx or python)."),
    args: List[str] = typer.Argument(None, help="Arguments to pass to the executable command."),
) -> None:
    """Register a new stdio MCP server (starts disabled by default)."""
    registry = get_mcp_registry()
    cfg = MCPServerConfig(
        name=name,
        command=command,
        args=args or [],
        enabled=False,  # starts disabled per Session M spec
        allowlist=["*"],
    )
    registry.register_server(cfg)
    console.print(f"[bold green]✓[/] Registered MCP server '[bold]{name}[/]' (status: [bold red]disabled[/]).")
    console.print(f"  Enable when ready with: [cyan]nx mcp enable {name}[/]")


@mcp_app.command(name="test")
def mcp_test(
    name: str = typer.Argument(..., help="Name of the MCP server to test."),
) -> None:
    """Connect to an MCP server, perform initialize handshake, and list tools."""
    registry = get_mcp_registry()
    server = registry.get_server(name)

    if not server:
        console.print(f"[red]✗[/] MCP server '{name}' not found.")
        raise typer.Exit(1)

    async def _test():
        console.print(f"[cyan]Testing connection to MCP server '{name}'...[/]")
        t0 = time.perf_counter()
        # Temporarily enable for test if needed
        prev_enabled = server.enabled
        server.enabled = True
        try:
            client = await registry.get_client(name)
            tools = await client.list_tools()
            latency_ms = (time.perf_counter() - t0) * 1000.0
            console.print(f"[bold green]✓[/] Handshake and tool listing succeeded in {latency_ms:.1f}ms:")
            console.print(f"  • Tools found ({len(tools)}):")
            for t in tools:
                console.print(f"    - [bold]{t.name}[/]: {t.description}")
        finally:
            server.enabled = prev_enabled

    try:
        asyncio.run(_test())
    except Exception as exc:
        console.print(f"[red]✗[/] MCP test failed: {exc}")
        raise typer.Exit(1)
