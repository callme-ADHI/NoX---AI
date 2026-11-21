# Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
# commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
# Modified for NoX: Read-only CLI for inspecting execution traces and latency metrics.

"""CLI commands for inspecting execution traces and metrics (Workstream C)."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from noxai.tracing.recorder import get_trace, get_trace_stats, list_traces

traces_app = typer.Typer(
    name="traces",
    help="Inspect step-level execution traces, latency metrics, and feedback.",
    no_args_is_help=True,
)
console = Console()


@traces_app.command(name="list")
def list_traces_cmd(
    last: int = typer.Option(20, "--last", "-n", help="Number of traces to display"),
    source: str = typer.Option(None, "--source", "-s", help="Filter by source (chat, briefing, job, schedule)"),
    status: str = typer.Option(None, "--status", help="Filter by status (running, success, failed, interrupted)"),
) -> None:
    """List recent execution traces."""
    rows = asyncio.run(list_traces(limit=last, source=source, status=status))
    if not rows:
        console.print("[dim]No traces found.[/]")
        return

    table = Table(title=f"NoX Execution Traces (Last {len(rows)})", header_style="bold magenta")
    table.add_column("Trace ID", style="bold cyan")
    table.add_column("Started (UTC)")
    table.add_column("Source", style="green")
    table.add_column("Status")
    table.add_column("Engine / Model", style="dim")
    table.add_column("Steps", justify="right")
    table.add_column("Tokens", justify="right", style="dim")
    table.add_column("Feedback", style="yellow")

    for r in rows:
        st = r.get("final_status", "-")
        if st == "success":
            st_fmt = "[green]success[/green]"
        elif st in ("failed", "interrupted"):
            st_fmt = f"[red]{st}[/red]"
        elif st == "running":
            st_fmt = "[yellow]running[/yellow]"
        else:
            st_fmt = st

        started = str(r.get("started_at") or "-")[:19]
        eng = r.get("engine") or "-"
        mod = r.get("model") or "-"
        eng_mod = f"{eng}/{mod}" if eng != "-" else "-"

        steps = r.get("steps") or []
        step_cnt = str(len(steps)) if isinstance(steps, list) else "-"

        tok = r.get("token_counts")
        tok_str = str(tok.get("total_tokens", "-")) if isinstance(tok, dict) else "-"

        fb = r.get("feedback")
        fb_str = "-"
        if isinstance(fb, dict):
            rating = fb.get("rating", "")
            rating_fmt = "[green]like[/green]" if rating == "like" else "[red]dislike[/red]"
            fb_str = rating_fmt

        table.add_row(
            str(r.get("id")),
            started,
            str(r.get("source")),
            st_fmt,
            eng_mod,
            step_cnt,
            tok_str,
            fb_str,
        )

    console.print(table)


@traces_app.command(name="show")
def show_trace_cmd(
    trace_id: str = typer.Argument(..., help="Trace ID to inspect (e.g. 'tr_1234')"),
) -> None:
    """Show detailed breakdown of a single execution trace."""
    trace = asyncio.run(get_trace(trace_id))
    if not trace:
        console.print(f"[red]Error:[/] Trace '{trace_id}' not found.")
        raise typer.Exit(code=1)

    console.print(Panel(f"[bold cyan]Trace:[/] {trace['id']}", expand=False))
    console.print(f"[bold]Source:[/] {trace.get('source')}  |  [bold]Status:[/] {trace.get('final_status')}")
    console.print(f"[bold]Started:[/] {trace.get('started_at')}  |  [bold]Finished:[/] {trace.get('finished_at')}")
    console.print(f"[bold]Engine:[/] {trace.get('engine')}  |  [bold]Model:[/] {trace.get('model')}  |  [bold]Key Slot:[/] {trace.get('key_slot_index')}")

    if trace.get("request_text"):
        console.print(Panel(trace["request_text"], title="Request Text", border_style="dim"))

    steps = trace.get("steps") or []
    if isinstance(steps, list) and steps:
        table = Table(title="Execution Steps", header_style="bold blue")
        table.add_column("Step Name")
        table.add_column("Tool / Skill", style="dim")
        table.add_column("Duration (ms)", justify="right")
        table.add_column("Status")

        for s in steps:
            st = s.get("status", "success")
            st_fmt = "[green]ok[/green]" if st == "success" else f"[red]{st}[/red]"
            table.add_row(
                str(s.get("name")),
                str(s.get("tool_or_skill") or "-"),
                str(s.get("duration_ms", 0.0)),
                st_fmt,
            )
        console.print(table)

    if trace.get("error_text"):
        console.print(Panel(f"[red]{trace['error_text']}[/red]", title="Error Details", border_style="red"))

    if trace.get("feedback"):
        console.print(f"[bold]Feedback:[/] {trace['feedback']}")


@traces_app.command(name="stats")
def stats_traces_cmd(
    days: int = typer.Option(7, "--days", "-d", help="Number of preceding days to aggregate"),
) -> None:
    """Show aggregate performance, failure, fallback, and telemetry statistics."""
    from noxai.agents.optimizer import TelemetryTracker

    stats = asyncio.run(get_trace_stats(days=days))
    telemetry = asyncio.run(TelemetryTracker.get_metrics(hours=days * 24))

    table = Table(
        title=f"Trace & Telemetry Statistics (Last {days} Days)",
        header_style="bold green",
    )
    table.add_column("Metric", style="bold")
    table.add_column("Value")

    table.add_row("Total Traces", str(stats.get("total_traces", 0)))
    table.add_row("Median Duration", f"{stats.get('median_duration_ms', 0)} ms")
    table.add_row("P90 Latency", f"{telemetry.get('p90_latency_ms', 0)} ms")
    table.add_row("Total Tokens", str(telemetry.get("total_tokens", 0)))
    table.add_row("Estimated Cost (USD)", f"${telemetry.get('estimated_cost_usd', 0.0):.4f}")
    table.add_row("Fallback Invocations", str(stats.get("fallback_count", 0)))

    by_src = stats.get("by_source", {})
    src_str = ", ".join(f"{k}: {v}" for k, v in by_src.items()) if by_src else "none"
    table.add_row("By Source", src_str)

    by_st = stats.get("by_status", {})
    st_str = ", ".join(f"{k}: {v}" for k, v in by_st.items()) if by_st else "none"
    table.add_row("By Status", st_str)

    routes = telemetry.get("route_distribution", {})
    routes_str = ", ".join(f"{k}: {v}" for k, v in routes.items()) if routes else "none"
    table.add_row("Route Distribution", routes_str)

    console.print(table)

