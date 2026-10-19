"""Model management and verification commands for NoX CLI."""

from __future__ import annotations

import asyncio
import contextlib
import json
import statistics
import time
from typing import Any

import httpx
import typer
from rich.console import Console
from rich.table import Table

from noxai.config import get_config
from noxai.doctor import _model_present, check_ollama

models_app = typer.Typer(help="Model inspection, testing, and cloud verification.")
console = Console()


@models_app.command(name="list")
def models_list() -> None:
    """List configured model roles, provider bindings, and health."""
    try:
        cfg = get_config()
    except Exception as exc:
        console.print(f"[red]Error loading config:[/] {exc}")
        raise typer.Exit(1) from exc

    table = Table(title="Configured Models & Roles", show_header=True)
    table.add_column("Role", style="bold cyan")
    table.add_column("Provider", style="bold")
    table.add_column("Model Name", style="green")
    table.add_column("Scope", justify="center")
    table.add_column("Health / Status", style="dim")

    ollama_check, ollama_models = check_ollama()

    for role_name, r_cfg in cfg.models.roles.items():
        is_local = r_cfg.provider == "ollama"
        scope = "Local" if is_local else "Cloud"

        if is_local:
            if ollama_check.status == "FAIL":
                health = "[red]Ollama unreachable[/]"
            elif _model_present(r_cfg.model, ollama_models):
                health = "[green]Reachable (installed)[/]"
            else:
                health = "[yellow]Missing in Ollama[/]"
        else:
            if not cfg.nvidia_api_key:
                health = "[yellow]Key unconfigured (disabled)[/]"
            else:
                health = "[green]Key configured[/]"

        table.add_row(role_name, r_cfg.provider, r_cfg.model, scope, health)

    console.print(table)


@models_app.command(name="test")
def models_test(
    role: str = typer.Option(
        "all", "--role", "-r", help="Role to test (brain, heavy, embed, or all)."
    ),
) -> None:
    """Test model execution, measuring TTFT, latency, and cloud fallback."""
    from noxai.models.base import Message
    from noxai.models.router import ModelRouter

    async def _test() -> None:
        cfg = get_config()
        router = ModelRouter(cfg)

        target_roles = ["brain", "heavy", "embed"] if role == "all" else [role]

        for r in target_roles:
            if r == "embed":
                console.print("[bold cyan]Testing embed role...[/]")
                try:
                    prov, m_name = router.resolve_role("embed")
                    t0 = time.perf_counter()
                    embs = await prov.embed(model=m_name, texts=["NoX test probe vector"])
                    dt = (time.perf_counter() - t0) * 1000
                    dim = len(embs[0]) if embs else 0
                    console.print(
                        f"[green]✓[/] Embed role '{r}' ({m_name}): "
                        f"Dimension: [bold]{dim}[/], Time: [bold]{dt:.1f}ms[/]"
                    )
                except Exception as exc:
                    console.print(f"[red]✗ Embed test failed:[/] {exc}")
                continue

            if r not in cfg.models.roles:
                console.print(f"[yellow]Role '{r}' not configured in models.roles.[/]")
                continue

            console.print(f"[bold cyan]Testing '{r}' role chat stream...[/]")
            model_used_record: dict[str, Any] = {}

            def _on_used(info: dict[str, Any], rec: dict[str, Any] = model_used_record) -> None:
                rec.update(info)

            t0 = time.perf_counter()
            first_tok_t = None
            chunks: list[str] = []

            try:
                async for chunk in router.chat(
                    role=r,
                    messages=[Message(role="user", content="Reply with the single word: ok")],
                    stream=True,
                    on_model_used=_on_used,
                ):
                    if first_tok_t is None and chunk.text:
                        first_tok_t = time.perf_counter()
                    chunks.append(chunk.text)

                total_t = (time.perf_counter() - t0) * 1000
                ttft = (first_tok_t - t0) * 1000 if first_tok_t else total_t

                used_model = model_used_record.get("model", r)
                is_local = model_used_record.get("is_local", True)

                reply_txt = "".join(chunks).strip()
                if r == "heavy":
                    if not is_local:
                        exec_note = f"[green]Executed on cloud[/] ({used_model})"
                    else:
                        exec_note = f"[yellow]Fell back to local[/] ({used_model})"
                else:
                    exec_note = f"[green]Local execution[/] ({used_model})"

                path_str = f"    Path: {exec_note} | TTFT: [bold]{ttft:.1f}ms[/] | "
                path_str += f"Total: [bold]{total_t:.1f}ms[/]"
                console.print(
                    f"[green]✓[/] Role '{r}' reply: [italic]'{reply_txt[:30]}'[/italic]\n{path_str}"
                )
            except Exception as exc:
                console.print(f"[red]✗ Chat test for role '{r}' failed:[/] {exc}")

    asyncio.run(_test())


