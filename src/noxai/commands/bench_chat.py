"""Benchmark command for measuring and comparing chat latency across paths (Phase F1.2)."""

from __future__ import annotations

import statistics
import subprocess
import time
from typing import Any

from rich.console import Console
from rich.table import Table

from noxai.agent.loop import AgentLoop
from noxai.config import get_config
from noxai.db import get_db, run_migrations
from noxai.events import global_event_bus
from noxai.models.router import ModelRouter
from noxai.tools.registry import default_registry

BENCH_PROMPTS = [
    ("Greeting", "Hello, what can you do?"),
    ("Factual", "What is the capital of France?"),
    ("Reminder", "Remind me in 10 minutes to take a break"),
    ("Job Edit", "Change my morning briefing to 8am"),
    ("Disk Usage", "What is using the most disk space on my system?"),
    ("Deep Research", "Give me a detailed comparison of modern Linux desktop environments"),
]


def get_ollama_ps() -> str:
    """Return raw output of ollama ps."""
    try:
        res = subprocess.run(["ollama", "ps"], capture_output=True, text=True, check=False)
        return res.stdout.strip() or "(no models loaded)"
    except Exception as exc:
        return f"Error executing ollama ps: {exc}"


async def measure_raw_ollama(
    model: str,
    base_url: str,
    prompt: str,
) -> tuple[float, float, int]:
    """Measure raw Ollama chat: returns (ttft_ms, total_ms, prompt_tokens_est)."""
    import httpx

    url = f"{base_url.rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "options": {"num_predict": 128, "num_ctx": 2048},
    }
    prompt_tokens_est = len(prompt) // 4

    start = time.perf_counter()
    first_token_time: float | None = None

    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", url, json=payload) as response:
            async for line in response.aiter_lines():
                if line.strip() and first_token_time is None:
                    first_token_time = time.perf_counter()

    end = time.perf_counter()
    ttft_ms = ((first_token_time - start) * 1000) if first_token_time else ((end - start) * 1000)
    total_ms = (end - start) * 1000
    return ttft_ms, total_ms, prompt_tokens_est


