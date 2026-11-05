"""Job management commands for NoX CLI (spec §6.7, §10)."""

from __future__ import annotations

import asyncio
import json
import sqlite3

import typer
from rich.console import Console
from rich.table import Table

from noxai.db import DB_PATH, fetch_all, fetch_one, get_db, run_migrations
from noxai.scheduler.scheduler import JobScheduler
from noxai.scheduler.seeds import seed_database

jobs_app = typer.Typer(help="Job management commands (M6).")
console = Console()


@jobs_app.command(name="list")
def jobs_list() -> None:
    """List all scheduled jobs."""

    async def _list() -> None:
        await run_migrations()
        async with get_db() as db:
            await seed_database(db)
            rows = await db.execute_fetchall(
                "SELECT id, name, schedule_type, schedule_expr, timezone, enabled, next_run_at "
                "FROM jobs WHERE deleted_at IS NULL ORDER BY name ASC"
            )
            if not rows:
                console.print("[dim]No jobs found.[/dim]")
                return

            table = Table(title="NoX Scheduled Jobs")
            table.add_column("ID", style="dim")
            table.add_column("Name", style="bold cyan")
            table.add_column("Schedule", style="green")
            table.add_column("Timezone", style="dim")
            table.add_column("Enabled", style="bold")
            table.add_column("Next Run", style="yellow")

            for r in rows:
                en_str = "[green]yes[/]" if r[5] else "[red]no[/]"
                next_str = str(r[6]) if r[6] else "[dim]none[/]"
                table.add_row(
                    str(r[0]),
                    str(r[1]),
                    f"{r[2]}: {r[3]}",
                    str(r[4] or "local"),
                    en_str,
                    next_str,
                )
            console.print(table)

    asyncio.run(_list())


@jobs_app.command(name="show")
def jobs_show(name: str = typer.Argument(..., help="Job name or ID.")) -> None:
    """Show detailed configuration of a scheduled job."""
    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}[/]")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM jobs WHERE name = ? OR id = ?", (name, name)).fetchone()
        if not row:
            console.print(f"[red]Job '{name}' not found.[/]")
            raise typer.Exit(1)

        console.rule(f"[bold cyan]Job: {row['name']} ({row['id']})[/bold cyan]")
        console.print(f"[bold]Description:[/]  {row['description']}")
        console.print(f"[bold]Schedule:[/]     {row['schedule_type']} ({row['schedule_expr']})")
        console.print(f"[bold]Timezone:[/]     {row['timezone']}")
        console.print(
            f"[bold]Enabled:[/]      {'[green]yes[/]' if row['enabled'] else '[red]no[/]'}"
        )
        console.print(f"[bold]Version:[/]      {row['version']}")
        console.print(f"[bold]Next Run:[/]     {row['next_run_at'] or '[dim]none[/]'}")
        console.print(f"[bold]Last Run:[/]     {row['last_run_at'] or '[dim]none[/]'}")
        console.print("\n[bold]Instruction:[/]")
        console.print(row["instruction"])
        if row["config_json"]:
            console.print("\n[bold]Config JSON:[/]")
            console.print(row["config_json"])
    finally:
        conn.close()


@jobs_app.command(name="enable")
def jobs_enable(name: str = typer.Argument(..., help="Job name or ID to enable.")) -> None:
    """Enable a scheduled job."""
    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}[/]")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.execute("UPDATE jobs SET enabled = 1 WHERE name = ? OR id = ?", (name, name))
        conn.commit()
        if cur.rowcount == 0:
            console.print(f"[yellow]Job '{name}' not found.[/yellow]")
        else:
            console.print(f"[green]✓[/] Job '{name}' enabled.")
    finally:
        conn.close()


@jobs_app.command(name="disable")
def jobs_disable(name: str = typer.Argument(..., help="Job name or ID to disable.")) -> None:
    """Disable a scheduled job."""
    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}[/]")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.execute("UPDATE jobs SET enabled = 0 WHERE name = ? OR id = ?", (name, name))
        conn.commit()
        if cur.rowcount == 0:
            console.print(f"[yellow]Job '{name}' not found.[/yellow]")
        else:
            console.print(f"[yellow]✓[/] Job '{name}' disabled.")
    finally:
        conn.close()


