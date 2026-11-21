# Changelog

All notable changes to NoX will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased] - 2026-11-21 (Session N)

### Added
- **Full OpenJarvis Parity & Analysis Matrix (`docs/OPENJARVIS_PARITY.md`)**:
  - Comprehensive 48-capability matrix across 20 subsystems mapped to OpenJarvis (`77bff254`).
- **Security & Privacy Foundation**:
  - Indian PII Redactor: zero-leak redactor for Aadhaar, PAN, IFSC, UPI, debit/credit cards, OTPs, phone, and email with local in-memory placeholder restoration.
  - Guardrails Input/Output Scanner: prompt injection, invisible Unicode, synthetic canary token blocking.
  - SHA-256 Tamper-Evident Audit Log: cryptographic hash-chained events with `nx audit verify` and UI verification badge.
  - Secret Store: 0600-permission file and system keyring abstraction (`SecretStore`).
  - Approvals Center: scoped single-use approval tokens for guarded write actions with TTL and interactive preview.
  - Emergency Kill Switch: `nx panic` and UI panic switch to instantaneously stop continuous agents, disconnect external channels, and revoke pending approvals.
- **Connector Framework & Mail Intelligence**:
  - BaseConnector with typed health, retry exponential backoff, rate limiting, and non-blocking daemon startup.
  - Gmail Connector: OAuth 2.0 PKCE flow with read-only default and secure token storage.
  - IMAP Connector: SSL IMAP sync for generic email providers with local metadata storage.
  - Calendar Connector: Google Calendar REST API and local `.ics` iCalendar import.
  - Mail Profile (`~/.config/nox/mail_profile.toml`): user-defined sender classification, VIP filtering, phishing danger detection, and draft reply generation.
  - Feeds: RSS/Atom, Bluesky, Mastodon, Reddit feeds (`nx connect feeds`).
- **Channels & Social Intelligence**:
  - Telegram Bot: owner pairing via one-time 6-digit challenge code, sender allowlist, and private output redaction.
  - Slack & Discord ingestion into unified social digest with untrusted data sanitization.
- **GitHub & Actions Intelligence**:
  - Dual-Engine GitHubConnector: native `gh` CLI subprocess execution with fallback to GitHub REST API via fine-grained token.
  - GitHub Actions failure triage: failure step extraction, plain-language root cause explanation, and flaky test detection.
  - Static Workflow Linter: checks for unpinned Actions (`@master`/`@main`), shell script injection vulnerabilities, and secret leaks.
  - Automated CI watcher: `nx gh watch` polling and triaging live workflow runs.
  - Built-in skills: `github-actions-triage` and `pr-review-summary` in agentskills.io format.
- **Multi-Mode Agents Framework & Presets**:
  - Supervised agent runner supporting on-demand, scheduled, and continuous modes.
  - 8 built-in agents: `deep_research`, `morning_digest`, `inbox_triage`, `github_ci_watch`, `scheduled_monitor`, `code_assistant`, `notes_assistant`, `chat`.
  - 7 Presets: `nx preset list` and `nx preset apply <name>`.
  - Untrusted Skill Import: `nx skill install <url>` with Python AST security scanner (blocks unsafe imports, OS command execution, and network exfiltration).
  - Learning Loop & Telemetry: prompt optimizer (`nx optimize prompts`) with automated regression detection and rollback, token/cost tracking.
- **Night-Sky UI & Unified CLI Diagnostics**:
  - 7 new UI Pages: `ConnectorsPage`, `MailPage`, `GitHubPage`, `AgentsPage`, `ApprovalsPage`, `AuditPage`, `SkillsPage`.
  - Universal Soft-Delete Protection: delete controls across all data pages with `UndoToast` and `DeleteConfirmModal`.
  - Unified diagnostics: `nx health` (daemon, DB, KeyPool, audit, tools) and `nx selftest` (subsystem integrity test table).
- **Repo Hygiene & CI**:
  - `.github/workflows/ci.yml` (multi-language Python 3.13 + Node 20 matrix, ruff, pytest, vitest, UI build).
  - `.github/workflows/codeql.yml` for static security analysis.
  - `.github/dependabot.yml` and `SECURITY.md`.

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
