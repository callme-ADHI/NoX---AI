"""CLI commands for managing interactive communication channels (Telegram, Slack, Discord)."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from noxai.channels.base import ChannelRegistry
from noxai.channels.discord import DiscordChannel
from noxai.channels.slack import SlackChannel
from noxai.channels.telegram import TelegramChannel

channel_app = typer.Typer(
    name="channel",
    help="Manage interactive messaging channels (Telegram, Slack, Discord).",
    no_args_is_help=True,
)
console = Console()


@channel_app.command(name="list")
def channel_list() -> None:
    """List all registered channels and their pairing/health status."""
    items = asyncio.run(ChannelRegistry.list_db_channels())
    if not items:
        console.print(
            "[dim]No channels configured. Run `nx channel telegram` or `nx channel slack`.[/dim]"
        )
        return

    table = Table(title="Configured Messaging Channels")
    table.add_column("Name", style="cyan bold")
    table.add_column("Type", style="magenta")
    table.add_column("Enabled", style="green")
    table.add_column("Owner ID", style="yellow")
    table.add_column("Health", style="bold")
    table.add_column("Details", style="dim")

    for it in items:
        status = it["health_status"]
        status_color = "green" if status == "ok" else ("yellow" if status == "degraded" else "red")
        table.add_row(
            it["name"],
            it["type"],
            "yes" if it["enabled"] else "no",
            it["owner_id"] or "[dim]unpaired[/dim]",
            f"[{status_color}]{status}[/{status_color}]",
            it["health_reason"] or "",
        )
    console.print(table)


@channel_app.command(name="telegram")
def channel_telegram(
    token: str = typer.Option("", "--token", help="Telegram Bot API token from @BotFather."),
    pair: bool = typer.Option(False, "--pair", help="Generate a new 6-digit owner pairing code."),
) -> None:
    """Configure and pair a Telegram bot."""
    async def _run() -> None:
        chan = TelegramChannel(name="telegram")
        await chan.load_state_from_db()

        tok = token or chan.get_token()
        if not tok:
            console.print("[bold yellow]No Telegram bot token found.[/bold yellow]")
            tok = Prompt.ask("Enter Telegram bot token from @BotFather", password=True)
            chan.set_token(tok)

        if pair or not chan.owner_id:
            code = chan.generate_pairing_code()
            await chan.save_state_to_db()
            console.print(
                f"\n[bold green]🔑 Telegram Pairing Code:[/bold green] "
                f"[bold cyan]{code}[/bold cyan]\n"
                f"Send [bold white]/pair {code}[/bold white] to your bot on Telegram "
                f"to lock ownership."
            )
            return

        h = await chan.health()
        if h["status"] == "ok":
            console.print(f"[bold green]✓ Telegram channel healthy: {h['reason']}[/bold green]")
            if chan.owner_id:
                console.print(f"  Owner chat ID: [cyan]{chan.owner_id}[/cyan]")
        else:
            console.print(f"[bold red]✗ Telegram health check failed: {h['reason']}[/bold red]")

    asyncio.run(_run())


@channel_app.command(name="slack")
def channel_slack(
    bot_token: str = typer.Option("", "--token", help="Slack Bot User OAuth Token (xoxb-...)."),
    channel: str = typer.Option("general", "--channel", help="Default channel for notifications."),
) -> None:
    """Configure a Slack workspace bot channel."""
    async def _run() -> None:
        chan = SlackChannel(name="slack", config={"channel": channel})
        tok = bot_token or chan.get_token()
        if not tok:
            tok = Prompt.ask("Enter Slack Bot Token (xoxb-...)", password=True)
        chan.set_token(tok)
        await chan.save_state_to_db()
        h = await chan.health()
        if h["status"] == "ok":
            console.print(f"[bold green]✓ Slack channel connected: {h['reason']}[/bold green]")
        else:
            console.print(f"[bold red]✗ Slack connection error: {h['reason']}[/bold red]")

    asyncio.run(_run())


@channel_app.command(name="discord")
def channel_discord(
    bot_token: str = typer.Option("", "--token", help="Discord Bot Token."),
    channel_id: str = typer.Option("", "--channel-id", help="Default Discord Channel ID."),
) -> None:
    """Configure a Discord bot channel."""
    async def _run() -> None:
        chan = DiscordChannel(name="discord", config={"channel_id": channel_id})
        tok = bot_token or chan.get_token()
        if not tok:
            tok = Prompt.ask("Enter Discord Bot Token", password=True)
        chan.set_token(tok)
        if channel_id:
            chan.default_channel_id = channel_id
        await chan.save_state_to_db()
        h = await chan.health()
        if h["status"] == "ok":
            console.print(f"[bold green]✓ Discord bot connected: {h['reason']}[/bold green]")
        else:
            console.print(f"[bold red]✗ Discord connection error: {h['reason']}[/bold red]")

    asyncio.run(_run())
