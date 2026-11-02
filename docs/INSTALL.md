# NoX Installation & Arch Linux Packaging Guide

This guide details the complete installation procedure for NoX on Arch Linux with Hyprland.

---

## 1. Prerequisites

### Arch Linux System Packages
Ensure core toolchains and libraries are present:
```bash
sudo pacman -S --needed base-devel git python nodejs npm libnotify sqlite rust cargo webkit2gtk-4.1 libappindicator-gtk3
```

### Local AI Models (Ollama)
Start the Ollama service and pull the required models:
```bash
sudo systemctl enable --now ollama.service

ollama pull nomic-embed-text
ollama pull qwen2.5:14b
ollama pull gemma4:e4b
```

---

## 2. Package Build & Installation (Recommended)

NoX packages into two clean Arch Linux packages (`nox` and `nox-app`) that install directly to `/opt/nox` without polluting system Python or requiring per-user virtual environment activation.

### Step 1: Build the UI and Tauri Desktop App
```bash
git clone https://github.com/callme-ADHI/NoX---AI.git ~/nox
cd ~/nox

# Build frontend UI and Tauri 2 desktop binary
make build-ui
make build-app
```

### Step 2: Build the Arch Packages
```bash
make package
```
This runs `makepkg -f` without root inside `packaging/arch/` and creates:
- `nox-0.1.0.*-x86_64.pkg.tar.zst` (~50 MB, contains `/opt/nox`, `/usr/bin/nx`, `/usr/bin/noxd`, `/usr/lib/systemd/user/noxd.service`)
- `nox-app-0.1.0.*-x86_64.pkg.tar.zst` (~3.8 MB, contains `/usr/bin/nox-app`, `/usr/share/applications/nox.desktop`, `/opt/nox/bin/nox-desktop`)

### Step 3: Verify Packages
Verify package structure, permissions, and systemd units before installation:
```bash
bash scripts/verify-install.sh
```

### Step 4: Install Packages via pacman
```bash
sudo pacman -U packaging/arch/*.pkg.tar.zst
```

---

## 3. Initial Configuration & Autostart

Run the idempotent setup command to configure autostart, Hyprland integration, and persistent background execution:

```bash
# Inspect current system integration
nx setup --check

# Configure all integrations at once:
# - ~/.config/autostart/nox.desktop (XDG Autostart)
# - ~/.config/hypr/startup.lua or hyprland.conf (exec-once = nox-app --hidden)
# - loginctl enable-linger (unattended 04:00 briefing execution)
nx setup --all
```

Enable and start the systemd user daemon:
```bash
systemctl --user enable --now noxd.service
```

Verify complete system readiness:
```bash
nx doctor
```

---

## 4. Launching NoX

- **Desktop Application**: Launch from application launcher (Rofi / Wofi / Noctalia) or run `nox-app`.
- **System Tray**: When started with `--hidden` (e.g. at boot via `nx setup`), NoX resides in the system tray. Left-clicking the tray icon or selecting **Open NoX** displays the main interface.
- **Web UI**: Visit `http://127.0.0.1:7777` in any browser.
- **CLI**: Execute `nx status`, `nx briefing audit`, `nx notes open`, or `nx cloud status`.
