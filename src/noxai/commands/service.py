"""systemd user service management and session environment commands for NoX CLI."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from noxai.doctor import SERVICE_UNIT_PATH, SYSTEMD_USER_DIR

service_app = typer.Typer(help="systemd user service management.")
console = Console()


def generate_service_unit_content(repo_dir: Path, venv_dir: Path) -> str:
    noxd_bin = venv_dir / "bin" / "noxd"
    if not noxd_bin.exists():
        noxd_bin = venv_dir / "bin" / "nx"
    doc_path = repo_dir / "docs" / "ARCHITECTURE.md"

    return f"""[Unit]
Description=NoX personal AI agent daemon
Documentation=file://{doc_path.resolve()}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={noxd_bin.resolve()} run
WorkingDirectory={repo_dir.resolve()}
Restart=on-failure
RestartSec=3
TimeoutStopSec=15
Environment=HOME=%h
Environment=XDG_RUNTIME_DIR=/run/user/%U
StandardOutput=journal
StandardError=journal
SyslogIdentifier=noxd
NoNewPrivileges=yes
PrivateTmp=yes

[Install]
WantedBy=default.target
"""


@service_app.command(name="install")
def service_install(
    enable: bool = typer.Option(False, "--enable", help="Enable the service after installation."),
) -> None:
    """Install systemd user service noxd.service with absolute paths resolved from current repo."""
    repo_dir = Path(__file__).resolve().parent.parent.parent.parent
    venv_dir = repo_dir / ".venv"

    noxd_bin = venv_dir / "bin" / "noxd"
    if not noxd_bin.exists():
        noxd_bin = venv_dir / "bin" / "nx"

    if not noxd_bin.exists():
        console.print(
            f"[bold red]Error:[/] ExecStart binary not found at {noxd_bin}. Refusing to install."
        )
        raise typer.Exit(1)

    SYSTEMD_USER_DIR.mkdir(parents=True, exist_ok=True)
    unit_content = generate_service_unit_content(repo_dir, venv_dir)
    SERVICE_UNIT_PATH.write_text(unit_content)

    console.print(f"[green]✓[/] Wrote {SERVICE_UNIT_PATH}")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    console.print("[green]✓[/] Reloaded user systemd daemon.")

    if enable:
        subprocess.run(["systemctl", "--user", "enable", "noxd.service"], check=True)
        console.print("[green]✓[/] Enabled noxd.service.")
    else:
        console.print(
            "[dim]Service installed but NOT enabled. Enable with: nx service install --enable[/dim]"
        )


@service_app.command(name="uninstall")
def service_uninstall() -> None:
    """Stop, disable, and remove noxd.service."""
    subprocess.run(["systemctl", "--user", "stop", "noxd.service"], capture_output=True)
    subprocess.run(["systemctl", "--user", "disable", "noxd.service"], capture_output=True)

    if SERVICE_UNIT_PATH.exists():
        SERVICE_UNIT_PATH.unlink()
        console.print(f"[green]✓[/] Removed {SERVICE_UNIT_PATH}")

    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    console.print("[green]✓[/] Uninstalled noxd.service.")


@service_app.command(name="status")
def service_status() -> None:
    """Display status of noxd.service and ExecStart target."""
    table = Table(title="systemd Service Status: noxd.service", show_header=True)
    table.add_column("Property", style="bold")
    table.add_column("Value")

    exists = SERVICE_UNIT_PATH.exists()
    table.add_row("Unit File", str(SERVICE_UNIT_PATH) if exists else "[red]Not installed[/]")

    if exists:
        try:
            content = SERVICE_UNIT_PATH.read_text()
            for line in content.splitlines():
                if line.strip().startswith("ExecStart="):
                    bin_target = line.strip().split("=", 1)[1].strip().split()[0]
                    bin_target = bin_target.replace("%h", str(Path.home()))
                    target_ok = Path(bin_target).exists()
                    status_str = (
                        f"[green]{bin_target} (exists)[/]"
                        if target_ok
                        else f"[red]{bin_target} (MISSING!)[/]"
                    )
                    table.add_row("ExecStart Target", status_str)
                    break
        except Exception as exc:
            table.add_row("ExecStart Check", f"[red]{exc}[/]")

        try:
            res = subprocess.run(
                ["systemctl", "--user", "is-enabled", "noxd.service"],
                capture_output=True,
                text=True,
            )
            table.add_row("Enabled", res.stdout.strip())
        except Exception:
            table.add_row("Enabled", "unknown")

        try:
            res = subprocess.run(
                ["systemctl", "--user", "is-active", "noxd.service"],
                capture_output=True,
                text=True,
            )
            table.add_row("Active", res.stdout.strip())
        except Exception:
            table.add_row("Active", "unknown")

    console.print(table)


@service_app.command(name="logs")
def service_logs(
    lines: int = typer.Option(50, "-n", "--lines", help="Number of journal log lines."),
) -> None:
    """Show recent journald logs for noxd.service."""
    try:
        subprocess.run(["journalctl", "--user", "-u", "noxd", "-n", str(lines), "--no-pager"])
    except Exception as exc:
        console.print(f"[red]Error viewing logs:[/] {exc}")


def session_env_command() -> None:
    """Export current display and desktop session variables into user systemd and D-Bus."""
    vars_to_export = [
        "WAYLAND_DISPLAY",
        "DISPLAY",
        "XDG_CURRENT_DESKTOP",
        "XDG_SESSION_TYPE",
        "DBUS_SESSION_BUS_ADDRESS",
        "HYPRLAND_INSTANCE_SIGNATURE",
        "SWAYSOCK",
        "NIRI_SOCKET",
        "I3SOCK",
    ]
    present = [v for v in vars_to_export if os.environ.get(v)]

    if not present:
        console.print(
            "[yellow]Warning:[/] No display environment variables found in current session."
        )
        return

    try:
        subprocess.run(
            ["systemctl", "--user", "import-environment", *present],
            check=True,
            capture_output=True,
            timeout=5,
        )
    except Exception as exc:
        console.print(f"[yellow]systemctl import-environment warning:[/] {exc}")

    if shutil.which("dbus-update-activation-environment"):
        try:
            subprocess.run(
                ["dbus-update-activation-environment", "--systemd", *present],
                check=True,
                capture_output=True,
                timeout=5,
            )
        except Exception as exc:
            console.print(f"[yellow]dbus update warning:[/] {exc}")

    v_list = ", ".join(present)
    console.print(
        f"[green]✓[/] Imported session environment ({len(present)} vars: {v_list}) "
        "into systemd user session."
    )
