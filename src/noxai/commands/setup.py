"""Setup and autostart automation for NoX on Arch Linux & Hyprland."""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import NamedTuple

import typer
from rich.console import Console
from rich.table import Table

setup_app = typer.Typer(help="Idempotent setup and autostart configuration for NoX.")
console = Console()

AUTOSTART_DIR = Path("~/.config/autostart").expanduser()
AUTOSTART_FILE = AUTOSTART_DIR / "nox.desktop"
HYPRLAND_DIR = Path("~/.config/hypr").expanduser()
HYPRLAND_CONF = HYPRLAND_DIR / "hyprland.conf"
HYPRLAND_LUA_STARTUP = HYPRLAND_DIR / "startup.lua"
HYPRLAND_LUA_WINDOWRULES = HYPRLAND_DIR / "windowrules.lua"


class SetupStatus(NamedTuple):
    component: str
    configured: bool
    details: str


def get_current_user() -> str:
    user = os.environ.get("USER", "")
    if not user:
        try:
            import pwd

            user = pwd.getpwuid(os.getuid()).pw_name
        except Exception:
            user = "current-user"
    return user


def check_autostart_desktop() -> SetupStatus:
    if not AUTOSTART_FILE.exists():
        return SetupStatus("XDG Autostart", False, "Not installed (nox.desktop)")
    try:
        content = AUTOSTART_FILE.read_text()
        if "nox-app" in content or "nx" in content or "nox-desktop" in content:
            return SetupStatus("XDG Autostart", True, "Active and valid")
        return SetupStatus("XDG Autostart", False, "Invalid desktop entry")
    except Exception as exc:
        return SetupStatus("XDG Autostart", False, f"Read error: {exc}")


def check_hyprland_autostart() -> SetupStatus:
    if HYPRLAND_LUA_STARTUP.exists():
        with contextlib.suppress(Exception):
            content = HYPRLAND_LUA_STARTUP.read_text()
            if any(k in content for k in ("nox-app", "nox-desktop")):
                return SetupStatus("Hyprland exec-once", True, "Configured in startup.lua")

    if HYPRLAND_CONF.exists():
        with contextlib.suppress(Exception):
            content = HYPRLAND_CONF.read_text()
            if any(k in content for k in ("nox-app", "nox-desktop")):
                return SetupStatus("Hyprland exec-once", True, "Configured in hyprland.conf")

    return SetupStatus("Hyprland exec-once", False, "Not configured in ~/.config/hypr/")


def check_hyprland_workspace_rule() -> SetupStatus:
    if HYPRLAND_LUA_WINDOWRULES.exists():
        with contextlib.suppress(Exception):
            content = HYPRLAND_LUA_WINDOWRULES.read_text()
            if any(k in content for k in ("nox-desktop", "NoX")) and (
                "workspace" in content and "1" in content
            ):
                return SetupStatus(
                    "Hyprland workspace 1", True, "Pinned to workspace 1 in windowrules.lua"
                )

    if HYPRLAND_CONF.exists():
        with contextlib.suppress(Exception):
            content = HYPRLAND_CONF.read_text()
            if any(k in content for k in ("nox-desktop", "NoX")) and "workspace 1" in content:
                return SetupStatus(
                    "Hyprland workspace 1", True, "Pinned to workspace 1 in hyprland.conf"
                )

    return SetupStatus(
        "Hyprland workspace 1", False, "No workspace 1 rule found in ~/.config/hypr/"
    )


def check_user_linger() -> SetupStatus:
    user = get_current_user()
    try:
        proc = subprocess.run(
            ["loginctl", "show-user", user, "-p", "Linger"],
            capture_output=True,
            text=True,
            check=False,
        )
        if "Linger=yes" in proc.stdout:
            return SetupStatus("User linger (loginctl)", True, "Enabled (daemons persist)")
        return SetupStatus("User linger (loginctl)", False, "Disabled")
    except Exception as exc:
        return SetupStatus("User linger (loginctl)", False, f"loginctl error: {exc}")


def check_systemd_service() -> SetupStatus:
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-enabled", "noxd.service"],
            capture_output=True,
            text=True,
            check=False,
        )
        state = proc.stdout.strip()
        if state == "enabled":
            return SetupStatus("systemd user service", True, "Enabled (noxd.service)")
        return SetupStatus("systemd user service", False, f"State: {state or 'not installed'}")
    except Exception as exc:
        return SetupStatus("systemd user service", False, f"systemctl error: {exc}")


def install_autostart_desktop() -> bool:
    AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
    template = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "packaging"
        / "arch"
        / "nox.desktop"
    )

    desktop_content = """[Desktop Entry]
Name=NoX
Comment=Personal AI Agent for Arch Linux & Hyprland
Exec=nox-app --hidden
Icon=nox
Terminal=false
Type=Application
Categories=Science;ArtificialIntelligence;
StartupWMClass=NoX
X-GNOME-Autostart-enabled=true
"""
    if template.exists():
        with contextlib.suppress(Exception):
            raw = template.read_text()
            if "Exec=" in raw:
                lines = [
                    "Exec=nox-app --hidden" if line.startswith("Exec=") else line
                    for line in raw.splitlines()
                ]
                desktop_content = "\n".join(lines) + "\n"

    AUTOSTART_FILE.write_text(desktop_content)
    return True


