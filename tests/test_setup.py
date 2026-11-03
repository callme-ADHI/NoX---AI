"""Unit tests for nx setup command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from noxai.cli import app
from noxai.commands import setup

runner = CliRunner()


def test_setup_check_mode() -> None:
    result = runner.invoke(app, ["setup", "--check"])
    assert result.exit_code == 0
    assert "NoX Setup & Autostart Status" in result.stdout
    assert "XDG Autostart" in result.stdout
    assert "Hyprland exec-once" in result.stdout
    assert "Hyprland workspace 1" in result.stdout


def test_setup_autostart_install(tmp_path: Path) -> None:
    fake_autostart = tmp_path / "autostart" / "nox.desktop"
    with patch.object(setup, "AUTOSTART_DIR", tmp_path / "autostart"), patch.object(
        setup, "AUTOSTART_FILE", fake_autostart
    ):
        result = runner.invoke(app, ["setup", "--autostart"])
        assert result.exit_code == 0
        assert fake_autostart.exists()
        assert "nox-app --hidden" in fake_autostart.read_text()


def test_setup_hyprland_lua(tmp_path: Path) -> None:
    fake_startup = tmp_path / "startup.lua"
    fake_rules = tmp_path / "windowrules.lua"
    fake_startup.write_text('local exec_once = {\n    "noctalia",\n}\n')
    fake_rules.write_text('-- window rules\n')
    with patch.object(setup, "HYPRLAND_LUA_STARTUP", fake_startup), patch.object(
        setup, "HYPRLAND_LUA_WINDOWRULES", fake_rules
    ):
        result = runner.invoke(app, ["setup", "--hyprland"])
        assert result.exit_code == 0
        content = fake_startup.read_text()
        assert "nox-app --hidden" in content
        rules_content = fake_rules.read_text()
        assert "workspace = \"1\"" in rules_content


def test_setup_hyprland_conf(tmp_path: Path) -> None:
    fake_conf = tmp_path / "hyprland.conf"
    fake_conf.write_text("# Hyprland config\n")
    fake_startup = tmp_path / "nonexistent.lua"
    fake_rules = tmp_path / "nonexistent_rules.lua"
    with patch.object(setup, "HYPRLAND_LUA_STARTUP", fake_startup), patch.object(
        setup, "HYPRLAND_LUA_WINDOWRULES", fake_rules
    ), patch.object(setup, "HYPRLAND_CONF", fake_conf):
        result = runner.invoke(app, ["setup", "--hyprland"])
        assert result.exit_code == 0
        content = fake_conf.read_text()
        assert "exec-once = nox-app --hidden" in content
        assert "windowrulev2 = workspace 1" in content
