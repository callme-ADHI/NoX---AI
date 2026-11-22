"""CLI commands for managing external connectors: Gmail, IMAP, Calendar, etc."""

from __future__ import annotations

import asyncio
from typing import Any

import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from noxai.connectors.base import ConnectorRegistry
from noxai.connectors.calendar import CalendarConnector
from noxai.connectors.gmail import GmailConnector
from noxai.connectors.imap import IMAPConnector
from noxai.security.secrets import SecretStore

connect_app = typer.Typer(
    name="connect",
    help="Connect and manage external services (Gmail, IMAP, Calendar, etc.).",
    no_args_is_help=True,
)
console = Console()


@connect_app.command(name="list")
def connect_list() -> None:
    """List all registered connectors and their health status."""
    items = asyncio.run(ConnectorRegistry.list_db_connectors())
    if not items:
        console.print(
            "[dim]No connectors configured yet. Run `nx connect gmail` or `nx connect imap`.[/dim]"
        )
        return

    table = Table(title="Configured Connectors")
    table.add_column("Name", style="cyan bold")
    table.add_column("Type", style="magenta")
    table.add_column("Enabled", style="green")
    table.add_column("Health", style="bold")
    table.add_column("Privacy", style="blue")
    table.add_column("Last Sync", style="dim")
    table.add_column("Details", style="white")

    for it in items:
        status = it["health_status"]
        status_color = "green" if status == "ok" else ("yellow" if status == "degraded" else "red")
        table.add_row(
            it["name"],
            it["type"],
            "yes" if it["enabled"] else "no",
            f"[{status_color}]{status}[/{status_color}]",
            it["privacy_class"],
            it["last_sync_at"] or "never",
            it["health_reason"] or "",
        )
    console.print(table)


@connect_app.command(name="status")
def connect_status() -> None:
    """Check live status of all active connectors."""
    connect_list()


@connect_app.command(name="gmail")
def connect_gmail(
    client_id: str = typer.Option("", "--client-id", help="Google Cloud OAuth Client ID."),
    client_secret: str = typer.Option(
        "", "--client-secret", help="Google Cloud OAuth Client Secret."
    ),
    code: str = typer.Option("", "--code", help="Authorization code from Google OAuth."),
) -> None:
    """Connect a Gmail account using OAuth 2.0 PKCE flow."""
    connector = GmailConnector()

    async def _run() -> None:
        cid = client_id or SecretStore.get("connector:gmail:client_id")
        csecret = client_secret or SecretStore.get("connector:gmail:client_secret")

        if not cid:
            console.print("[bold yellow]No Google OAuth credentials found.[/bold yellow]")
            console.print(
                "See instructions in [bold cyan]docs/CONNECT_GMAIL.md[/bold cyan] "
                "to create a client ID."
            )
            cid = Prompt.ask("Enter Google OAuth Client ID")
            csecret = Prompt.ask("Enter Google OAuth Client Secret", password=True)

        verifier, challenge = connector.generate_pkce_pair()
        redirect_uri = "urn:ietf:wg:oauth:2.0:oob"  # manual code exchange or loopback
        auth_url = connector.get_auth_url(
            client_id=cid,
            redirect_uri=redirect_uri,
            code_challenge=challenge,
            scopes=[
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.compose",
            ],
        )

        auth_code = code
        if not auth_code:
            console.print(
                "\n[bold green]Open the following link in browser to authorize:[/bold green]"
            )
            console.print(f"[link={auth_url}]{auth_url}[/link]\n")
            auth_code = Prompt.ask("Enter the authorization code from the browser")

        with console.status("[bold green]Exchanging authorization code...[/bold green]"):
            await connector.exchange_code_for_tokens(
                code=auth_code.strip(),
                code_verifier=verifier,
                redirect_uri=redirect_uri,
                client_id=cid,
                client_secret=csecret or "",
            )
            await connector.register_in_db()
            is_ok = await connector.auth()

        if is_ok:
            console.print(
                f"[bold green]✓ Connected Gmail ({connector.account_email})[/bold green]"
            )
        else:
            console.print("[bold red]✗ Failed to authenticate with Gmail API.[/bold red]")

    asyncio.run(_run())


@connect_app.command(name="imap")
def connect_imap(
    host: str = typer.Option(
        ..., "--host", "-h", help="IMAP server host (e.g. imap.example.com)."
    ),
    username: str = typer.Option(..., "--username", "-u", help="IMAP username / email."),
    port: int = typer.Option(993, "--port", "-p", help="IMAP SSL port (default 993)."),
    password: str = typer.Option("", "--password", help="IMAP account / app password."),
) -> None:
    """Connect an IMAP email account over SSL."""
    pass_val = password or Prompt.ask("Enter IMAP app password", password=True)

    async def _run() -> None:
        conn = IMAPConnector(name=f"imap_{username.split('@')[0]}")
        await conn.set_credentials(host=host, username=username, password=pass_val, port=port)
        with console.status("[bold green]Testing IMAP connection...[/bold green]"):
            ok = await conn.auth()
        if ok:
            console.print(
                f"[bold green]✓ Connected to IMAP server {host} as {username}[/bold green]"
            )
        else:
            console.print(f"[bold red]✗ Failed to connect to IMAP server {host}.[/bold red]")

    asyncio.run(_run())


@connect_app.command(name="calendar")
def connect_calendar(
    ics_path: str = typer.Option("", "--ics-path", help="Local path to .ics calendar file."),
    ics_url: str = typer.Option("", "--ics-url", help="Remote HTTP(S) URL to webcal or .ics file."),
    google: bool = typer.Option(False, "--google", help="Use Google Calendar OAuth."),
) -> None:
    """Connect a Calendar source (Google Calendar or local/remote ICS)."""

    async def _run() -> None:
        cfg: dict[str, Any] = {}
        if ics_path:
            cfg["ics_path"] = ics_path
        if ics_url:
            cfg["ics_url"] = ics_url
        conn = CalendarConnector(name="calendar", config=cfg)
        await conn.register_in_db()
        with console.status("[bold green]Checking calendar source...[/bold green]"):
            ok = await conn.auth()
        if ok:
            console.print("[bold green]✓ Calendar source configured and verified.[/bold green]")
        else:
            console.print(
                "[bold yellow]! Calendar registered, but source not yet reachable.[/bold yellow]"
            )

    asyncio.run(_run())


@connect_app.command(name="disconnect")
def connect_disconnect(
    name: str = typer.Argument(..., help="Connector name to disconnect."),
) -> None:
    """Disconnect and disable a configured connector."""

    async def _run() -> None:
        ok = await ConnectorRegistry.set_enabled(name, False)
        # Clear sensitive secrets
        SecretStore.delete(f"connector:{name}:access_token")
        SecretStore.delete(f"connector:{name}:password")
        if ok:
            console.print(
                f"[bold green]✓ Connector '{name}' disconnected and disabled.[/bold green]"
            )
        else:
            console.print(f"[bold red]✗ Connector '{name}' not found.[/bold red]")

    asyncio.run(_run())


def disconnect_command(
    name: str = typer.Argument(..., help="Connector name to disconnect."),
) -> None:
    """Top-level nx disconnect <name> command."""
    connect_disconnect(name)
