"""NoX CLI notes commands."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess

import typer
from rich.console import Console

from noxai.notes.vault import NotesVault

notes_app = typer.Typer(name="notes", help="Manage and open the NoX Notes Vault.")
console = Console()


@notes_app.command(name="open")
def notes_open() -> None:
    """Print the vault path, obsidian:// open URI, and launch in Obsidian or default editor."""
    vault = NotesVault()
    path_str = str(vault.vault_path)
    uri = vault.get_vault_uri()

    console.print(f"[bold cyan]NoX Notes Vault:[/bold cyan] {path_str}")
    console.print(f"[bold green]Obsidian URI:[/bold green]   {uri}")

    if shutil.which("obsidian"):
        try:
            subprocess.Popen(["obsidian", uri], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            console.print("[dim]Launched Obsidian with URI.[/dim]")
            return
        except Exception:
            pass

    if shutil.which("xdg-open"):
        try:
            subprocess.Popen(["xdg-open", path_str], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            console.print("[dim]Opened vault with xdg-open.[/dim]")
            return
        except Exception:
            pass


@notes_app.command(name="sync")
def notes_sync() -> None:
    """Sync user-edited frontmatter ratings and statuses from Notes Vault back into NoX DB."""
    vault = NotesVault()
    stats = asyncio.run(vault.sync_vault_to_db())
    console.print(
        f"[green]Vault sync complete.[/green] Scanned {stats['scanned']} item notes, "
        f"synced {stats['synced']} rating/feedback changes to database."
    )
