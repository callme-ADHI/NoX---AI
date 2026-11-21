"""CLI commands for autonomous agents management (`nx agents ...`)."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from noxai.agents.runner import AgentRunner
from noxai.agents.spec import sync_builtin_agents_to_db
from noxai.db import get_db

agents_app = typer.Typer(
    name="agents",
    help="Manage and execute multi-mode autonomous agents (on-demand, scheduled, continuous).",
    no_args_is_help=True,
)
console = Console()


@agents_app.command(name="list")
def agents_list() -> None:
    """List all registered agents and their execution modes and budgets."""
    async def _run() -> None:
        await sync_builtin_agents_to_db()
        async with get_db() as db:
            async with db.execute(
                """
                SELECT name, mode, cron_expression, enabled, budget_tokens,
                       budget_cost_usd, status, last_run_at
                FROM agents ORDER BY name ASC
                """
            ) as cur:
                rows = await cur.fetchall()

        table = Table(title="Autonomous Agents Registry")
        table.add_column("Agent", style="cyan bold")
        table.add_column("Mode", style="magenta")
        table.add_column("Schedule", style="yellow")
        table.add_column("Enabled", style="green")
        table.add_column("Budget (Tokens/USD)", style="dim")
        table.add_column("Status", style="bold")
        table.add_column("Last Run", style="dim")

        for r in rows:
            sched = r[2] or ("continuous" if r[1] == "continuous" else "manual")
            status_color = "green" if r[6] == "idle" else ("cyan" if r[6] == "running" else "red")
            table.add_row(
                r[0],
                r[1],
                sched,
                "yes" if r[3] else "no",
                f"{r[4]:,} / ${r[5]:.2f}",
                f"[{status_color}]{r[6]}[/{status_color}]",
                (r[7] or "")[:16],
            )
        console.print(table)

    asyncio.run(_run())


@agents_app.command(name="run")
def agents_run(
    name: str = typer.Argument(..., help="Agent name to run (e.g. deep_research, morning_digest)"),
    prompt: str = typer.Option("Run autonomous task", "--prompt", "-p", help="Task prompt"),
) -> None:
    """Execute an agent on-demand with budget and killswitch enforcement."""
    async def _run() -> None:
        await sync_builtin_agents_to_db()
        runner = AgentRunner()
        with console.status(f"[bold cyan]Running agent '{name}'...[/bold cyan]"):
            try:
                res = await runner.run_agent(name, input_prompt=prompt, trigger="manual")
            except Exception as exc:
                console.print(f"[bold red]✗ Agent execution failed: {exc}[/bold red]")
                raise typer.Exit(1) from exc

        if res["status"] == "succeeded":
            meta_str = (
                f"[dim]Tokens: {res['tokens_used']} | Cost: ${res['cost_usd']:.4f} | "
                f"Duration: {res['duration_ms']}ms[/dim]"
            )
            console.print(Panel(
                f"{res['output']}\n\n{meta_str}",
                title=f"✓ Agent Run Complete: {name} ({res['run_id']})",
                border_style="green",
            ))
        else:
            console.print(f"[bold red]✗ Agent run error: {res.get('error')}[/bold red]")

    asyncio.run(_run())


@agents_app.command(name="status")
def agents_status(
    name: str = typer.Argument(..., help="Agent name to inspect"),
) -> None:
    """Inspect status and recent execution history of an agent."""
    async def _run() -> None:
        async with get_db() as db:
            async with db.execute(
                """
                SELECT id, name, mode, cron_expression, enabled, budget_tokens,
                       budget_cost_usd, status, prompt_template, last_run_at, last_error
                FROM agents WHERE name = ?
                """,
                (name,),
            ) as cur:
                agent = await cur.fetchone()
                if not agent:
                    console.print(f"[bold red]Agent '{name}' not found.[/bold red]")
                    return

            async with db.execute(
                """
                SELECT id, trigger, status, tokens_used, cost_usd, duration_ms, created_at
                FROM agent_runs WHERE agent_id = ? ORDER BY created_at DESC LIMIT 5
                """,
                (agent[0],),
            ) as cur:
                runs = await cur.fetchall()

        en_str = "yes" if agent[4] else "no"
        console.print(Panel(
            f"[bold cyan]Name:[/bold cyan] {agent[1]} (Mode: {agent[2]})\n"
            f"[bold]Status:[/bold] {agent[7]} | [bold]Enabled:[/bold] {en_str}\n"
            f"[bold]Budget:[/bold] {agent[5]:,} tokens / ${agent[6]:.2f}\n"
            f"[bold]Prompt Template:[/bold] {agent[8][:100]}...\n"
            f"[bold]Last Run:[/bold] {agent[9] or 'never'}\n"
            f"[bold]Last Error:[/bold] {agent[10] or 'none'}",
            title=f"Agent Details: {name}",
            border_style="cyan",
        ))

        if runs:
            table = Table(title="Recent Execution Runs")
            table.add_column("Run ID", style="cyan")
            table.add_column("Trigger", style="magenta")
            table.add_column("Status", style="bold")
            table.add_column("Tokens", style="dim")
            table.add_column("Cost", style="dim")
            table.add_column("Duration", style="dim")
            table.add_column("Started", style="dim")
            for r in runs:
                c = "green" if r[2] == "succeeded" else "red"
                table.add_row(
                    r[0],
                    r[1],
                    f"[{c}]{r[2]}[/{c}]",
                    str(r[3]),
                    f"${r[4]:.4f}",
                    f"{r[5]}ms",
                    (r[6] or "")[:16],
                )
            console.print(table)

    asyncio.run(_run())
