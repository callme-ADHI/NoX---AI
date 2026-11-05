# NoX Session K — Architecture & Implementation Decisions

This document records all architectural decisions, trade-offs, and clarifications made during Session K pursuant to Rule 11.

---

## Decision Index

1. [ADR-K1: Arch Linux Packaging Hierarchy in /opt/nox](#adr-k1-arch-linux-packaging-hierarchy-in-optnox)
2. [ADR-K2: Native Tauri 2 Desktop Client on Wayland](#adr-k2-native-tauri-2-desktop-client-on-wayland)
3. [ADR-K3: Session Token Auth via Secure IPC (Zero URL Exposure)](#adr-k3-session-token-auth-via-secure-ipc-zero-url-exposure)
4. [ADR-K4: Single-Instance Enforcement & Deep Routing Protocol](#adr-k4-single-instance-enforcement--deep-routing-protocol)
5. [ADR-K5: Idempotent Setup Command (`nx setup`)](#adr-k5-idempotent-setup-command-nx-setup)
6. [ADR-K6: Automatic Timestamped Database Backups](#adr-k6-automatic-timestamped-database-backups)

---

### ADR-K1: Arch Linux Packaging Hierarchy in /opt/nox
- **Context**: Relying on per-user virtual environment activation (`source .venv/bin/activate`) or `uv run` causes PATH friction, systemd service fragility, and inconsistent desktop launcher behavior.
- **Decision**: Package NoX into two clean Arch Linux packages (`nox` and `nox-app`) built via `makepkg -f`.
  - `nox`: Installs isolated runtime to `/opt/nox`, with binaries wrapped into `/usr/bin/nx` and `/usr/bin/noxd`, and systemd unit in `/usr/lib/systemd/user/noxd.service`.
  - `nox-app`: Installs native Tauri 2 desktop binary `/opt/nox/bin/nox-desktop`, wrapped as `/usr/bin/nox-app`, with desktop launcher `/usr/share/applications/nox.desktop`.
- **Consequences**: Standard `pacman -U` installation puts `nx`, `noxd`, and `nox-app` globally on PATH without root-level Python pollution.

---

### ADR-K2: Native Tauri 2 Desktop Client on Wayland
- **Context**: Running a web browser tab or raw Electron client consumes excessive RAM and requires cumbersome window management on Hyprland.
- **Decision**: Build a native desktop app in Rust using Tauri 2 and WebKit2GTK with Wayland backend (`xwayland: false`).
- **Consequences**: Native Wayland window mapping, ~166 MB idle RSS, responsive system tray integration with 10 menu actions, and clean window closing that persists to tray without interrupting daemon tasks.

---

### ADR-K3: Session Token Auth via Secure IPC (Zero URL Exposure)
- **Context**: Passing session authentication tokens via URL query parameters (`http://127.0.0.1:7777/?token=...`) exposes sensitive credentials in browser histories, logs, and process inspection.
- **Decision**: The desktop app reads `~/.local/share/nox/session_token` inside the Rust backend and provides it to the webview via Tauri IPC command `get_session_token`. The webview saves it directly to `sessionStorage['nox_token']` without appending tokens to URLs.
- **Consequences**: Eliminates credential exposure vectors while retaining zero-interaction authentication.

---

### ADR-K4: Single-Instance Enforcement & Deep Routing Protocol
- **Context**: Clicking desktop shortcuts or notifications should not spawn multiple desktop windows.
- **Decision**: Use `tauri-plugin-single-instance`. Secondary launches forward command-line arguments (e.g. `--open /briefings`) to the running primary instance and exit with code 0.
- **Consequences**: Primary window immediately focuses and navigates to the requested route; secondary processes terminate cleanly.

---

### ADR-K5: Idempotent Setup Command (`nx setup`)
- **Context**: Users on Arch Linux and Hyprland need a single command to configure desktop integration, autostart, and session persistence without manual configuration file editing.
- **Decision**: Implement `nx setup` supporting `--autostart` (XDG autostart), `--hyprland` (exec-once appending with `--apply`), `--linger` (loginctl persistence), and `--check`.
- **Consequences**: Full system configuration in one command; completely idempotent and safe to re-run.

---

### ADR-K6: Automatic Timestamped Database Backups
- **Context**: Schema migrations and routine operations must never risk data loss on the real SQLite database (`~/.local/share/nox/nox.db`).
- **Decision**: Enforce automatic timestamped backups before migrations and operations in `~/.local/share/nox/backups/nox_YYYYMMDD_HHMMSS_<hash>.db`, retaining the last 10 backups.
- **Consequences**: Complete safety net with instant rollback capability via `nx backup restore <file>`.

---

### ADR-L1: Plan-First Job Architecture & Step Isolation
- **Context**: Jobs previously executed freeform agent loops with LLMs invoked during execution. For scheduled maintenance (such as git backups), this caused unpredictability, long stalls waiting for local LLMs, unhandled tool approval requirements, and lack of step tracking.
- **Decision**: Separate planning from execution. When a job is created or updated, a plan is generated ahead of time (via cloud LLM or deterministic template), validated statically, and approved once by the user. Execution runs deterministic Python subprocesses step-by-step with zero LLM calls during execution. Each step is persisted in `job_run_steps`.
- **Consequences**: Deterministic, robust scheduled execution; progress is auditable per step; elimination of execution-time LLM delays and hangs.

---

### ADR-L2: Strict Privacy Sanitization with Local Placeholder Resolution
- **Context**: Rule 7 and user privacy dictate that cloud models must never receive actual filesystem paths, folder names, file contents, remote URLs, or git details.
- **Decision**: `noxai.jobs.privacy` detects filesystem paths and URLs in instructions and replaces them with generic numbered placeholders (`{{folder_1}}`, `{{remote_1}}`). Only the sanitized instruction is transmitted to the cloud API. The plan is returned with placeholders and re-hydrated locally before display, approval, and execution.
- **Consequences**: Zero leakage of personal folder structures, user account names, or repository URLs to third-party endpoints. Verified via unit and network capture tests.

---

### ADR-L3: Generalized Slot Catch-up Scheduling & Heartbeat Watchdog
- **Context**: Jobs scheduled at 20:00 Asia/Kolkata were missed if the machine was off. Furthermore, a run could remain "running" indefinitely if interrupted or hung.
- **Decision**: Implement a due-slot catch-up rule where `due_slot` is the most recent scheduled slot in the past. If unserved, it is executed on startup immediately after network availability. In addition, `job_runs` maintain a 5-second heartbeat. Runs with no heartbeat for 3 minutes are marked `stalled`. `nx jobs repair` reconciles orphaned runs at startup.
- **Consequences**: Guarantees daily execution even across system reboots or power downs. Completely prevents runs from remaining permanently in an unmonitored "running" state.

---

### ADR-L4: Cascading Soft Delete with Cooperative Cancellation and 10s Undo
- **Context**: Users need delete controls for jobs, tasks, and briefings with safety against accidental deletion.
- **Decision**: Soft delete via `deleted_at` timestamp. Running items are cooperatively cancelled first through TaskSupervisor (within 5s). Deleted items are hidden from default views and can be restored within 24h (`/restore` API or CLI, or 10s toast in UI). Deleting a briefing safely cleans up unmodified generated vault notes while preserving user-edited notes and opportunity entries.
- **Consequences**: Immediate, safe responsiveness without permanent accidental data loss.