@jobs_app.command(name="history")
def jobs_history(
    name: str = typer.Argument(..., help="Job name or ID."),
    limit: int = typer.Option(10, "-n", "--limit", help="Max history runs to show."),
) -> None:
    """Show execution history for a job."""
    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}[/]")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        job = conn.execute(
            "SELECT id, name FROM jobs WHERE name = ? OR id = ?", (name, name)
        ).fetchone()
        if not job:
            console.print(f"[red]Job '{name}' not found.[/]")
            raise typer.Exit(1)

        rows = conn.execute(
            """
            SELECT id, started_at, finished_at, status, error
            FROM job_runs WHERE job_id = ? ORDER BY started_at DESC LIMIT ?
            """,
            (job["id"], limit),
        ).fetchall()

        if not rows:
            console.print(f"[dim]No execution history found for job '{job['name']}'.[/dim]")
            return

        table = Table(title=f"History for {job['name']} (ID: {job['id']})")
        table.add_column("Run ID", style="dim")
        table.add_column("Started At", style="cyan")
        table.add_column("Finished At", style="dim")
        table.add_column("Status", justify="center")
        table.add_column("Error", style="red")

        for r in rows:
            st = "[green]ok[/]" if r["status"] == "ok" else f"[red]{r['status']}[/]"
            table.add_row(
                r["id"], r["started_at"][:19], (r["finished_at"] or "")[:19], st, r["error"] or ""
            )
        console.print(table)
    finally:
        conn.close()


