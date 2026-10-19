"""Task Center management commands for NoX CLI (§1.4, §G3, M10)."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table

from noxai.config import get_config
from noxai.db import run_migrations
from noxai.tasks.center import TaskCenter
from noxai.tasks.models import TaskLane, TaskStatus

tasks_app = typer.Typer(help="Manage asynchronous background tasks.")
console = Console()


@tasks_app.command(name="list")
def tasks_list(
    status: str | None = typer.Option(None, "--status", "-s", help="Filter by status."),
    lane: str | None = typer.Option(
        None, "--lane", "-l", help="Filter by lane (local, cloud, scheduler)."
    ),
    limit: int = typer.Option(50, "--limit", "-n", help="Max tasks to show."),
) -> None:
    """List background tasks."""

    async def _list() -> None:
        await run_migrations()
        tc = TaskCenter(config=get_config())
        s_val = TaskStatus(status) if status else None
        l_val = TaskLane(lane) if lane else None
        items = await tc.list_tasks(status=s_val, lane=l_val, limit=limit)

        if not items:
            console.print("[dim]No tasks found.[/dim]")
            return

        table = Table(title="NoX Background Tasks")
        table.add_column("ID", style="dim", no_wrap=True)
        table.add_column("Title", style="bold cyan")
        table.add_column("Lane", style="green")
        table.add_column("Priority", style="yellow")
        table.add_column("Status", style="bold")
        table.add_column("Progress", style="magenta")
        table.add_column("Created", style="dim")

        for t in items:
            status_style = {
                TaskStatus.QUEUED: "[yellow]queued[/]",
                TaskStatus.RUNNING: "[bold blue]running[/]",
                TaskStatus.PAUSED: "[dim]paused[/]",
                TaskStatus.WAITING_APPROVAL: "[bold magenta]waiting_approval[/]",
                TaskStatus.DONE: "[bold green]done[/]",
                TaskStatus.FAILED: "[bold red]failed[/]",
                TaskStatus.CANCELLED: "[dim red]cancelled[/]",
            }.get(t.status, str(t.status.value))

            table.add_row(
                t.id,
                t.title,
                t.lane.value,
                t.priority.value,
                status_style,
                f"{int(t.progress * 100)}%",
                t.created_at[:19] if t.created_at else "",
            )

        console.print(table)

    asyncio.run(_list())


@tasks_app.command(name="show")
def tasks_show(
    task_id: str = typer.Argument(..., help="Task ID (e.g. task_...)."),
) -> None:
    """Show details of a specific background task."""

    async def _show() -> None:
        await run_migrations()
        tc = TaskCenter(config=get_config())
        t = await tc.get_task(task_id)
        if not t:
            console.print(f"[red]Task '{task_id}' not found.[/]")
            raise typer.Exit(1)

        console.rule(f"[bold cyan]Task: {t.title} ({t.id})[/bold cyan]")
        console.print(f"[bold]Lane:[/]        {t.lane.value}")
        console.print(f"[bold]Priority:[/]    {t.priority.value}")
        console.print(f"[bold]Status:[/]      {t.status.value}")
        console.print(f"[bold]Progress:[/]    {int(t.progress * 100)}%")
        console.print(f"[bold]Created:[/]     {t.created_at}")
        console.print(f"[bold]Started:[/]     {t.started_at or '[dim]none[/]'}")
        console.print(f"[bold]Finished:[/]    {t.finished_at or '[dim]none[/]'}")
        if t.approval_id:
            console.print(f"[bold yellow]Approval ID:[/] {t.approval_id}")
        if t.error:
            console.print(f"[bold red]Error:[/]       {t.error}")
        console.print("\n[bold]Prompt:[/]\n" + t.prompt)
        if t.result_json:
            console.print("\n[bold]Result JSON:[/]\n" + t.result_json)

    asyncio.run(_show())


@tasks_app.command(name="cancel")
def tasks_cancel(
    task_id: str = typer.Argument(..., help="Task ID to cancel."),
) -> None:
    """Cancel a queued or running background task."""

    async def _cancel() -> None:
        await run_migrations()
        tc = TaskCenter(config=get_config())
        ok = await tc.cancel_task(task_id)
        if not ok:
            console.print(
                f"[yellow]Could not cancel task '{task_id}' "
                "(not found or already finished).[/yellow]"
            )
            raise typer.Exit(1)
        console.print(f"[green]✓[/] Task '{task_id}' cancelled.")

    asyncio.run(_cancel())


@tasks_app.command(name="create")
def tasks_create(
    title: str = typer.Argument(..., help="Short title."),
    prompt: str = typer.Argument(..., help="Prompt or instructions for the task."),
    lane: str = typer.Option("local", "--lane", "-l", help="Execution lane (local, cloud)."),
    priority: str = typer.Option(
        "normal", "--priority", "-p", help="Priority (low, normal, high, urgent)."
    ),
) -> None:
    """Create and enqueue a background task."""

    async def _create() -> None:
        await run_migrations()
        tc = TaskCenter(config=get_config())
        try:
            t = await tc.create_task(
                title=title,
                prompt=prompt,
                lane=lane,
                priority=priority,
            )
            console.print(f"[green]✓[/] Created task [bold]{t.id}[/] ('{t.title}')")
        except Exception as exc:
            console.print(f"[bold red]Failed to create task:[/] {exc}")
            raise typer.Exit(1) from exc

    asyncio.run(_create())
