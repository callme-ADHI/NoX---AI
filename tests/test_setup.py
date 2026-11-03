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
    assert "Desktop Entry (Launcher)" in result.stdout
    assert "XDG Autostart" in result.stdout
    assert "Hyprland exec-once" in result.stdout
    assert "Hyprland workspace 1" in result.stdout


def test_setup_desktop_install(tmp_path: Path) -> None:
    fake_apps_dir = tmp_path / "applications"
    fake_apps_file = fake_apps_dir / "nox.desktop"
    fake_bin_dir = tmp_path / "bin"
    fake_pixmaps_dir = tmp_path / "pixmaps"
    with patch.object(setup, "APPLICATIONS_DIR", fake_apps_dir), patch.object(
        setup, "APPLICATIONS_FILE", fake_apps_file
    ), patch.object(setup, "USER_BIN_DIR", fake_bin_dir), patch.object(
        setup, "USER_PIXMAPS_DIR", fake_pixmaps_dir
    ):
        result = runner.invoke(app, ["setup", "--desktop"])
        assert result.exit_code == 0
        assert fake_apps_file.exists()
        assert "Name=NoX" in fake_apps_file.read_text()
        assert "Exec=/home/boss/.local/bin/nox-app %U" in fake_apps_file.read_text()


def test_setup_autostart_install(tmp_path: Path) -> None:
    fake_autostart = tmp_path / "autostart" / "nox.desktop"
    fake_apps_file = tmp_path / "applications" / "nox.desktop"
    with patch.object(setup, "AUTOSTART_DIR", tmp_path / "autostart"), patch.object(
        setup, "AUTOSTART_FILE", fake_autostart
    ), patch.object(setup, "APPLICATIONS_FILE", fake_apps_file), patch.object(
        setup, "APPLICATIONS_DIR", tmp_path / "applications"
    ), patch.object(setup, "USER_BIN_DIR", tmp_path / "bin"), patch.object(
        setup, "USER_PIXMAPS_DIR", tmp_path / "pixmaps"
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
