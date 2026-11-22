"""CLI commands for GitHub & Actions Intelligence (`nx gh ...`)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from noxai.connectors.github import GitHubConnector

gh_app = typer.Typer(
    name="gh",
    help="GitHub & Actions Intelligence: workflow triage, watch, linter, and PR/issue summaries.",
    no_args_is_help=True,
)
console = Console()


@gh_app.command(name="repos")
def gh_repos() -> None:
    """List watched repositories and their monitoring settings."""
    async def _run() -> None:
        conn = GitHubConnector()
        repos = await conn.list_watched_repos()
        if not repos:
            console.print("[dim]No watched repositories. Run `nx gh add <owner/repo>`.[/dim]")
            return

        table = Table(title="Watched GitHub Repositories")
        table.add_column("Repository", style="cyan bold")
        table.add_column("Private", style="magenta")
        table.add_column("Actions Watch", style="green")
        table.add_column("PRs Watch", style="yellow")
        table.add_column("Last Checked", style="dim")

        for r in repos:
            table.add_row(
                r["repo_name"],
                "yes" if r["is_private"] else "no",
                "yes" if r["watch_actions"] else "no",
                "yes" if r["watch_prs"] else "no",
                (r["last_check_at"] or "")[:16],
            )
        console.print(table)

    asyncio.run(_run())


@gh_app.command(name="add")
def gh_add(
    repo: str = typer.Argument(..., help="Repository name in format owner/repo"),
    private: bool = typer.Option(
        False, "--private", "-p", help="Treat repository as private (local_only)"
    ),
) -> None:
    """Add a repository to the watch list."""
    async def _run() -> None:
        conn = GitHubConnector()
        await conn.add_watched_repo(repo_name=repo, is_private=private)
        console.print(f"[bold green]✓ Added {repo} to watched repositories.[/bold green]")

    asyncio.run(_run())


@gh_app.command(name="remove")
def gh_remove(
    repo: str = typer.Argument(..., help="Repository name in format owner/repo"),
) -> None:
    """Remove a repository from the watch list."""
    async def _run() -> None:
        conn = GitHubConnector()
        await conn.remove_watched_repo(repo_name=repo)
        console.print(f"[bold green]✓ Removed {repo} from watched repositories.[/bold green]")

    asyncio.run(_run())


@gh_app.command(name="runs")
def gh_runs(
    repo: str = typer.Option("", "--repo", "-r", help="Repository (default: callme-ADHI/NoX---AI)"),
    limit: int = typer.Option(10, "--limit", "-n", help="Number of runs to list"),
) -> None:
    """List recent GitHub Actions runs and conclusions."""
    async def _run() -> None:
        conn = GitHubConnector()
        runs = await conn.get_workflow_runs(repo=repo or None, limit=limit)
        if not runs:
            console.print("[dim]No workflow runs found or unable to connect to GitHub.[/dim]")
            return

        table = Table(title=f"GitHub Actions Runs ({repo or conn.default_repo})")
        table.add_column("ID", style="cyan bold")
        table.add_column("Workflow", style="bold")
        table.add_column("Branch", style="yellow")
        table.add_column("SHA", style="dim")
        table.add_column("Status", style="magenta")
        table.add_column("Conclusion", style="bold")
        table.add_column("Created", style="dim")

        for r in runs:
            c = r["conclusion"]
            color = "green" if c == "success" else ("red" if c == "failure" else "yellow")
            table.add_row(
                r["id"],
                r["name"][:30],
                r["head_branch"],
                r["head_sha"],
                r["status"],
                f"[{color}]{c}[/{color}]",
                (r["created_at"] or "")[:16],
            )
        console.print(table)

    asyncio.run(_run())


@gh_app.command(name="triage")
def gh_triage(
    run_id: str = typer.Argument(..., help="Workflow run ID to triage"),
    repo: str = typer.Option("", "--repo", "-r", help="Repository"),
) -> None:
    """Triage a failed GitHub Actions run and extract root causes."""
    async def _run() -> None:
        conn = GitHubConnector()
        target_repo = repo or conn.default_repo
        with console.status(f"[bold cyan]Triaging run {run_id} on {target_repo}...[/bold cyan]"):
            data = await conn.triage_failed_run(target_repo, run_id)

        console.print(Panel(
            f"[bold red]Failure Category:[/bold red] {data['category'].upper()}\n"
            f"[bold yellow]Failing Step:[/bold yellow] {data['failing_step']}\n\n"
            f"[bold white]Summary:[/bold white]\n{data['summary']}\n\n"
            f"[bold green]Suggested Next Action:[/bold green]\n{data['suggested_action']}",
            title=f"Triage Report — Run {run_id} ({target_repo})",
            border_style="red",
        ))

        if data.get("error_lines"):
            console.print("\n[bold]Key Error Frame Lines:[/bold]")
            for el in data["error_lines"]:
                console.print(f"  [dim red]>[/dim red] {el}")

    asyncio.run(_run())


@gh_app.command(name="watch")
def gh_watch(
    repo: str = typer.Argument("", help="Repository (optional)"),
    timeout: int = typer.Option(300, "--timeout", "-t", help="Timeout in seconds"),
) -> None:
    """Watch an in-progress GitHub Actions run until completion with real-time updates."""
    async def _run() -> None:
        conn = GitHubConnector()
        target_repo = repo or conn.default_repo

        console.print(f"[bold cyan]Watching GitHub Actions on {target_repo}...[/bold cyan]")

        def _on_poll(run_data: dict) -> None:
            c = run_data.get("conclusion")
            s = run_data.get("status")
            console.print(
                f"  Run [cyan]{run_data.get('id')}[/cyan] ({run_data.get('name')}): "
                f"status=[magenta]{s}[/magenta], conclusion=[yellow]{c}[/yellow]"
            )

        res = await conn.watch_actions(
            repo=target_repo,
            timeout_sec=timeout,
            poll_interval_sec=5,
            on_poll=_on_poll,
        )

        conclusion = res.get("conclusion")
        url = res.get("html_url", "")
        if conclusion == "success":
            console.print(f"\n[bold green]✓ Workflow completed successfully: {url}[/bold green]")
        elif conclusion == "failure":
            console.print(f"\n[bold red]✗ Workflow failed: {url}[/bold red]")
            triage = res.get("triage")
            if triage:
                console.print(f"  [bold]Failing Step:[/bold] {triage.get('failing_step')}")
                console.print(f"  [bold]Cause:[/bold] {triage.get('summary')}")
                console.print(f"  [bold]Fix:[/bold] {triage.get('suggested_action')}")
        else:
            console.print(f"\n[bold yellow]Workflow finished with: {conclusion}[/bold yellow]")

    asyncio.run(_run())


@gh_app.command(name="lint")
def gh_lint(
    directory: str = typer.Option(
        ".", "--dir", "-d", help="Directory containing .github/workflows"
    ),
) -> None:
    """Statically lint .github/workflows YAML files for permissions and security issues."""
    root = Path(directory).resolve()
    results = GitHubConnector.lint_repo_workflows(repo_dir=root)

    if not results:
        console.print(f"[dim]No workflow files found under {root / '.github' / 'workflows'}.[/dim]")
        return

    total_findings = 0
    for fname, findings in results.items():
        if not findings:
            console.print(f"[bold green]✓ {fname}:[/bold green] [dim]Clean (no issues)[/dim]")
            continue

        total_findings += len(findings)
        table = Table(title=f"Workflow Lint Findings: {fname}")
        table.add_column("Severity", style="bold")
        table.add_column("Rule", style="cyan")
        table.add_column("Message", style="white")
        table.add_column("Suggested Fix", style="yellow")

        for f in findings:
            sev = f.get("severity", "info")
            sev_color = "red" if sev == "error" else ("yellow" if sev == "warning" else "blue")
            table.add_row(
                f"[{sev_color}]{sev.upper()}[/{sev_color}]",
                f.get("rule", ""),
                f.get("message", ""),
                f.get("suggested_fix", ""),
            )
        console.print(table)

    if total_findings > 0:
        console.print(
            f"\n[bold yellow]Found {total_findings} security/hygiene finding(s).[/bold yellow]"
        )
    else:
        console.print("\n[bold green]All workflows passed static security inspection.[/bold green]")


@gh_app.command(name="prs")
def gh_prs(
    repo: str = typer.Option("", "--repo", "-r", help="Repository"),
    state: str = typer.Option("open", "--state", "-s", help="PR state (open, closed, all)"),
) -> None:
    """Summarize pull requests for a repository."""
    async def _run() -> None:
        conn = GitHubConnector()
        target_repo = repo or conn.default_repo
        prs = await conn.get_pr_summaries(repo=target_repo, state=state)
        if not prs:
            console.print(f"[dim]No {state} pull requests found on {target_repo}.[/dim]")
            return

        table = Table(title=f"Pull Requests ({target_repo})")
        table.add_column("#", style="cyan bold")
        table.add_column("Title", style="bold")
        table.add_column("Author", style="magenta")
        table.add_column("Branch", style="yellow")
        table.add_column("Draft", style="dim")
        table.add_column("Created", style="dim")

        for p in prs:
            table.add_row(
                str(p["number"]),
                p["title"][:40],
                p["author"],
                p["branch"],
                "yes" if p["is_draft"] else "no",
                (p["created_at"] or "")[:16],
            )
        console.print(table)

    asyncio.run(_run())


@gh_app.command(name="issues")
def gh_issues(
    repo: str = typer.Option("", "--repo", "-r", help="Repository"),
) -> None:
    """Summarize open issues and security/Dependabot alerts."""
    async def _run() -> None:
        conn = GitHubConnector()
        target_repo = repo or conn.default_repo
        items = await conn.get_issue_and_alert_summaries(repo=target_repo)
        if not items:
            console.print(f"[dim]No open issues or alerts on {target_repo}.[/dim]")
            return

        table = Table(title=f"Open Issues & Alerts ({target_repo})")
        table.add_column("#", style="cyan bold")
        table.add_column("Type", style="bold")
        table.add_column("Title", style="white")
        table.add_column("Author", style="magenta")
        table.add_column("Labels", style="yellow")
        table.add_column("Created", style="dim")

        for it in items:
            is_alert = it["type"] == "dependabot"
            type_color = "red" if is_alert else "blue"
            table.add_row(
                str(it["number"]),
                f"[{type_color}]{it['type'].upper()}[/{type_color}]",
                it["title"][:40],
                it["author"],
                ", ".join(it.get("labels", []))[:30],
                (it["created_at"] or "")[:16],
            )
        console.print(table)

    asyncio.run(_run())


@gh_app.command(name="rerun")
def gh_rerun(
    run_id: str = typer.Argument(..., help="Workflow run ID to re-run"),
    repo: str = typer.Option("", "--repo", "-r", help="Repository"),
    approval_id: str = typer.Option(
        "", "--approval-id", help="Approval ID if previously requested"
    ),
) -> None:
    """Re-run failed jobs in a workflow run (guarded write)."""
    async def _run() -> None:
        conn = GitHubConnector()
        target_repo = repo or conn.default_repo
        payload = {"repo": target_repo, "run_id": run_id}

        if not approval_id:
            res = await conn.guarded_write("rerun_run", payload)
            console.print(
                f"[bold yellow]⚠️ Re-running workflow requires approval.[/bold yellow]\n"
                f"Approval ID: [bold cyan]{res['approval_id']}[/bold cyan]\n"
                f"Confirmation Code: [bold white]{res['confirmation_code']}[/bold white]\n\n"
                f"To approve and run: `nx approvals approve {res['approval_id']}` then\n"
                f"`nx gh rerun {run_id} --approval-id {res['approval_id']}`"
            )
            return

        res = await conn.guarded_write("rerun_run", payload, approval_id=approval_id)
        console.print(f"[bold green]✓ Re-run executed: {res['status']}[/bold green]")

    asyncio.run(_run())
