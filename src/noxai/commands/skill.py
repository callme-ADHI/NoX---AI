# Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
# commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
# Modified for NoX: CLI commands for skill catalog inspection, installation, and safety toggles (Workstream E).

"""CLI commands for NoX Skills Catalog management."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from noxai.db import get_db, run_migrations
from noxai.skills.catalog import get_skills_catalog

skill_app = typer.Typer(help="Manage reusable agent skills (Workstream E).")
console = Console()


@skill_app.command(name="list")
def skill_list() -> None:
    """List all available skills (shipped and user installed)."""
    async def _run():
        await run_migrations()
        async with get_db() as db:
            catalog = get_skills_catalog()
            await catalog.sync_state(db)
            skills = catalog.list_skills()

            if not skills:
                console.print("[dim]No skills found.[/dim]")
                return

            table = Table(title="NoX Reusable Skills Catalog", show_header=True)
            table.add_column("Name", style="bold cyan", no_wrap=True)
            table.add_column("Version", justify="center", style="dim")
            table.add_column("Enabled", justify="center")
            table.add_column("Scripts Allowed", justify="center")
            table.add_column("Location", style="dim")
            table.add_column("Description")

            for s in skills:
                en_badge = "[bold green]YES[/]" if s.enabled else "[bold red]NO[/]"
                sc_badge = "[bold yellow]YES[/]" if s.allow_scripts else "[dim]no[/]"
                loc = "repo" if "skills" in str(s.folder_path) and "share" not in str(s.folder_path) else "user"
                table.add_row(
                    s.name,
                    s.meta.version,
                    en_badge,
                    sc_badge,
                    loc,
                    s.description[:80] + ("..." if len(s.description) > 80 else ""),
                )
            console.print(table)

    asyncio.run(_run())


@skill_app.command(name="show")
def skill_show(
    name: str = typer.Argument(..., help="Name of the skill to inspect."),
) -> None:
    """Display frontmatter metadata and full instruction body of a skill."""
    catalog = get_skills_catalog()
    catalog.discover_skills()
    skill = catalog.get_skill(name)

    if not skill:
        console.print(f"[red]✗[/] Skill '{name}' not found.")
        raise typer.Exit(1)

    console.print(f"\n[bold cyan]Skill:[/] [bold]{skill.name}[/] [dim](v{skill.meta.version})[/]")
    console.print(f"[bold]Description:[/] {skill.description}")
    console.print(f"[bold]Folder:[/] {skill.folder_path}")
    console.print(f"[bold]Enabled:[/] {skill.enabled}")
    console.print(f"[bold]Scripts Allowed:[/] {skill.allow_scripts}")
    if skill.scripts:
        console.print(f"[bold]Contained Scripts:[/] {', '.join(skill.scripts)}")
    console.print("\n[bold yellow]--- Instruction Body ---[/]\n")
    console.print(Markdown(skill.body))


@skill_app.command(name="enable")
def skill_enable(
    name: str = typer.Argument(..., help="Name of the skill to enable."),
) -> None:
    """Enable a skill for use by the agent."""
    async def _run():
        await run_migrations()
        async with get_db() as db:
            catalog = get_skills_catalog()
            ok = await catalog.enable_skill(name, db=db)
            if ok:
                console.print(f"[bold green]✓[/] Skill '{name}' is now [bold green]enabled[/].")
            else:
                console.print(f"[red]✗[/] Skill '{name}' not found.")
                raise typer.Exit(1)

    asyncio.run(_run())


@skill_app.command(name="disable")
def skill_disable(
    name: str = typer.Argument(..., help="Name of the skill to disable."),
) -> None:
    """Disable a skill."""
    async def _run():
        await run_migrations()
        async with get_db() as db:
            catalog = get_skills_catalog()
            ok = await catalog.disable_skill(name, db=db)
            if ok:
                console.print(f"[yellow]![/] Skill '{name}' is now [dim]disabled[/].")
            else:
                console.print(f"[red]✗[/] Skill '{name}' not found.")
                raise typer.Exit(1)

    asyncio.run(_run())


@skill_app.command(name="allow-scripts")
def skill_allow_scripts(
    name: str = typer.Argument(..., help="Name of the skill."),
    allow: bool = typer.Option(True, "--allow/--deny", help="Allow or deny script execution for this skill."),
) -> None:
    """Toggle executable script safety gate for a skill."""
    async def _run():
        await run_migrations()
        async with get_db() as db:
            catalog = get_skills_catalog()
            ok = await catalog.set_allow_scripts(name, allow, db=db)
            if ok:
                state = "[bold green]ALLOWED[/]" if allow else "[bold red]DENIED[/]"
                console.print(f"[bold]✓[/] Scripts for skill '{name}' are now {state}.")
            else:
                console.print(f"[red]✗[/] Skill '{name}' not found.")
                raise typer.Exit(1)

    asyncio.run(_run())


@skill_app.command(name="install")
def skill_install(
    path: str = typer.Argument(..., help="Directory path or git repository URL of skill package."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm installation without interactive prompt."),
    force: bool = typer.Option(False, "--force", help="Allow installation even with critical security warnings."),
) -> None:
    """Install an external skill package (AST scanned, starts disabled by default)."""
    from noxai.skills.security import SkillSecurityScanner, install_untrusted_skill

    is_remote = path.startswith(("http://", "https://", "git@"))
    if not is_remote:
        p = Path(path).expanduser().resolve()
        if not p.is_dir():
            console.print(f"[red]✗[/] Directory not found: {p}")
            raise typer.Exit(1)
        findings = SkillSecurityScanner.scan_skill_directory(p)
        if findings:
            console.print(f"\n[bold yellow]Security Findings ({len(findings)}):[/]")
            for f in findings:
                sev = f.get("severity", "info")
                color = "red" if sev == "critical" else "yellow" if sev == "warning" else "dim"
                console.print(f"  [{color}]• [{sev.upper()}][/{color}] {f['file']}:{f['line']} - {f['message']}")

    if not yes:
        confirm = typer.confirm("Do you want to proceed with installing this skill?", default=False)
        if not confirm:
            console.print("[dim]Installation aborted.[/dim]")
            return

    async def _install() -> None:
        await run_migrations()
        try:
            res = await install_untrusted_skill(path, allow_critical=force)
            catalog = get_skills_catalog()
            async with get_db() as db:
                await catalog.sync_state(db)

            console.print(
                f"\n[bold green]✓[/] Successfully installed skill '[bold]{res['name']}[/]' (status: disabled)."
            )
            console.print(f"  Installation ID: [dim]{res['id']}[/dim]")
            console.print(f"  Security Findings: {res['findings_count']}")
            console.print(f"  Enable when ready with: [cyan]nx skill enable {res['name']}[/]\n")
        except PermissionError as pe:
            console.print(f"\n[bold red]✗ Security Scan Rejected Installation:[/] {pe}")
            console.print("  Use [yellow]--force[/] to override if you trust this code.\n")
            raise typer.Exit(1)
        except Exception as exc:
            console.print(f"[red]✗[/] Failed installing skill: {exc}")
            raise typer.Exit(1)

    asyncio.run(_install())


@skill_app.command(name="remove")
def skill_remove(
    name: str = typer.Argument(..., help="Name of the skill to remove."),
) -> None:
    """Remove a user installed skill."""
    async def _run():
        await run_migrations()
        async with get_db() as db:
            catalog = get_skills_catalog()
            try:
                ok = await catalog.remove_skill(name, db=db)
                if ok:
                    console.print(f"[bold green]✓[/] Removed skill '{name}'.")
                else:
                    console.print(f"[red]✗[/] Skill '{name}' not found.")
            except PermissionError as pe:
                console.print(f"[red]✗[/] {pe}")

    asyncio.run(_run())