@jobs_app.command(name="restore")
def jobs_restore(
    name: str = typer.Argument(..., help="Job name or ID."),
    version: int | None = typer.Argument(None, help="Optional version number to restore. If omitted, restores a soft-deleted job."),
) -> None:
    """Restore a soft-deleted job, or restore a previous version of a job definition."""
    if version is None:
        from noxai.jobs.deletion import restore_job

        try:
            res = asyncio.run(restore_job(name))
            console.print(f"[green]✓[/] Job '{res['name']}' ({res['id']}) restored.")
        except Exception as exc:
            console.print(f"[red]Error restoring job:[/] {exc}")
            raise typer.Exit(1)
        return

    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}[/]")
        raise typer.Exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        job = conn.execute(
            "SELECT id, name FROM jobs WHERE name = ? OR id = ?", (name, name)
        ).fetchone()
        if not job:
            console.print(f"[red]Job '{name}' not found.[/]")
            raise typer.Exit(1)

        v_row = conn.execute(
            "SELECT snapshot_json FROM job_versions WHERE job_id = ? AND version = ?",
            (job["id"], version),
        ).fetchone()

        if not v_row:
            console.print(f"[red]Version {version} not found for job '{job['name']}'.[/]")
            raise typer.Exit(1)

        snapshot = json.loads(v_row["snapshot_json"])
        conn.execute(
            """
            UPDATE jobs SET
                schedule_type = ?, schedule_expr = ?, timezone = ?,
                instruction = ?, config_json = ?, enabled = ?,
                version = version + 1, updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                snapshot["schedule_type"],
                snapshot["schedule_expr"],
                snapshot.get("timezone", "Asia/Kolkata"),
                snapshot["instruction"],
                snapshot.get("config_json", "{}"),
                snapshot.get("enabled", 1),
                job["id"],
            ),
        )
        conn.commit()
        console.print(
            f"[green]✓[/] Restored job '{job['name']}' to configuration from version {version}."
        )
    finally:
        conn.close()


@jobs_app.command(name="run")
def jobs_run(name: str = typer.Argument(..., help="Job name or ID to execute.")) -> None:
    """Run a job immediately by name or ID."""

    async def _run() -> None:
        await run_migrations()
        async with get_db() as db:
            await seed_database(db)
            cursor = await db.execute("SELECT * FROM jobs WHERE name = ? OR id = ?", (name, name))
            row = await cursor.fetchone()
            if not row:
                console.print(f"[red]Error:[/] Job '{name}' not found.")
                raise typer.Exit(1)

            col_names = [d[0] for d in cursor.description]
            job_dict = dict(zip(col_names, row, strict=False))

            console.print(f"[bold cyan]Running job:[/] {job_dict['name']} ({job_dict['id']})...")
            scheduler = JobScheduler()
            res = await scheduler.run_job(job_dict, db, run_context="cli")
            status = res.get("status", "unknown")
            if status == "ok":
                console.print("[green]Job completed successfully![/]")
                if res.get("output"):
                    console.print(f"\n[dim]--- Output ---[/dim]\n{res['output'][:500]}")
            else:
                console.print(f"[red]Job failed with status: {status}[/]")
                if res.get("error"):
                    console.print(f"[red]Error:[/] {res['error']}")
                raise typer.Exit(1)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Planned Jobs Engine Commands (Section 4.1 & 4.3)
# ---------------------------------------------------------------------------

plan_app = typer.Typer(help="Plan creation, inspection, and scoped approval.")
jobs_app.add_typer(plan_app, name="plan")


def _resolve_job_id_and_instruction(name_or_id: str) -> tuple[str, str, str]:
    if not DB_PATH.exists():
        console.print(f"[red]Database not found at {DB_PATH}[/]")
        raise typer.Exit(1)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT id, name, instruction FROM jobs WHERE name = ? OR id = ?", (name_or_id, name_or_id)).fetchone()
        if not row:
            console.print(f"[red]Error:[/] Job '{name_or_id}' not found.")
            raise typer.Exit(1)
        return row["id"], row["name"], row["instruction"]
    finally:
        conn.close()


def _render_plan_table(plan_data: dict[str, Any]) -> None:
    steps = plan_data.get("steps", [])
    table = Table(title=f"Plan Steps ({len(steps)} steps)")
    table.add_column("#", style="dim", justify="right")
    table.add_column("Kind", style="bold cyan")
    table.add_column("Folder", style="green")
    table.add_column("Remote", style="yellow")
    table.add_column("Timeout", style="magenta")

    for s in steps:
        table.add_row(
            str(s.get("step_index")),
            s.get("kind", ""),
            s.get("folder_path") or s.get("folder_alias", ""),
            s.get("remote") or "[dim]origin[/dim]",
            f"{s.get('timeout_seconds', 60)}s",
        )
    console.print(table)


def _render_folder_summaries(plan_data: dict[str, Any]) -> None:
    from noxai.jobs.template import calculate_folder_summary
    steps = plan_data.get("steps", [])
    folders = list(dict.fromkeys(s.get("folder_path") for s in steps if s.get("folder_path") and s.get("folder_path") != "~/Documents"))
    if not folders:
        return

    table = Table(title="Upload Pre-flight Summary")
    table.add_column("Folder", style="bold")
    table.add_column("Files", justify="right")
    table.add_column("Size", justify="right")
    table.add_column("Remote Origin", style="cyan")
    table.add_column("Private Repo", style="green")

    for f in folders:
        summ = calculate_folder_summary(f)
        priv_str = "[green]yes[/]" if summ["is_private"] is True else ("[red]NO (PUBLIC)[/]" if summ["is_private"] is False else "[yellow]unverified[/]")
        remote_str = summ["remote"] or "[yellow]missing[/]"
        table.add_row(
            f,
            str(summ["file_count"]),
            f"{summ['total_size_mb']} MB",
            remote_str,
            priv_str,
        )
    console.print(table)


def _do_generate_plan(name_or_id: str, force_template: bool = False) -> None:
    actual_id, name, instruction = _resolve_job_id_and_instruction(name_or_id)
    console.print(f"[bold cyan]Generating plan for job:[/] {name} ({actual_id})...")

    from noxai.jobs.planner import generate_plan_for_job
    plan = asyncio.run(generate_plan_for_job(actual_id, instruction, force_template=force_template))

    console.print(f"[green]✓[/] Plan version {plan.version} created (source: [bold]{plan.source}[/bold], status: [yellow]{plan.status}[/yellow]).")
    _render_plan_table(plan.model_dump())
    _render_folder_summaries(plan.model_dump())
    console.print("\n[dim]Run 'nx jobs plan approve <job_id>' to grant scoped approval.[/dim]")


@plan_app.callback()
def plan_main() -> None:
    """Plan creation, inspection, and scoped approval."""
    pass


@plan_app.command(name="generate")
def plan_generate(
    name_or_id: str = typer.Argument(..., help="Job name or ID."),
    force_template: bool = typer.Option(False, "--template", help="Force deterministic template planner."),
) -> None:
    """Generate or regenerate an ordered plan for a job."""
    _do_generate_plan(name_or_id, force_template=force_template)


@plan_app.command(name="show")
def plan_show(name_or_id: str = typer.Argument(..., help="Job name or ID.")) -> None:
    """Show active or draft plan, step list, and folder upload summary."""
    actual_id, name, _ = _resolve_job_id_and_instruction(name_or_id)

    async def _show() -> None:
        async with get_db() as db:
            row = await fetch_one(
                db,
                "SELECT version, source, status, steps_json, created_at FROM job_plans WHERE job_id = ? ORDER BY version DESC LIMIT 1",
                (actual_id,),
            )
            if not row:
                console.print(f"[yellow]No plan exists yet for job '{name}'. Run 'nx jobs plan {actual_id}' to create one.[/yellow]")
                return

            v, src, stat, steps_json, created_at = row
            plan_data = json.loads(steps_json)

            console.rule(f"[bold cyan]Plan: {name} (Version {v})[/bold cyan]")
            console.print(f"[bold]Status:[/]      {'[green]approved[/]' if stat == 'approved' else '[yellow]' + stat + '[/]'}")
            console.print(f"[bold]Source:[/]      {src}")
            console.print(f"[bold]Created:[/]     {created_at}")
            console.print(f"[bold]Description:[/] {plan_data.get('description', '')}\n")

            _render_plan_table(plan_data)
            _render_folder_summaries(plan_data)

    asyncio.run(_show())


@plan_app.command(name="approve")
def plan_approve(
    name_or_id: str = typer.Argument(..., help="Job name or ID to approve plan for."),
    version: int | None = typer.Option(None, "--version", "-v", help="Plan version to approve."),
) -> None:
    """Approve a draft plan, granting scoped authorization for its steps and roots."""
    actual_id, name, _ = _resolve_job_id_and_instruction(name_or_id)

    from noxai.jobs.planner import approve_job_plan
    try:
        plan = asyncio.run(approve_job_plan(actual_id, version=version))
        console.print(f"[green]✓[/] Plan version {plan.version} for job '{name}' approved!")
        console.print("[dim]Scoped execution grant active. Scheduled runs will execute this plan without per-action prompts.[/dim]")
    except Exception as exc:
        console.print(f"[red]Error approving plan:[/] {exc}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Watchdog Repair & Delete Commands (Section 4.3 & 7)
# ---------------------------------------------------------------------------


@jobs_app.command(name="repair")
def jobs_repair(
    dry_run: bool = typer.Option(False, "--dry-run", help="Report orphaned runs without updating DB."),
) -> None:
    """Reconcile orphaned or stalled runs with no heartbeat (>3 min)."""
    from noxai.jobs.watchdog import repair_orphaned_runs

    async def _repair() -> None:
        reconciled = await repair_orphaned_runs(dry_run=dry_run)
        if not reconciled:
            console.print("[green]✓[/] No orphaned or stalled runs found. All job runs are healthy.")
            return

        table = Table(title="Orphaned Job Runs Reconciled" if not dry_run else "Orphaned Job Runs (Dry Run)")
        table.add_column("Run ID", style="dim")
        table.add_column("Job ID", style="cyan")
        table.add_column("Started At", style="yellow")
        table.add_column("Last Heartbeat", style="dim")
        table.add_column("Action", style="bold red" if not dry_run else "bold yellow")

        for r in reconciled:
            table.add_row(
                r["run_id"],
                r["job_id"],
                str(r["started_at"]),
                str(r.get("heartbeat_at") or "none"),
                r["action"],
            )
        console.print(table)
        if dry_run:
            console.print("\n[dim]Run 'nx jobs repair' to apply reconciliation.[/dim]")

    asyncio.run(_repair())


@jobs_app.command(name="delete")
def jobs_delete(
    name_or_id: str = typer.Argument(..., help="Job name or ID to delete."),
    purge: bool = typer.Option(False, "--purge", help="Permanently delete job and records instead of soft-delete."),
) -> None:
    """Delete a job (with cooperative cancellation and soft-delete/purge)."""
    actual_id, name, _ = _resolve_job_id_and_instruction(name_or_id)

    from noxai.jobs.deletion import delete_job
    try:
        res = asyncio.run(delete_job(actual_id, purge=purge))
        action_str = "[bold red]purged[/bold red]" if res["action"] == "purged" else "[yellow]soft-deleted[/yellow]"
        console.print(f"[green]✓[/] Job '{res['name']}' ({res['id']}) {action_str}.")
        if res["running_runs_cancelled"] > 0:
            console.print(f"[dim]Cancelled {res['running_runs_cancelled']} running execution(s).[/dim]")
        if not purge:
            console.print("[dim]Can be restored within 30 days via API or 'nx jobs restore'.[/dim]")
    except Exception as exc:
        console.print(f"[red]Error deleting job:[/] {exc}")
        raise typer.Exit(1)