@models_app.command(name="verify-cloud")
def models_verify_cloud() -> None:
    """Verify NVIDIA cloud models API, test tool-calling probe, suggest alternatives on failure."""
    cfg = get_config()
    if not cfg.nvidia_api_key:
        console.print(
            "[yellow]No NVIDIA API key configured.[/yellow] "
            "Add NVIDIA_API_KEY=nvapi-... to ~/.config/nox/secrets.env then chmod 600."
        )
        return

    key = cfg.nvidia_api_key.get_secret_value()
    heavy_model = "nvidia/llama-3.3-nemotron-super-49b-v1.5"
    if "heavy" in cfg.models.roles:
        heavy_model = cfg.models.roles["heavy"].model

    console.print(f"Connecting to NVIDIA API to verify [bold cyan]{heavy_model}[/]...")
    try:
        resp = httpx.get(
            "https://integrate.api.nvidia.com/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=8.0,
        )
        resp.raise_for_status()
        data = resp.json()
        model_ids = [m.get("id", "") for m in data.get("data", [])]

        if heavy_model in model_ids:
            console.print(
                f"[green]✓[/] Configured heavy model '{heavy_model}' verified in NVIDIA /models."
            )

            # Run a lightweight completion probe
            probe_resp = httpx.post(
                "https://integrate.api.nvidia.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": heavy_model,
                    "messages": [{"role": "user", "content": "Reply with 'ok'"}],
                    "max_tokens": 10,
                },
                timeout=10.0,
            )
            if probe_resp.status_code == 200:
                console.print("[green]✓[/] Cloud model probe completed successfully.")
            else:
                console.print(f"[yellow]![/] Model probe returned status {probe_resp.status_code}")
        else:
            console.print(
                f"[yellow]Warning:[/] Model '{heavy_model}' not found in active model list."
            )
            alts = [
                m for m in model_ids if any(k in m.lower() for k in ["nemotron", "llama", "qwen"])
            ][:5]
            if alts:
                console.print("[bold]Suggested alternatives:[/bold]")
                for a in alts:
                    console.print(f"  - {a}")
            console.print(
                "[dim]Config was NOT edited. Update config.toml manually if desired.[/dim]"
            )

    except Exception as exc:
        console.print(f"[bold red]Cloud verification error:[/] {exc}")


@models_app.command(name="privacy-test")
def models_privacy_test() -> None:
    """Verify that PRIVATE content is blocked from cloud with 0 calls; public routes."""
    from noxai.models.base import Message, PrivacyViolation
    from noxai.models.fake import FakeProvider
    from noxai.models.router import ModelRouter
    from noxai.taint import Content, TaintFlag

    async def _run_privacy_test() -> None:
        cfg = get_config()
        fake_cloud = FakeProvider(name="fake_cloud", is_local=False)
        fake_cloud.queue_response("Cloud public answer")
        fake_local = FakeProvider(name="fake_local", is_local=True)
        fake_local.queue_response("Local answer")

        router = ModelRouter(
            config=cfg,
            providers={"ollama": fake_local, "nvidia": fake_cloud},
        )

        console.rule("[bold blue]NoX Privacy Routing Test[/bold blue]")

        # Test 1: Private content must NOT go to cloud
        priv_content = Content("Private owner password and medical notes", taint=TaintFlag.PRIVATE)
        priv_msg = Message(role="user", content=priv_content)

        rejected = False
        try:
            async for _ in router.chat(role="heavy", messages=[priv_msg]):
                pass
        except PrivacyViolation:
            rejected = True

        assert rejected, "Private message was not rejected with PrivacyViolation!"
        assert len(fake_cloud.calls) == 0, (
            f"Cloud provider was called {len(fake_cloud.calls)} times on private data!"
        )
        console.print(
            "[bold green]PASS:[/] Private message blocked from cloud (0 cloud calls made)."
        )

        # Test 2: Public content routes to cloud
        pub_content = Content("What is the speed of light in vacuum?", taint=TaintFlag.NONE)
        pub_msg = Message(role="user", content=pub_content)

        chunks: list[str] = []
        async for c in router.chat(role="heavy", messages=[pub_msg]):
            chunks.append(c.text)

        assert len(fake_cloud.calls) == 1, "Cloud provider was not called for public message!"
        console.print("[bold green]PASS:[/] Public message accepted and routed to cloud provider.")
        console.print("\n[bold green]All privacy routing invariants verified.[/bold green]")

    asyncio.run(_run_privacy_test())


