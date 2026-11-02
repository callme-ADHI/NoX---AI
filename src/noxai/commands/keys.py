"""CLI commands for managing NVIDIA API KeyPool (Phase 3)."""

from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx
import typer
from rich.console import Console
from rich.table import Table

from noxai.config import get_config
from noxai.models.keypool import KeyPool

keys_app = typer.Typer(help="Manage and inspect NVIDIA API Key Pool.")
console = Console()
log = logging.getLogger(__name__)


@keys_app.command(name="status")
def keys_status(
    json_output: bool = typer.Option(False, "--json", help="Output state as JSON"),
    daemon_url: str = typer.Option("http://127.0.0.1:7777", "--daemon-url", help="Daemon API URL"),
) -> None:
    """Display live status, allocation, and metrics of all API keys."""
    rows = None
    source = "local"
    # Try reading from live daemon API first
    try:
        from pathlib import Path
        token_path = Path("~/.local/share/nox/session_token").expanduser()
        headers = {}
        if token_path.exists():
            headers["Authorization"] = f"Bearer {token_path.read_text().strip()}"
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(f"{daemon_url}/api/keys", headers=headers)
            if resp.status_code == 200:
                rows = resp.json()
                source = "daemon"
    except Exception:
        pass

    if rows is None:
        cfg = get_config()
        pool = KeyPool(config=cfg)
        rows = pool.get_status_table()

    if json_output:
        console.print(json.dumps(rows, indent=2))
        return

    if not rows:
        console.print("[yellow]No NVIDIA API keys configured in ~/.config/nox/secrets.env[/yellow]")
        return

    title_suffix = " (Live Daemon API)" if source == "daemon" else " (Local Instance)"
    table = Table(title=f"NVIDIA API Key Pool Status (Phase 3){title_suffix}")
    table.add_column("Key ID", style="cyan")
    table.add_column("Masked Key", style="white")
    table.add_column("Status", style="bold")
    table.add_column("In Flight", justify="right")
    table.add_column("RPM Limit", justify="right")
    table.add_column("Tokens Left", justify="right")
    table.add_column("EWMA (ms)", justify="right")
    table.add_column("Total Req", justify="right")
    table.add_column("429s", justify="right")
    table.add_column("Allocation", style="magenta")

    for r in rows:
        st_style = (
            "green"
            if r["status"] == "ACTIVE"
            else ("yellow" if r["status"] == "COOLDOWN" else "red")
        )
        table.add_row(
            r["key_id"],
            r["masked"],
            f"[{st_style}]{r['status']}[/{st_style}]",
            str(r["in_flight"]),
            str(r["rpm_limit"]),
            str(r["tokens_left"]),
            str(r["ewma_latency_ms"]),
            str(r["total_requests"]),
            str(r["total_429s"]),
            r["assigned_job"],
        )

    console.print(table)


@keys_app.command(name="reload")
def keys_reload() -> None:
    """Hot-reload keys from ~/.config/nox/secrets.env."""
    cfg = get_config()
    pool = KeyPool(config=cfg)
    count = asyncio.run(pool.reload_keys())
    console.print(f"[green]KeyPool successfully reloaded {count} keys from secrets.env[/green]")


@keys_app.command(name="test")
def keys_test() -> None:
    """Execute 1 tiny live test call per key to verify real connectivity."""
    cfg = get_config()
    pool = KeyPool(config=cfg)
    snapshot = pool.get_snapshot()

    if not snapshot.keys:
        console.print("[red]No keys found to test.[/red]")
        return

    console.print(
        f"[bold cyan]Testing {len(snapshot.keys)} keys with live probe to NVIDIA API...[/bold cyan]"
    )

    async def _test_all() -> None:
        async with httpx.AsyncClient(timeout=15.0) as client:
            for k in snapshot.keys:
                t0 = time.monotonic()
                try:
                    resp = await client.post(
                        "https://integrate.api.nvidia.com/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {k.api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": getattr(
                                cfg.models.roles.get("front", None),
                                "model",
                                "nvidia/nemotron-3.5-lightning-30b-a3b",
                            ),  # type: ignore[arg-type]
                            "messages": [{"role": "user", "content": "ping"}],
                            "max_tokens": 1,
                        },
                    )
                    latency = round((time.monotonic() - t0) * 1000.0, 1)
                    if resp.status_code == 200:
                        console.print(
                            f"  [green]PASS[/green] {k.key_id} ({k.masked_display}): "
                            f"HTTP 200 in {latency}ms"
                        )
                    else:
                        console.print(
                            f"  [red]FAIL[/red] {k.key_id} ({k.masked_display}): "
                            f"HTTP {resp.status_code} in {latency}ms"
                        )
                except Exception as exc:
                    latency = round((time.monotonic() - t0) * 1000.0, 1)
                    console.print(
                        f"  [red]ERROR[/red] {k.key_id} ({k.masked_display}): {exc} in {latency}ms"
                    )

    asyncio.run(_test_all())