async def run_chat_bench(
    console: Console | None = None,
    runs_per_case: int = 3,
) -> dict[str, Any]:
    """Execute chat latency benchmark suite across 6 standard prompts."""
    c = console or Console()
    cfg = get_config()
    await run_migrations()
    router = ModelRouter(config=cfg)

    brain_model = cfg.models.roles.get("brain", None)
    model_name = brain_model.model if brain_model else "qwen2.5:14b"
    base_url = cfg.models.ollama.base_url

    c.print("\n[bold cyan]=== NoX Chat Latency Benchmark (Phase F1) ===[/bold cyan]\n")
    c.print("[bold yellow]Ollama State Before Benchmark:[/bold yellow]")
    ps_before = get_ollama_ps()
    c.print(f"[dim]{ps_before}[/dim]\n")

    cloud_available = router.is_cloud_available()
    results: list[dict[str, Any]] = []

    # Warm-up check
    c.print(f"Checking brain model warm-up: [cyan]{model_name}[/cyan]...")
    w_start = time.perf_counter()
    try:
        await measure_raw_ollama(model_name, base_url, "test")
        c.print(f"Warm-up complete in {(time.perf_counter() - w_start):.2f}s.\n")
    except Exception as exc:
        c.print(f"[bold red]Warning: warm-up call failed:[/] {exc}\n")

    for category, prompt in BENCH_PROMPTS:
        c.print(f"Benchmarking [bold]{category}[/]: [italic]\"{prompt}\"[/]...")

        # 1. Raw Ollama baseline
        raw_ttfts, raw_totals = [], []
        for _ in range(runs_per_case):
            try:
                ttft, total, _ = await measure_raw_ollama(model_name, base_url, prompt)
                raw_ttfts.append(ttft)
                raw_totals.append(total)
            except Exception as exc:
                c.print(f"  [red]Raw Ollama error:[/] {exc}")
                raw_ttfts.append(0.0)
                raw_totals.append(0.0)

        # 2. NoX Local Path
        local_ttfts, local_totals, local_tokens = [], [], []
        local_traces: list[dict[str, Any]] = []
        for _ in range(runs_per_case):
            async with get_db() as db:
                loop = AgentLoop(
                    config=cfg,
                    router=router,
                    registry=default_registry,
                    event_bus=global_event_bus,
                )
                res = await loop.run(
                    user_message=prompt,
                    allow_cloud=False,
                    max_tokens=128,
                    db=db,
                )
                tr = res.trace or {}
                local_ttfts.append(tr.get("time_to_first_token", res.duration_seconds * 1000))
                local_totals.append(res.duration_seconds * 1000)
                local_tokens.append(tr.get("prompt_tokens_est", 0))
                local_traces.append(tr)

        # 3. NoX Cloud Path (if key available)
        cloud_ttfts, cloud_totals = [], []
        if cloud_available:
            from noxai.orchestrator import orchestrate_chat_turn

            for _ in range(runs_per_case):
                res = await orchestrate_chat_turn(
                    prompt,
                    allow_cloud=True,
                    router=router,
                    config=cfg,
                )
                cloud_ttfts.append(
                    res.trace.get("time_to_first_token", res.duration_seconds * 1000)
                )
                cloud_totals.append(res.duration_seconds * 1000)

        # Medians
        median_raw_ttft = statistics.median(raw_ttfts) if raw_ttfts else 0.0
        median_raw_total = statistics.median(raw_totals) if raw_totals else 0.0
        median_local_ttft = statistics.median(local_ttfts) if local_ttfts else 0.0
        median_local_total = statistics.median(local_totals) if local_totals else 0.0
        median_tokens = int(statistics.median(local_tokens)) if local_tokens else 0

        # Typical local trace (median total run)
        best_idx = 0
        if local_totals:
            best_idx = min(
                range(len(local_totals)),
                key=lambda i: abs(local_totals[i] - median_local_total),
            )
        rep_trace = local_traces[best_idx] if local_traces else {}

        results.append(
            {
                "category": category,
                "prompt": prompt,
                "raw_ttft": median_raw_ttft,
                "raw_total": median_raw_total,
                "local_ttft": median_local_ttft,
                "local_total": median_local_total,
                "cloud_ttft": statistics.median(cloud_ttfts) if cloud_ttfts else None,
                "cloud_total": statistics.median(cloud_totals) if cloud_totals else None,
                "prompt_tokens_est": median_tokens,
                "trace": rep_trace,
            }
        )

    # Print summary comparison table
    table = Table(title="Chat Benchmark Comparison (Medians across 3 runs)", show_header=True)
    table.add_column("Category", style="cyan")
    table.add_column("Tokens", justify="right")
    table.add_column("Raw TTFT (ms)", justify="right")
    table.add_column("Local TTFT (ms)", justify="right")
    table.add_column("TTFT Ratio", justify="right", style="bold")
    table.add_column("Raw Total (s)", justify="right")
    table.add_column("Local Total (s)", justify="right")
    table.add_column("Cloud TTFT (ms)", justify="right")

    for r in results:
        ratio = (r["local_ttft"] / r["raw_ttft"]) if r["raw_ttft"] > 0 else 0.0
        cloud_str = f"{r['cloud_ttft']:.1f}" if r["cloud_ttft"] is not None else "N/A"
        table.add_row(
            r["category"],
            str(r["prompt_tokens_est"]),
            f"{r['raw_ttft']:.1f}",
            f"{r['local_ttft']:.1f}",
            f"{ratio:.2f}x",
            f"{r['raw_total'] / 1000:.2f}s",
            f"{r['local_total'] / 1000:.2f}s",
            cloud_str,
        )

    c.print()
    c.print(table)

    # Stage breakdown table
    breakdown_table = Table(title="NoX Local Path Stage Breakdown (ms)", show_header=True)
    breakdown_table.add_column("Category", style="cyan")
    breakdown_table.add_column("Memory Recall", justify="right")
    breakdown_table.add_column("Prompt Build", justify="right")
    breakdown_table.add_column("TTFT", justify="right")
    breakdown_table.add_column("Generation", justify="right")
    breakdown_table.add_column("Tool Time", justify="right")
    breakdown_table.add_column("Extraction", justify="right")
    breakdown_table.add_column("Total", justify="right", style="bold")

    for r in results:
        tr = r["trace"]
        tools_sum = sum(tr.get("tool_time", {}).values())
        breakdown_table.add_row(
            r["category"],
            f"{tr.get('memory_recall', 0.0):.1f}",
            f"{tr.get('prompt_build', 0.0):.1f}",
            f"{tr.get('time_to_first_token', 0.0):.1f}",
            f"{tr.get('generation', 0.0):.1f}",
            f"{tools_sum:.1f}",
            f"{tr.get('post_run_extraction', 0.0):.1f}",
            f"{tr.get('total', 0.0):.1f}",
        )

    c.print()
    c.print(breakdown_table)

    c.print("\n[bold yellow]Ollama State After Benchmark:[/bold yellow]")
    ps_after = get_ollama_ps()
    c.print(f"[dim]{ps_after}[/dim]\n")

    return {
        "results": results,
        "ps_before": ps_before,
        "ps_after": ps_after,
    }
