"""Emergency kill switch command for NoX CLI ('nx panic')."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.panel import Panel

from noxai.security.kill_switch import KillSwitch

panic_app = typer.Typer(help="Emergency kill switch controls.")
console = Console()


@panic_app.callback(invoke_without_command=True)
def panic_main(
    ctx: typer.Context,
    resume: bool = typer.Option(
        False, "--resume", "-r", help="Resume normal operations from panic state"
    ),
    status: bool = typer.Option(False, "--status", "-s", help="Check kill switch status"),
    reason: str = typer.Option(
        "CLI panic command", "--reason", help="Reason for emergency shutdown"
    ),
) -> None:
    """Trigger the emergency kill switch or resume from panic state."""
    if status:
        state = asyncio.run(KillSwitch.get_state())
        if state["engaged"]:
            console.print(
                Panel(
                    f"[bold red]KILL SWITCH IS ACTIVE[/bold red]\n"
                    f"Engaged at: {state['engaged_at']}\n"
                    f"Actor: {state['actor']}\n"
                    f"Reason: {state['reason']}",
                    title="Panic State",
                    border_style="red",
                )
            )
        else:
            console.print("[bold green]✔ System running normally (kill switch inactive)[/]")
        return

    if resume:
        asyncio.run(KillSwitch.resume(actor="cli"))
        console.print("[bold green]✔ Emergency kill switch resumed. System operating normally.[/]")
        return

    # Default action: engage kill switch
    asyncio.run(KillSwitch.engage(reason=reason, actor="cli"))
    console.print(
        Panel(
            "[bold white on red] EMERGENCY KILL SWITCH ENGAGED [/bold white on red]\n\n"
            "- Connectors background sync suspended\n"
            "- Social channel polling stopped\n"
            "- Continuous agents paused\n"
            "- All pending approvals revoked\n"
            "- Tamper-evident audit event recorded\n\n"
            "Run [bold cyan]nx panic --resume[/bold cyan] to restore normal operations.",
            title="Panic Mode Active",
            border_style="red",
        )
    )
