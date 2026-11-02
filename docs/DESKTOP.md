# NoX Desktop App ("NoX") — Tauri 2 on Arch Linux & Hyprland

This document describes the design, architecture, and behavior of the native NoX desktop application.

---

## 1. Overview

The NoX desktop client is built on **Tauri 2** using native WebKit2GTK on Wayland. It serves as the primary desktop entry point for NoX on Arch Linux running Hyprland.

- **Window Title**: `NoX`
- **Window Class**: `nox-desktop` (mapped cleanly for Hyprland window rules)
- **Wayland Client**: Native Wayland execution (`xwayland: 0` verified)
- **Idle Memory Footprint**: ~166 MB RSS
- **Binary Location**: `/opt/nox/bin/nox-desktop` (symlinked from `/usr/bin/nox-app`)

---

## 2. Core Behaviors & Lifecycle

### Night-Sky Splash Screen & Health Polling
When launched, NoX displays a bundled night-sky `#05060F` splash screen (`desktop/src/index.html`) featuring an animated pulsing lightning bolt and stars.
1. The client immediately begins polling `http://127.0.0.1:7777/api/health`.
2. If the daemon is not running after 3 seconds, the desktop app automatically starts `noxd` via systemd (`systemctl --user start noxd.service`).
3. Once the daemon is healthy, the app retrieves the local session token via secure Tauri IPC (`get_session_token`) and stores it into `sessionStorage['nox_token']` before navigating into the main UI. **Session tokens are never exposed in URL query parameters.**
4. If the daemon remains unreachable, an offline state card appears with an interactive **Retry Connection** button and a direct **Start Daemon** action.

### Single-Instance Enforcement & Routing
NoX enforces single-instance execution via `tauri-plugin-single-instance`:
- Running `nox-app` when an instance is already running focuses the active instance window.
- Running `nox-app --open <path>` (e.g. `nox-app --open /briefings`) routes the active window immediately to that view and brings it to focus.
- Running `nox-app --hidden` launches the application directly into the system tray without opening a window (used for system autostart).

### Window Close & Tray Persistence
Closing the application window via the window manager (`Alt+Q`, `Super+C`, or clicking the close button) intercepts the `CloseRequested` event:
- The window is hidden (`window.hide()`).
- The process and the system tray remain running in the background.
- The background daemon is **not** stopped.

### System Tray Menu
The system tray icon offers quick actions:
1. **Open NoX**: Brings window to foreground.
2. **Run briefing now**: Triggers immediate briefing run via daemon API.
3. **Next briefing**: Displays scheduled morning/evening briefing times.
4. **Last briefing**: Navigates to `/briefings` and opens the latest digest.
5. **Cloud toggle**: Toggles NVIDIA NIM cloud capabilities on/off.
6. **Open Notes Vault**: Opens `~/Documents/NoX-Vault/` in Obsidian or system editor.
7. **Quit app**: Closes the desktop window and tray icon without stopping `noxd`.
8. **Stop daemon and quit**: Stops `noxd.service` and quits the application.

---

## 3. Hyprland Integration

### Window Rules
Add the following window rule to `~/.config/hypr/windowrules.lua` or `~/.config/hypr/hyprland.conf`:
```ini
windowrulev2 = float, class:^(nox-desktop)$
windowrulev2 = size 1280 820, class:^(nox-desktop)$
windowrulev2 = center, class:^(nox-desktop)$
```

### Autostart
Use `nx setup --hyprland --apply` to automatically append autostart to your Hyprland configuration.
