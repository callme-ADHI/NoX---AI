"""CLI commands for NoX Briefing Engine (Phase 2 & 5)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx
import typer
from rich.console import Console
from rich.table import Table

from noxai.api.auth import load_session_token
from noxai.config import get_config
from noxai.db import fetch_all, fetch_one, get_db

briefing_app = typer.Typer(help="Manage, trigger, and inspect daily intelligence briefings.")
console = Console()
log = logging.getLogger(__name__)


def _get_api_headers() -> dict[str, str]:
    token = load_session_token() or ""
    return {
        "Authorization": f"Bearer {token}",
        "X-Nox-Client": "cli",
        "Content-Type": "application/json",
    }


@briefing_app.command(name="status")
def briefing_status() -> None:
    """Show briefing scheduler status, next runs, active progress, and recent history."""
    cfg = get_config()
    url = f"http://{cfg.server.host}:{cfg.server.port}/api/briefings/status"
    headers = _get_api_headers()

    data: dict[str, Any]
    try:
        resp = httpx.get(url, headers=headers, timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        console.print(
            f"[yellow]Daemon API unreachable ({exc}). Reading local database status...[/yellow]"
        )
        from noxai.briefing.scheduler import BriefingScheduler

        sched = BriefingScheduler(config=cfg)
        data = asyncio.run(sched.get_status())

    # 1. Overview & Next Runs Table
    overview = Table(title="NoX Briefing Scheduler Status", show_header=True)
    overview.add_column("Property", style="cyan")
    overview.add_column("Value", style="white")

    overview.add_row("Current Time", data.get("now", "unknown"))
    overview.add_row("Timezone", data.get("timezone", "Asia/Kolkata"))

    next_runs = data.get("next_runs", [])
    for nr in next_runs:
        overview.add_row(f"Next {nr.get('slot', '').capitalize()}", nr.get("at", "unknown"))

    console.print(overview)
    console.print()

    # 2. Active Run
    active = data.get("active")
    if active:
        active_tbl = Table(title=f"Active Briefing Run: {active.get('run_id')}", show_header=True)
        active_tbl.add_column("Slot", style="cyan")
        active_tbl.add_column("Revision", style="cyan")
        active_tbl.add_column("Trigger", style="magenta")
        active_tbl.add_column("State", style="yellow")
        active_tbl.add_column("Progress", style="green")

        prog_pct = int(active.get("progress", 0.0) * 100)
        done_tp = active.get("topics_completed", 0)
        tot_tp = active.get("topics_total", 0)
        topics_str = f"{done_tp}/{tot_tp} topics ({prog_pct}%)"
        active_tbl.add_row(
            active.get("slot", "").capitalize(),
            str(active.get("rev", 1)),
            active.get("trigger", "scheduled"),
            active.get("state", "running"),
            topics_str,
        )
        console.print(active_tbl)

        # Topics detail
        topics = active.get("topics", [])
        if topics:
            topics_tbl = Table(title="Per-Topic Live Progress", show_header=True)
            topics_tbl.add_column("Topic", style="cyan")
            topics_tbl.add_column("State", style="yellow")
            topics_tbl.add_column("Items Staged", style="green")
            for tp in topics:
                topics_tbl.add_row(
                    tp.get("name", tp.get("id")), tp.get("state", ""), str(tp.get("items", 0))
                )
            console.print(topics_tbl)
        console.print()
    else:
        console.print("[dim]No briefing run is currently active.[/dim]\n")

    # 3. Last Runs
    last = data.get("last", {})
    if last:
        last_tbl = Table(title="Last Briefing Runs", show_header=True)
        last_tbl.add_column("Slot", style="cyan")
        last_tbl.add_column("Date", style="white")
        last_tbl.add_column("Revision", style="dim")
        last_tbl.add_column("State", style="green")
        last_tbl.add_column("Items", style="white")
        last_tbl.add_column("Generation (s)", style="dim")

        for s in ("morning", "evening"):
            r = last.get(s)
            if r:
                last_tbl.add_row(
                    s.capitalize(),
                    r.get("date", ""),
                    f"rev {r.get('rev', 1)}",
                    r.get("state", ""),
                    str(r.get("sources_count", 0)),
                    f"{r.get('generation_seconds', 0.0):.1f}s",
                )
            else:
                last_tbl.add_row(s.capitalize(), "—", "—", "none", "0", "—")
        console.print(last_tbl)

    # 4. Missed Windows
    missed = data.get("missed", [])
    if missed:
        console.print()
        missed_tbl = Table(title="Missed Windows (Honest History)", show_header=True)
        missed_tbl.add_column("Date", style="yellow")
        missed_tbl.add_column("Slot", style="yellow")
        missed_tbl.add_column("Recorded At", style="dim")
        for m in missed:
            missed_tbl.add_row(
                m.get("date", ""), m.get("slot", "").capitalize(), m.get("created_at", "")
            )
        console.print(missed_tbl)


@briefing_app.command(name="run")
def briefing_run(
    slot: str | None = typer.Option(
        None, "--slot", help="Briefing slot ('morning' or 'evening'); default: current window"
    ),
    wait: bool = typer.Option(False, "--wait", help="Wait and stream progress until completion"),
    force: bool = typer.Option(False, "--force", help="Force run"),
) -> None:
    """Submit an on-demand briefing run to the daemon TaskSupervisor."""
    cfg = get_config()
    url = f"http://{cfg.server.host}:{cfg.server.port}/api/briefings/run"
    headers = _get_api_headers()

    try:
        resp = httpx.post(url, headers=headers, json={"slot": slot, "force": force}, timeout=10.0)
    except Exception as exc:
        console.print(f"[red]Cannot connect to NoX daemon at {url}: {exc}[/red]")
        console.print(
            "[dim]Briefings are managed by the daemon TaskSupervisor. "
            "Start the daemon with 'systemctl --user start noxd'.[/dim]"
        )
        raise typer.Exit(1) from exc

    if resp.status_code not in (200, 202):
        console.print(
            f"[red]Failed to trigger briefing: HTTP {resp.status_code}: {resp.text}[/red]"
        )
        raise typer.Exit(1)

    data = resp.json()
    if data.get("active"):
        console.print(
            f"[yellow]Attaching to active briefing run: [bold]{data.get('run_id')}[/bold] "
            f"({data.get('slot')}, rev {data.get('rev', 1)}, state={data.get('state')})[/yellow]"
        )
    else:
        console.print(
            f"[green]Queued briefing run: [bold]{data.get('run_id')}[/bold] "
            f"(slot={data.get('slot')}, rev={data.get('rev', 1)})[/green]"
        )

    if not wait:
        console.print(
            "[dim]Run continues in background. Inspect via 'nx briefing status' or UI.[/dim]"
        )
        return

    # Wait loop
    status_url = f"http://{cfg.server.host}:{cfg.server.port}/api/briefings/status"
    target_id = data.get("run_id")
    console.print("[cyan]Waiting for briefing completion...[/cyan]")
    while True:
        time.sleep(2.0)
        try:
            s_resp = httpx.get(status_url, headers=headers, timeout=5.0)
            if s_resp.status_code == 200:
                s_data = s_resp.json()
                act = s_data.get("active")
                if not act or act.get("run_id") != target_id:
                    break
                done = act.get("topics_completed", 0)
                tot = act.get("topics_total", 0)
                pct = int(act.get("progress", 0.0) * 100)
                console.print(f"[dim]Progress: {done}/{tot} topics completed ({pct}%)...[/dim]")
        except Exception as exc:
            log.debug("Polling status error: %s", exc)

    console.print(f"[bold green]Briefing run {target_id} finished successfully![/bold green]")


@briefing_app.command(name="list")
def briefing_list(
    limit: int = typer.Option(10, "--limit", "-n", help="Max runs to display"),
) -> None:
    """List recent briefing runs from the database."""

    async def _fetch() -> Any:
        async with get_db() as db:
            return await fetch_all(
                db,
                "SELECT * FROM briefing_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )

    rows = asyncio.run(_fetch())
    if not rows:
        console.print("[dim]No briefings generated yet.[/dim]")
        return

    table = Table(title="NoX Briefing Runs", show_header=True)
    table.add_column("Run ID", style="cyan")
    table.add_column("Date", style="white")
    table.add_column("Slot", style="magenta")
    table.add_column("Rev", style="dim")
    table.add_column("Trigger", style="dim")
    table.add_column("State", style="green")
    table.add_column("Items", style="white")
    table.add_column("Duration", style="dim")
    table.add_column("Created", style="dim")

    for r in rows:
        table.add_row(
            r["id"],
            r["date"],
            r["slot"],
            f"r{r.get('rev', 1)}",
            r.get("trigger", "scheduled"),
            r["state"],
            str(r["sources_count"] or 0),
            f"{r['generation_seconds'] or 0:.1f}s",
            r["created_at"][:19] if r["created_at"] else "",
        )

    console.print(table)


@briefing_app.command(name="show")
def briefing_show(
    briefing_id: str = typer.Argument(..., help="Briefing ID or date (YYYY-MM-DD-slot)"),
) -> None:
    """Display the full markdown briefing."""

    async def _fetch() -> Any:
        async with get_db() as db:
            return await fetch_one(
                db,
                "SELECT * FROM briefing_runs WHERE id = ? OR date = ?",
                (briefing_id, briefing_id),
            )

    row = asyncio.run(_fetch())
    if not row:
        console.print(f"[red]Briefing {briefing_id} not found.[/red]")
        raise typer.Exit(code=1)

    md_path_str = row["markdown_path"]
    if md_path_str:
        from pathlib import Path

        p = Path(md_path_str)
        if p.exists():
            console.print(p.read_text(encoding="utf-8"))
            return

    console.print(f"# {row['title']}\n")
    console.print(row["summary"] or "No summary available.")


@briefing_app.command(name="repair")
def briefing_repair(
    requeue: bool = typer.Option(
        True, "--requeue/--no-requeue", help="Re-queue repaired runs for resume"
    ),
) -> None:
    """Repair stuck or orphaned briefing runs without manual SQL edits (Session K, Phase 0)."""
    from noxai.briefing.scheduler import BriefingScheduler

    sched = BriefingScheduler()
    repaired = asyncio.run(sched.repair_runs(requeue=requeue))
    if not repaired:
        console.print("[green]✓ No stuck or orphaned briefing runs detected.[/green]")
        return

    table = Table(title=f"Repaired Briefing Runs ({len(repaired)})", show_header=True)
    table.add_column("Run ID", style="cyan")
    table.add_column("Slot", style="magenta")
    table.add_column("Date", style="white")
    table.add_column("Rev", style="dim")
    table.add_column("Task Status", style="yellow")
    table.add_column("Requeued", style="green")

    for r in repaired:
        table.add_row(
            r["run_id"],
            r["slot"],
            r["date"],
            f"r{r['rev']}",
            str(r.get("task_status", "none")),
            "Yes" if r.get("requeued") else "No",
        )
    console.print(table)
