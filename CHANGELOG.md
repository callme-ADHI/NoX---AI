# Changelog

All notable changes to NoX will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.0] - 2026-11-02 (Session K Release)

### Added
- **Native Tauri 2 Desktop Application (`nox-app`)**:
  - Bundled night-sky `#05060F` splash screen with animated pulsing lightning bolt, health polling, automatic daemon launch after 3s, and offline error state with retry.
  - Native Wayland client execution under Arch Linux / Hyprland (`xwayland: 0` verified, ~166 MB idle RSS).
  - Single-instance IPC protocol supporting deep linking (`--open <path>`) and background tray start (`--hidden`).
  - System Tray menu with 10 interactive controls (Open NoX, Run briefing now, Next/Last briefing, Cloud toggle, Open Notes Vault, Quit app, Stop daemon).
  - Secure session token exchange via Tauri IPC (`get_session_token`) stored directly in `sessionStorage['nox_token']` with zero URL exposure.
- **Arch Linux Native Packaging (`/opt/nox`)**:
  - `packaging/arch/PKGBUILD` building `nox` (daemon, CLI, systemd unit, icons) and `nox-app` (native desktop client, desktop entry).
  - Global PATH wrappers `/usr/bin/nx`, `/usr/bin/noxd`, and `/usr/bin/nox-app` eliminating virtual environment activation friction.
  - User verification script `scripts/verify-install.sh`.
- **System Integration Command (`nx setup`)**:
  - Idempotent setup for XDG Autostart (`~/.config/autostart/nox.desktop`), Hyprland `exec-once` with `--apply` reload, and `loginctl enable-linger`.
  - Comprehensive inspection mode with `nx setup --check`.
- **Automatic Database Backups (`nx backup`)**:
  - Automatic timestamped backups before migrations and operations in `~/.local/share/nox/backups/`.
  - Retention management keeping the last 10 backups.
  - CLI commands `nx backup create`, `nx backup list`, and `nx backup restore <file>`.
- **Extended Diagnostic Doctor Checks (`nx doctor`)**:
  - Added checks for desktop app installation and path resolution.
  - Added checks for autostart configuration in XDG autostart and Hyprland.
  - Added checks for notification daemon D-Bus actions.
  - Added checks for database backup health and recency.
  - Added checks for briefing scheduler reliability (last 6 slots).
- **Authoritative Daily Briefings & Opportunity Intelligence**:
  - Authoritative clock at 04:00 (Morning) and 18:00 (Evening) with sleep drift detection and boot catch-up.
  - Missed window auditing recording unvarnished `state='missed'` audit rows.
  - Enforced Evidence Rule (verbatim quote <= 25 words from official page for all benefits).
  - Funded internships (accommodation, stipend, travel) and individual hackathons parsed from event listings.
  - Multi-factor 0–100 value scoring and strict CTF challenge cap (<= 3).
- **Bidirectional Obsidian Vault Sync**:
  - Syncs directly to `~/Documents/NoX-Vault/` preserving user notes outside atomic markers.

### Changed
- Standardized migration 010 schema convergence.
- Enabled Cloud burst research by default with resilient 5-key KeyPool allocator, circuit breakers, and strict taint protection.
