"""System built-in tools: time, resource usage, shell runner, notifications, app launcher."""

from __future__ import annotations

import asyncio
import datetime
import shlex
import zoneinfo
from pathlib import Path
from typing import Literal

import psutil
from pydantic import BaseModel, Field

from noxai.tools.registry import ToolContext, ToolResult, tool
from noxai.tools.safety import evaluate_shell_command

# ---------------------------------------------------------------------------
# get_time
# ---------------------------------------------------------------------------


class GetTimeParams(BaseModel):
    model_config = {"extra": "forbid"}

    timezone: str | None = Field(
        default=None,
        description="Optional IANA timezone (e.g. 'Asia/Kolkata'). Defaults to system timezone.",
    )


@tool(
    name="get_time",
    description="Get current date, time, weekday, and timezone.",
    params=GetTimeParams,
    tier="read",
    timeout=5.0,
)
async def get_time(params: GetTimeParams, ctx: ToolContext) -> ToolResult:
    tz_name = params.timezone or ctx.config.timezone.name
    tz: datetime.tzinfo
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = datetime.UTC
        tz_name = "UTC (fallback)"

    now = datetime.datetime.now(tz)
    result_str = (
        f"Date: {now.strftime('%Y-%m-%d')} ({now.strftime('%A')})\n"
        f"Time: {now.strftime('%H:%M:%S')}\n"
        f"Timezone: {tz_name}\n"
        f"ISO: {now.isoformat()}"
    )
    return ToolResult(
        ok=True,
        output=result_str,
        summary=f"Current time: {now.strftime('%Y-%m-%d %H:%M:%S')} {tz_name}",
    )


# ---------------------------------------------------------------------------
# system_info
# ---------------------------------------------------------------------------


class SystemInfoParams(BaseModel):
    model_config = {"extra": "forbid"}


@tool(
    name="system_info",
    description="Get system resource usage: CPU percentage, RAM, disk space, and battery status.",
    params=SystemInfoParams,
    tier="read",
    timeout=10.0,
)
async def system_info(params: SystemInfoParams, ctx: ToolContext) -> ToolResult:
    cpu_percent = psutil.cpu_percent(interval=0.1)
    cpu_count = psutil.cpu_count(logical=True)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    mem_used_mb = mem.used // (1024**2)
    mem_total_mb = mem.total // (1024**2)
    disk_used_gb = disk.used // (1024**3)
    disk_total_gb = disk.total // (1024**3)

    lines = [
        f"CPU: {cpu_percent}% across {cpu_count} cores",
        f"RAM: {mem_used_mb} MB used / {mem_total_mb} MB total ({mem.percent}%)",
        f"Disk (/): {disk_used_gb} GB used / {disk_total_gb} GB total ({disk.percent}%)",
    ]

    battery = psutil.sensors_battery()
    if battery:
        plugged_str = "charging" if battery.power_plugged else "discharging"
        lines.append(f"Battery: {battery.percent}% ({plugged_str})")

    output_text = "\n".join(lines)
    return ToolResult(
        ok=True,
        output=output_text,
        summary=f"CPU: {cpu_percent}%, RAM: {mem.percent}%, Disk: {disk.percent}%",
    )


# ---------------------------------------------------------------------------
# run_shell
# ---------------------------------------------------------------------------


class RunShellParams(BaseModel):
    model_config = {"extra": "forbid"}

    command: str = Field(..., description="The exact shell command to execute.", min_length=1)


