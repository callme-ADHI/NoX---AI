"""Secret store management commands for NoX CLI."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from noxai.security.secrets import delete_secret, get_secret, list_secrets, set_secret

secrets_app = typer.Typer(help="Manage secure credentials and tokens.")
console = Console()


@secrets_app.command(name="list")
def secrets_list() -> None:
    """List stored secret names (values are never shown)."""
    names = list_secrets()
    if not names:
        console.print("[dim]No secrets found in store.[/dim]")
        return

    table = Table(title=f"Stored Secrets ({len(names)})")
    table.add_column("Secret Name", style="bold cyan")
    table.add_column("Status", style="green")

    for name in names:
        table.add_row(name, "configured")
    console.print(table)


@secrets_app.command(name="set")
def secrets_set(
    name: str = typer.Argument(..., help="Name/key of the secret"),
    value: str = typer.Option(
        None, "--value", "-v", help="Secret value (if omitted, prompts securely)"
    ),
) -> None:
    """Store or update a secret."""
    if value is None:
        value = typer.prompt(f"Enter secret value for '{name}'", hide_input=True)

    if not value:
        console.print("[red]Secret value cannot be empty.[/red]")
        raise typer.Exit(1)

    set_secret(name, value)
    console.print(f"[green]✔ Secret '{name}' stored successfully.[/green]")


@secrets_app.command(name="get")
def secrets_get(
    name: str = typer.Argument(..., help="Name of the secret to retrieve"),
    show: bool = typer.Option(False, "--show", help="Display the secret value in the terminal"),
) -> None:
    """Check if a secret is set (or display it with --show)."""
    val = get_secret(name)
    if val is None:
        console.print(f"[yellow]Secret '{name}' is not configured.[/yellow]")
        raise typer.Exit(1)

    if show:
        console.print(f"[bold]{val}[/bold]")
    else:
        preview = f"{val[:3]}...{val[-2:]}" if len(val) > 6 else "[REDACTED]"
        console.print(
            f"[green]Secret '{name}' is configured: {preview} (use --show to reveal)[/green]"
        )


@secrets_app.command(name="delete")
def secrets_delete(
    name: str = typer.Argument(..., help="Name of the secret to delete"),
) -> None:
    """Delete a secret by name."""
    ok = delete_secret(name)
    if ok:
        console.print(f"[green]✔ Secret '{name}' deleted.[/green]")
    else:
        console.print(f"[yellow]Secret '{name}' was not found.[/yellow]")