def install_hyprland_rules() -> bool:
    HYPRLAND_DIR.mkdir(parents=True, exist_ok=True)
    rule_lua = (
        '\n-- NoX Desktop App — always on workspace 1\n'
        'hl.window_rule({ match = { class = "^(nox-desktop|NoX)$" }, workspace = "1" })\n'
        'hl.window_rule({ match = { class = "^(nox-desktop|NoX)$" }, opacity = 1.0 })\n'
    )
    if HYPRLAND_LUA_WINDOWRULES.exists():
        with contextlib.suppress(Exception):
            content = HYPRLAND_LUA_WINDOWRULES.read_text()
            if "nox-desktop" not in content and "NoX" not in content:
                with HYPRLAND_LUA_WINDOWRULES.open("a") as f:
                    f.write(rule_lua)
    elif HYPRLAND_CONF.exists():
        with contextlib.suppress(Exception):
            content = HYPRLAND_CONF.read_text()
            if "nox-desktop" not in content and "NoX" not in content:
                with HYPRLAND_CONF.open("a") as f:
                    f.write(
                        "\n# NoX Desktop App — always on workspace 1\n"
                        "windowrulev2 = workspace 1, class:^(nox-desktop|NoX)$\n"
                    )
    return True


def install_hyprland_autostart(apply: bool = False) -> bool:
    HYPRLAND_DIR.mkdir(parents=True, exist_ok=True)
    install_hyprland_rules()

    if HYPRLAND_LUA_STARTUP.exists():
        content = HYPRLAND_LUA_STARTUP.read_text()
        if not any(k in content for k in ("nox-app", "nox-desktop")):
            if "local exec_once = {" in content:
                content = content.replace(
                    "local exec_once = {",
                    'local exec_once = {\n    "nox-app --hidden",',
                    1,
                )
                HYPRLAND_LUA_STARTUP.write_text(content)
            else:
                with HYPRLAND_LUA_STARTUP.open("a") as f:
                    f.write('\nhl.exec_once("nox-app --hidden")\n')
    else:
        conf_path = HYPRLAND_CONF
        content = conf_path.read_text() if conf_path.exists() else ""
        if "exec-once = nox-app" not in content and "exec-once = nox-desktop" not in content:
            with conf_path.open("a") as f:
                f.write(
                    "\n# NoX desktop app autostart in system tray\n"
                    "exec-once = nox-app --hidden\n"
                )

    if apply and shutil.which("hyprctl"):
        with contextlib.suppress(Exception):
            subprocess.run(["hyprctl", "reload"], capture_output=True, check=False)
    return True


def enable_user_linger() -> bool:
    user = get_current_user()
    try:
        proc = subprocess.run(
            ["loginctl", "enable-linger", user],
            capture_output=True,
            check=False,
        )
        return proc.returncode == 0
    except Exception:
        return False


@setup_app.callback(invoke_without_command=True)
def setup_main(
    ctx: typer.Context,
    autostart: bool = typer.Option(
        False, "--autostart", help="Configure XDG autostart entry."
    ),
    hyprland: bool = typer.Option(
        False, "--hyprland", help="Configure Hyprland exec-once."
    ),
    apply: bool = typer.Option(
        False, "--apply", help="Apply Hyprland configuration immediately."
    ),
    linger: bool = typer.Option(
        False, "--linger", help="Enable persistent user linger."
    ),
    check: bool = typer.Option(
        False, "--check", help="Check setup status without modifications."
    ),
    all_flags: bool = typer.Option(
        False, "--all", "-a", help="Enable all setup options."
    ),
) -> None:
    """Idempotently configure NoX system integration, autostart, and session persistence."""
    if ctx.invoked_subcommand is not None:
        return

    if check or (not autostart and not hyprland and not linger and not all_flags):
        console.rule("[bold blue]NoX Setup & Autostart Status[/bold blue]")
        statuses = [
            check_autostart_desktop(),
            check_hyprland_autostart(),
            check_hyprland_workspace_rule(),
            check_user_linger(),
            check_systemd_service(),
        ]

        table = Table(show_header=True, header_style="bold")
        table.add_column("Component", style="bold")
        table.add_column("Status", justify="center")
        table.add_column("Details", style="dim")

        for s in statuses:
            badge = (
                "[green]CONFIGURED[/green]" if s.configured else "[yellow]NOT CONFIGURED[/yellow]"
            )
            table.add_row(s.component, badge, s.details)

        console.print(table)
        console.print("\nTo apply setup:")
        console.print("  [bold]nx setup --all[/bold]          Enable all autostart and linger")
        console.print("  [bold]nx setup --autostart[/bold]    Enable XDG autostart desktop entry")
        console.print("  [bold]nx setup --hyprland[/bold]     Append exec-once to Hyprland")
        console.print("  [bold]nx setup --linger[/bold]       Enable persistent background runs")
        return

    if autostart or all_flags:
        if install_autostart_desktop():
            console.print(f"[green]✓[/green] Configured XDG: [bold]{AUTOSTART_FILE}[/bold]")
        else:
            console.print("[red]✗[/red] Failed to configure XDG Autostart")

    if hyprland or all_flags:
        if install_hyprland_autostart(apply=apply):
            console.print("[green]✓[/green] Configured Hyprland exec-once (nox-app --hidden)")
            if apply:
                console.print("[green]✓[/green] Executed [bold]hyprctl reload[/bold]")
        else:
            console.print("[red]✗[/red] Failed to configure Hyprland autostart")

    if linger or all_flags:
        if enable_user_linger():
            console.print("[green]✓[/green] Enabled loginctl user linger")
            console.print(
                "[yellow]![/yellow] Could not enable linger (run 'loginctl enable-linger')"
            )

    console.print("\n[bold green]NoX setup completed successfully![/bold green]")