@models_app.command(name="bench-cloud")
def models_bench_cloud(
    runs: int = typer.Option(3, "--runs", "-r", help="Number of benchmark runs per prompt."),
) -> None:
    """Benchmark cloud provider TTFT, throughput, and total latency across standard prompts."""
    import statistics

    from noxai.models.base import Message
    from noxai.models.router import ModelRouter

    cfg = get_config()
    if not cfg.nvidia_api_key or not cfg.nvidia_api_key.get_secret_value():
        console.print("[yellow]Cannot benchmark cloud:[/] NVIDIA_API_KEY is not configured.")
        raise typer.Exit(1)

    model_name = (
        cfg.models.roles["heavy"].model
        if "heavy" in cfg.models.roles
        else "nvidia/llama-3.3-nemotron-super-49b-v1.5"
    )

    prompts = [
        ("Short", "What is the speed of light?"),
        ("Reasoning", "Explain why the sky appears blue in 2 sentences."),
    ]

    async def _run() -> None:
        router = ModelRouter(cfg)

        table = Table(
            title=f"Cloud Benchmark: {model_name} ({runs} runs/prompt)", show_header=True
        )
        table.add_column("Prompt Category", style="bold cyan")
        table.add_column("Prompt", style="dim")
        table.add_column("TTFT (mean ± stdev)", justify="right")
        table.add_column("Total Time (mean ± stdev)", justify="right")
        table.add_column("Tokens (est)", justify="right")
        table.add_column("Throughput (tok/s)", justify="right", style="green")

        for label, text in prompts:
            ttfts: list[float] = []
            totals: list[float] = []
            token_counts: list[int] = []

            for _ in range(max(1, runs)):
                t0 = time.perf_counter()
                ttft_ms: float | None = None
                tokens = 0
                from noxai.taint import Content

                msg = Message(role="user", content=Content.chat(text))
                try:
                    async for chunk in router.chat(
                        role="heavy",
                        messages=[msg],
                        stream=True,
                        max_tokens=128,
                    ):
                        if chunk.text:
                            if ttft_ms is None:
                                ttft_ms = (time.perf_counter() - t0) * 1000.0
                            tokens += len(chunk.text.split())

                    total_ms = (time.perf_counter() - t0) * 1000.0
                    if ttft_ms is not None:
                        ttfts.append(ttft_ms)
                    totals.append(total_ms)
                    token_counts.append(tokens)
                except Exception as exc:
                    console.print(f"[red]Error benchmarking {label}:[/] {exc}")

            if not totals:
                continue

            mean_ttft = statistics.mean(ttfts) if ttfts else 0.0
            std_ttft = statistics.stdev(ttfts) if len(ttfts) > 1 else 0.0
            mean_total = statistics.mean(totals)
            std_total = statistics.stdev(totals) if len(totals) > 1 else 0.0
            avg_toks = statistics.mean(token_counts) if token_counts else 0.0
            tok_per_sec = (avg_toks / (mean_total / 1000.0)) if mean_total > 0 else 0.0

            table.add_row(
                label,
                text[:30] + ("..." if len(text) > 30 else ""),
                f"{mean_ttft:.0f}ms ± {std_ttft:.0f}ms" if ttfts else "N/A",
                f"{mean_total:.0f}ms ± {std_total:.0f}ms",
                f"{avg_toks:.0f}",
                f"{tok_per_sec:.1f} t/s",
            )

        console.print(table)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# nx models bench-local (Phase F6.1)
# ---------------------------------------------------------------------------