@tool(
    name="run_shell",
    description="Execute a shell command safely. Read commands run; write commands need approval.",
    params=RunShellParams,
    tier="read",  # Evaluator dynamically escalates to write or forbidden
    timeout=30.0,
)
async def run_shell(params: RunShellParams, ctx: ToolContext) -> ToolResult:
    cmd_str = params.command.strip()
    tier, reason = evaluate_shell_command(cmd_str)

    if tier == "forbidden":
        return ToolResult(
            ok=False,
            output=f"Command is forbidden by security policy: {reason}",
            summary="Forbidden command blocked",
        )

    # If evaluated tier is write or dangerous, check approval
    if tier in ("write", "dangerous"):
        if ctx.request_approval:
            approved = await ctx.request_approval(
                ctx.run_id,
                "run_shell",
                {"command": cmd_str},
                tier,
                reason,
            )
            if not approved:
                return ToolResult(
                    ok=False,
                    output=f"Command '{cmd_str}' was denied by owner or timed out.",
                    summary="Command denied by owner",
                )
        else:
            return ToolResult(
                ok=False,
                output=(
                    f"Command '{cmd_str}' requires approval ({reason}) "
                    "but no approval handler was available."
                ),
                summary="Command requires approval",
            )

    try:
        argv = shlex.split(cmd_str)
    except Exception as exc:
        return ToolResult(
            ok=False,
            output=f"Failed to parse command arguments: {exc}",
            summary="Malformed command arguments",
        )

    try:
        # Run process directly without shell=True
        proc = await asyncio.create_subprocess_exec(
            argv[0],
            *argv[1:],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        exit_code = proc.returncode

        # Output cap: 20 KB
        max_bytes = 20 * 1024
        out_decoded = stdout[:max_bytes].decode("utf-8", errors="replace")
        err_decoded = stderr[:max_bytes].decode("utf-8", errors="replace")

        res_lines = []
        if out_decoded:
            res_lines.append(out_decoded)
        if err_decoded:
            res_lines.append(f"[stderr]\n{err_decoded}")
        if exit_code != 0:
            res_lines.append(f"[exit code: {exit_code}]")

        full_output = "\n".join(res_lines) if res_lines else "(No output)"
        is_ok = exit_code == 0

        return ToolResult(
            ok=is_ok,
            output=full_output,
            summary=f"Executed: {argv[0]} (exit {exit_code})",
        )
    except FileNotFoundError:
        return ToolResult(
            ok=False,
            output=f"Command not found: {argv[0]}",
            summary=f"Command not found: {argv[0]}",
        )
    except Exception as exc:
        return ToolResult(
            ok=False,
            output=f"Error executing subprocess: {exc}",
            summary="Subprocess error",
        )


# ---------------------------------------------------------------------------
# notify
# ---------------------------------------------------------------------------


class NotifyParams(BaseModel):
    model_config = {"extra": "forbid"}

    title: str = Field(..., description="Notification title.", min_length=1)
    message: str = Field(..., description="Notification body message.", min_length=1)
    urgency: Literal["low", "normal", "critical"] = Field(
        default="normal",
        description="Notification urgency level.",
    )


@tool(
    name="notify",
    description="Send a desktop notification to the owner via notify-send.",
    params=NotifyParams,
    tier="write",
    timeout=5.0,
)
async def notify(params: NotifyParams, ctx: ToolContext) -> ToolResult:
    try:
        proc = await asyncio.create_subprocess_exec(
            "notify-send",
            "-u",
            params.urgency,
            "-a",
            "NoX",
            params.title,
            params.message,
        )
        await proc.communicate()
        return ToolResult(
            ok=(proc.returncode == 0),
            output=f"Notification sent: [{params.title}] {params.message}",
            summary=f"Sent notification: {params.title}",
        )
    except FileNotFoundError:
        return ToolResult(
            ok=False,
            output="notify-send binary not found in PATH.",
            summary="notify-send not found",
        )


# ---------------------------------------------------------------------------
# open_app
# ---------------------------------------------------------------------------


class OpenAppParams(BaseModel):
    model_config = {"extra": "forbid"}

    app_name: str = Field(
        ...,
        description="Name or desktop file of app to launch (e.g. 'firefox', 'kitty', 'thunar').",
        min_length=1,
    )


def _find_desktop_file(app_name: str) -> str | None:
    """Find installed .desktop file matching app_name."""
    name_clean = app_name.lower().removesuffix(".desktop")
    search_dirs = [
        Path("/usr/share/applications"),
        Path("/usr/local/share/applications"),
        Path("~/.local/share/applications").expanduser(),
    ]
    for d in search_dirs:
        if not d.exists():
            continue
        for f in d.glob("*.desktop"):
            if f.stem.lower() == name_clean or name_clean in f.stem.lower():
                return f.name
    return None


@tool(
    name="open_app",
    description="Launch an installed desktop application safely.",
    params=OpenAppParams,
    tier="write",
    timeout=10.0,
)
async def open_app(params: OpenAppParams, ctx: ToolContext) -> ToolResult:
    desktop_file = _find_desktop_file(params.app_name)
    if not desktop_file:
        return ToolResult(
            ok=False,
            output=f"Could not find installed desktop application matching '{params.app_name}'.",
            summary=f"App not found: {params.app_name}",
        )

    # Prefer gtk-launch, fallback to hyprctl dispatch exec
    try:
        proc = await asyncio.create_subprocess_exec("gtk-launch", desktop_file)
        await proc.communicate()
        if proc.returncode == 0:
            return ToolResult(
                ok=True,
                output=f"Launched application '{desktop_file}' via gtk-launch.",
                summary=f"Launched {desktop_file}",
            )
    except FileNotFoundError:
        pass

    try:
        proc = await asyncio.create_subprocess_exec(
            "hyprctl", "dispatch", "exec", desktop_file.removesuffix(".desktop")
        )
        await proc.communicate()
        return ToolResult(
            ok=(proc.returncode == 0),
            output=f"Launched '{desktop_file}' via hyprctl.",
            summary=f"Launched {desktop_file}",
        )
    except FileNotFoundError:
        return ToolResult(
            ok=False,
            output="Neither gtk-launch nor hyprctl found to launch applications.",
            summary="Launcher unavailable",
        )
