"""Cloud provider status, diagnostics, and testing commands for NoX CLI (§1.5)."""

from __future__ import annotations

import asyncio
import json
import time

import typer
from rich.console import Console
from rich.table import Table

from noxai.config import get_config
from noxai.models.base import Message
from noxai.models.budget import RequestBudget
from noxai.models.cloud_health import get_cloud_health
from noxai.models.router import ModelRouter

cloud_app = typer.Typer(help="Cloud model health inspection, status, and testing.")
console = Console()


@cloud_app.command(name="status")
def cloud_status(
    json_output: bool = typer.Option(False, "--json", help="Output status as JSON."),
) -> None:
    """Show current cloud provider state, circuit breaker, and rate budget."""
    try:
        cfg = get_config()
    except Exception as exc:
        console.print(f"[red]Error loading configuration:[/] {exc}")
        raise typer.Exit(1) from exc

    has_key = bool(cfg.nvidia_api_key and cfg.nvidia_api_key.get_secret_value())
    health = get_cloud_health(has_key=has_key)
    health_dict = health.to_dict()

    budget = RequestBudget(requests_per_minute=cfg.models.nvidia.requests_per_minute)
    budget_dict = budget.to_dict()

    if json_output:
        combined = {
            "health": health_dict,
            "budget": budget_dict,
            "configured_model": (
                cfg.models.roles["heavy"].model
                if "heavy" in cfg.models.roles
                else "nvidia/llama-3.3-nemotron-super-49b-v1.5"
            ),
        }
        console.print(json.dumps(combined, indent=2))
        return

    # Table 1: Provider Health & Circuit Breaker
    table_health = Table(title="Cloud Provider Health & Circuit Breaker", show_header=True)
    table_health.add_column("Property", style="bold cyan")
    table_health.add_column("Value", style="green")

    state_color = {
        "active": "[bold green]active[/]",
        "degraded": "[bold yellow]degraded[/]",
        "rate_limited": "[bold yellow]rate_limited[/]",
        "offline": "[bold red]offline[/]",
        "invalid_key": "[bold red]invalid_key[/]",
        "no_key": "[dim yellow]no_key (cloud disabled)[/]",
    }.get(health_dict["state"], health_dict["state"])

    circuit_color = {
        "closed": "[green]CLOSED (healthy)[/]",
        "open": "[red]OPEN (blocking/rerouting)[/]",
        "half_open": "[yellow]HALF_OPEN (probing)[/]",
    }.get(health_dict["circuit"], health_dict["circuit"])

    table_health.add_row("Health State", state_color)
    table_health.add_row("Circuit Breaker", circuit_color)
    table_health.add_row(
        "Interactive Ready", "[green]Yes[/]" if health.is_active() else "[red]No[/]"
    )
    table_health.add_row("Reason", health_dict["reason"])
    table_health.add_row("Consecutive Failures", str(health_dict["consecutive_failures"]))
    table_health.add_row("Cooldown Remaining", f"{health_dict['cooldown_remaining_sec']}s")
    table_health.add_row(
        "Last Latency",
        f"{health_dict['last_latency_sec']}s"
        if health_dict["last_latency_sec"] is not None
        else "N/A",
    )
    table_health.add_row(
        "Last TTFT",
        f"{health_dict['last_ttft_sec']}s" if health_dict["last_ttft_sec"] is not None else "N/A",
    )
    table_health.add_row("Since", health_dict["since"])

    console.print(table_health)

    # Table 2: Request Budget
    table_budget = Table(title="Request Rate Budget (RPM)", show_header=True)
    table_budget.add_column("Budget Metric", style="bold cyan")
    table_budget.add_column("Value", style="magenta")

    table_budget.add_row("Total Capacity", f"{budget_dict['capacity_rpm']} RPM")
    table_budget.add_row("Reserved Interactive", f"{budget_dict['reserved_interactive_rpm']} RPM")
    table_budget.add_row("Tokens Available (Now)", str(budget_dict["tokens_current"]))
    table_budget.add_row("Background Usable", str(budget_dict["background_available"]))
    table_budget.add_row("Total Interactive Requests", str(budget_dict["total_interactive"]))
    table_budget.add_row("Total Background Requests", str(budget_dict["total_background"]))
    table_budget.add_row("Total Rejected Requests", str(budget_dict["total_rejected"]))

    console.print(table_budget)


@cloud_app.command(name="test")
def cloud_test(
    prompt: str = typer.Option(
        "Hello from NoX cloud diagnostic probe.",
        "--prompt",
        "-p",
        help="Prompt to test cloud completion.",
    ),
    stream: bool = typer.Option(True, "--stream/--no-stream", help="Stream response tokens."),
) -> None:
    """Send a live test prompt to the cloud provider to check connectivity, TTFT, and latency."""
    try:
        cfg = get_config()
    except Exception as exc:
        console.print(f"[red]Error loading configuration:[/] {exc}")
        raise typer.Exit(1) from exc

    if not cfg.nvidia_api_key or not cfg.nvidia_api_key.get_secret_value():
        console.print(
            "[yellow]Cannot test cloud:[/] NVIDIA_API_KEY is not configured "
            "in ~/.config/nox/secrets.env."
        )
        raise typer.Exit(1)

    model_name = (
        cfg.models.roles["heavy"].model
        if "heavy" in cfg.models.roles
        else "nvidia/llama-3.3-nemotron-super-49b-v1.5"
    )

    async def _run_test() -> None:
        health = get_cloud_health(has_key=True)
        router = ModelRouter(cfg)

        console.print(f"[bold cyan]Sending test query to cloud model '{model_name}'...[/]")
        t0 = time.perf_counter()
        ttft: float | None = None
        chunks: list[str] = []

        try:
            from noxai.taint import Content

            msg = Message(role="user", content=Content.chat(prompt))
            async for chunk in router.chat(
                role="heavy",
                messages=[msg],
                stream=stream,
                max_tokens=64,
            ):
                if chunk.text:
                    if ttft is None:
                        ttft = (time.perf_counter() - t0) * 1000.0
                    chunks.append(chunk.text)

            total_ms = (time.perf_counter() - t0) * 1000.0
            output = "".join(chunks).strip()

            console.print("[bold green]✓ Cloud test successful![/]")
            console.print(f"  Model: [green]{model_name}[/]")
            console.print(
                f"  TTFT: [bold]{ttft:.1f}ms[/]" if ttft is not None else "  TTFT: [dim]N/A[/]"
            )
            console.print(f"  Total Latency: [bold]{total_ms:.1f}ms[/]")
            console.print(f"  Response: [dim]{output[:120]}[/]")
            console.print(f"  Health State: [green]{health.state.value}[/]")

        except Exception as exc:
            console.print(f"[bold red]✗ Cloud test failed:[/] {exc}")
            console.print(f"  Health State: [red]{health.state.value}[/] ({health.reason})")
            raise typer.Exit(1) from exc

    asyncio.run(_run_test())


@cloud_app.command(name="reload")
def cloud_reload() -> None:
    """Force reload secrets.env and update cloud health state without daemon restart."""
    health = get_cloud_health()
    reloaded = health.check_secrets_file(force=True)
    if reloaded and health.is_active():
        console.print(
            f"[bold green]✓ Secrets reloaded:[/] Cloud state is [green]{health.state.value}[/]."
        )
    else:
        console.print(
            f"[bold yellow]! Secrets reloaded:[/] Cloud state is [yellow]{health.state.value}[/] "
            f"({health.reason})."
        )