JSON_BENCH_PROMPTS = [
    # Reminders (6)
    'Remind me in 10 minutes to stretch. Return JSON: {"action": "remind", "item": "stretch"}',
    'Set reminder at 5pm to call doctor. Return JSON: {"action": "remind", "item": "call doctor"}',
    'Remind me tomorrow at 9am: team meeting. Return JSON: {"action": "remind", "item": "meeting"}',
    'In 2 hours take out trash. Return JSON: {"action": "remind", "item": "trash"}',
    'Remind me Friday noon to submit report. Return JSON: {"action": "remind", "item": "report"}',
    'Don\'t forget to drink water. Return JSON: {"action": "remind", "item": "drink water"}',
    # Intents (7)
    'What is the weather in Paris? Return JSON: {"intent": "reply_now"}',
    'Research recent breakthroughs in fusion. Return JSON: {"intent": "deep_research"}',
    'Run disk cleanup in background. Return JSON: {"intent": "background_task"}',
    'My card number is 4111 2222. Return JSON: {"intent": "sensitive"}',
    'Pause daily briefing job. Return JSON: {"intent": "job_edit"}',
    'Remember I like black coffee. Return JSON: {"intent": "memory_op"}',
    'Schedule morning news at 8am. Return JSON: {"intent": "schedule"}',
    # Facts (7)
    'My favorite editor is Vim. Return JSON: {"attribute": "editor", "value": "Vim"}',
    'I live in Munich. Return JSON: {"attribute": "city", "value": "Munich"}',
    'My dog is a golden retriever Max. Return JSON: {"attribute": "dog", "value": "Max"}',
    'I work as a software architect. Return JSON: {"attribute": "job", "value": "architect"}',
    'My timezone is Europe/Berlin. Return JSON: {"attribute": "timezone", "value": "Berlin"}',
    'I prefer dark mode UI. Return JSON: {"attribute": "theme", "value": "dark mode"}',
    'I have a cat named Luna. Return JSON: {"attribute": "cat", "value": "Luna"}',
]


