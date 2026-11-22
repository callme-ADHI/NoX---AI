"""Approvals Center CLI commands for NoX."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from noxai.security.approvals import ApprovalsCenter

approvals_app = typer.Typer(help="Review and authorize pending write actions.")
console = Console()


@approvals_app.command(name="list")
def approvals_list(
    status: str | None = typer.Option(
        None,
        "--status",
        "-s",
        help="Filter by status (pending, approved, denied, consumed, expired, revoked)",
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum approvals to show"),
) -> None:
    """List approvals pending user review."""
    items = asyncio.run(ApprovalsCenter.list_approvals(status=status, limit=limit))
    if not items:
        status_msg = f" with status '{status}'" if status else ""
        console.print(f"[dim]No approvals found{status_msg}.[/dim]")
        return

    table = Table(title=f"Approvals Center ({len(items)})")
    table.add_column("ID", style="bold cyan")
    table.add_column("Action Type", style="yellow")
    table.add_column("Scope", style="magenta")
    table.add_column("Status", justify="center")
    table.add_column("Code", style="dim")
    table.add_column("Expires", style="cyan")
    table.add_column("Preview", style="italic")

    for a in items:
        status_color = {
            "pending": "yellow",
            "approved": "green",
            "denied": "red",
            "consumed": "blue",
            "expired": "dim",
            "revoked": "red",
        }.get(a.status, "white")
        badge = f"[{status_color}]{a.status}[/{status_color}]"
        table.add_row(
            a.id,
            a.action_type,
            a.scope,
            badge,
            a.confirmation_code,
            a.expires_at[:19] if a.expires_at else "",
            a.preview[:40],
        )
    console.print(table)


@approvals_app.command(name="approve")
def approvals_approve(
    approval_id: str = typer.Argument(..., help="ID of the approval (e.g. appr_...)"),
    code: str | None = typer.Option(None, "--code", "-c", help="Optional confirmation code"),
) -> None:
    """Authorize a pending action."""
    try:
        appr = asyncio.run(
            ApprovalsCenter.approve(approval_id, actor="cli", confirmation_code=code)
        )
        msg = f"[bold green]✔ Approved action {approval_id} ({appr.action_type}: {appr.scope})[/]"
        console.print(msg)
    except Exception as exc:
        console.print(f"[bold red]✘ Approval failed: {exc}[/bold red]")
        raise typer.Exit(1) from None


@approvals_app.command(name="deny")
def approvals_deny(
    approval_id: str = typer.Argument(..., help="ID of the approval (e.g. appr_...)"),
    reason: str = typer.Option("", "--reason", "-r", help="Reason for rejection"),
) -> None:
    """Reject a pending action."""
    try:
        appr = asyncio.run(ApprovalsCenter.deny(approval_id, actor="cli", reason=reason))
        msg = f"[bold yellow]✔ Denied action {approval_id} ({appr.action_type}: {appr.scope})[/]"
        console.print(msg)
    except Exception as exc:
        console.print(f"[bold red]✘ Rejection failed: {exc}[/bold red]")
        raise typer.Exit(1) from None
