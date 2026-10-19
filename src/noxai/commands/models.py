"""Model management and verification commands for NoX CLI."""

from __future__ import annotations

import asyncio
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
                    f"[green]✓[/] Role '{r}' reply: [italic]'{reply_txt[:30]}'[/italic]\n"
                    f"{path_str}"
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