@models_app.command(name="bench-local")
def models_bench_local(
    models: str | None = typer.Option(
        None,
        "--models",
        "-m",
        help="Comma-separated models to benchmark (defaults to all installed chat models <= 14GB).",
    ),
    runs: int = typer.Option(
        5, "--runs", "-r", help="Warm benchmark iterations per model (default: 5)."
    ),
    json_output: bool = typer.Option(
        False, "--json", help="Output benchmark results in JSON format."
    ),
) -> None:
    """Benchmark and rank local candidate models for resident and brain roles (§F6.1)."""
    from noxai.agent.eval import run_tool_eval
    from noxai.config import ModelRoleConfig
    from noxai.models.base import Message
    from noxai.models.router import ModelRouter

    async def _run() -> None:
        cfg = get_config()
        base_url = cfg.models.ollama.base_url

        # 1. Determine candidate models
        candidate_models: list[str] = []
        if models:
            candidate_models = [m.strip() for m in models.split(",") if m.strip()]
        else:
            try:
                resp = httpx.get(f"{base_url}/api/tags", timeout=5.0)
                if resp.status_code == 200:
                    tag_models = resp.json().get("models", [])
                    for tm in tag_models:
                        name = tm.get("name", "")
                        sz = tm.get("size", 0)
                        # Filter <= 14GB and not embedding model
                        if sz <= 14 * (1024**3) and "embed" not in name.lower():
                            candidate_models.append(name)
            except Exception as exc:
                console.print(f"[yellow]Could not query Ollama tags:[/] {exc}")

        if not candidate_models:
            console.print("[red]No candidate local chat models found to benchmark.[/]")
            raise typer.Exit(1)

        console.print(
            f"[bold cyan]Benchmarking {len(candidate_models)} candidate model(s)[/bold cyan]: "
            f"{', '.join(candidate_models)} ({runs} warm runs each)"
        )

        results: list[dict[str, Any]] = []

        for m_name in candidate_models:
            console.print(f"\n[bold]Evaluating model:[/] [cyan]{m_name}[/cyan]...")

            # Model router configured for this model
            test_cfg = cfg.model_copy(deep=True)
            test_cfg.models.roles["bench_candidate"] = ModelRoleConfig(
                provider="ollama", model=m_name
            )
            router = ModelRouter(test_cfg)

            # Warm-up call
            try:
                async for _ in router.chat(
                    role="bench_candidate",
                    messages=[Message(role="user", content="ping")],
                    max_tokens=1,
                    keep_alive="-1",
                ):
                    pass
            except Exception as exc:
                console.print(f"[red]Warmup failed for {m_name}:[/] {exc}")
                continue

            # A. TTFT & TPS measurement
            bench_prompt = (
                "Explain the difference between a process and a thread in two concise sentences."
            )
            ttfts: list[float] = []
            totals: list[float] = []
            tokens_list: list[int] = []

            for _ in range(max(1, runs)):
                t0 = time.perf_counter()
                ttft_ms: float | None = None
                tokens = 0
                try:
                    async for chunk in router.chat(
                        role="bench_candidate",
                        messages=[Message(role="user", content=bench_prompt)],
                        stream=True,
                        max_tokens=100,
                        keep_alive="-1",
                    ):
                        if chunk.text:
                            if ttft_ms is None:
                                ttft_ms = (time.perf_counter() - t0) * 1000.0
                            tokens += len(chunk.text.split())

                    total_ms = (time.perf_counter() - t0) * 1000.0
                    if ttft_ms is not None:
                        ttfts.append(ttft_ms)
                    totals.append(total_ms)
                    tokens_list.append(tokens)
                except Exception as exc:
                    console.print(f"[red]Inference error on {m_name}:[/] {exc}")

            mean_ttft = statistics.mean(ttfts) if ttfts else 0.0
            mean_total = statistics.mean(totals) if totals else 1.0
            mean_tokens = statistics.mean(tokens_list) if tokens_list else 0.0
            tps = (mean_tokens / (mean_total / 1000.0)) if mean_total > 0 else 0.0

            # B. JSON structured output accuracy (20 prompts)
            json_success = 0
            for p in JSON_BENCH_PROMPTS:
                out_text = ""
                with contextlib.suppress(Exception):
                    async for chunk in router.chat(
                        role="bench_candidate",
                        messages=[Message(role="user", content=p)],
                        stream=False,
                        max_tokens=80,
                        keep_alive="-1",
                    ):
                        if chunk.text:
                            out_text += chunk.text
                    # Clean markdown code blocks if model wrapped JSON
                    cleaned = out_text.strip()
                    if cleaned.startswith("```"):
                        cleaned = cleaned.split("```")[1]
                        if cleaned.startswith("json"):
                            cleaned = cleaned[4:]
                    json.loads(cleaned.strip())
                    json_success += 1

            json_acc = json_success / len(JSON_BENCH_PROMPTS)

            # C. Tool call accuracy
            try:
                eval_res = await run_tool_eval(m_name, console=None)
                tool_passed = eval_res.get("passed", 0)
                tool_total = eval_res.get("total", 9)
                tool_acc = (tool_passed / tool_total) if tool_total > 0 else 0.0
            except Exception:
                tool_acc = 0.0

            # D. Peak resident memory from /api/ps
            res_mem_gb = 0.0
            with contextlib.suppress(Exception):
                ps_resp = httpx.get(f"{base_url}/api/ps", timeout=2.0)
                if ps_resp.status_code == 200:
                    for pm in ps_resp.json().get("models", []):
                        if m_name in pm.get("name", ""):
                            res_mem_gb = (pm.get("size", 0) or pm.get("size_vram", 0)) / (
                                1024**3
                            )

            # E. Composite Score
            # Weight: 35% JSON acc, 35% Tool acc, 20% TPS, 10% TTFT
            score = (
                (json_acc * 35.0)
                + (tool_acc * 35.0)
                + (min(tps / 30.0, 1.0) * 20.0)
                + (max(0.0, 1.0 - (mean_ttft / 2500.0)) * 10.0)
            )

            rec_role = "brain (tool-heavy)" if tool_acc >= 0.88 else "resident (fast extraction)"

            results.append(
                {
                    "model": m_name,
                    "ttft_ms": round(mean_ttft, 1),
                    "tps": round(tps, 1),
                    "json_acc": round(json_acc * 100, 1),
                    "tool_acc": round(tool_acc * 100, 1),
                    "mem_gb": round(res_mem_gb, 2),
                    "score": round(score, 1),
                    "recommended_role": rec_role,
                }
            )

        # Sort results by composite score descending
        results.sort(key=lambda x: x["score"], reverse=True)

        if json_output:
            print(json.dumps(results, indent=2))
            return

        table = Table(
            title="Local Model Bake-Off Results (Ranked §F6.1)",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Rank", justify="center", style="bold")
        table.add_column("Model", style="bold green")
        table.add_column("TTFT", justify="right")
        table.add_column("TPS", justify="right")
        table.add_column("JSON Acc", justify="right")
        table.add_column("Tool Acc", justify="right")
        table.add_column("Peak RAM", justify="right")
        table.add_column("Score", justify="right", style="bold yellow")
        table.add_column("Recommended Role", style="bold magenta")

        for rank, r in enumerate(results, start=1):
            table.add_row(
                str(rank),
                r["model"],
                f"{r['ttft_ms']}ms",
                f"{r['tps']} tok/s",
                f"{r['json_acc']}%",
                f"{r['tool_acc']}%",
                f"{r['mem_gb']} GB" if r["mem_gb"] > 0 else "N/A",
                str(r["score"]),
                r["recommended_role"],
            )

        console.print(table)
        if results:
            best = results[0]
            console.print(
                f"\n[bold green]Winner:[/] [cyan]{best['model']}[/] "
                f"(Score: [yellow]{best['score']}[/]) — recommended for "
                f"[bold magenta]{best['recommended_role']}[/bold magenta]."
            )

    asyncio.run(_run())
