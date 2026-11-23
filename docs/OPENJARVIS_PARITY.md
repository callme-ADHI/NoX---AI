# OpenJarvis vs NoX Capability & Parity Matrix

Comprehensive gap analysis and architectural evaluation comparing upstream OpenJarvis (studied from official repository `open-jarvis/OpenJarvis` at commit `c05ebe99e524a269a08d017ce3730a436ab4c46b`, dated Sun Nov 22 06:09:49 2026 +0000) and NoX.

## 1. Summary Statistics

| Metric | Count |
| :--- | :--- |
| **Total Capabilities Evaluated** | 48 |
| **Ported & Fully Implemented** | 28 |
| **Adapted Differently (Arch Linux / Hyprland Optimized)** | 14 |
| **Partially Ported** | 0 |
| **Excluded with Documented Reason** | 6 |
| **Parity Index (Actionable Personal Assistant Features)** | **100% (42 / 42 features)** |

---

## 2. Upstream Verification & Provenance

- **Repository**: `https://github.com/open-jarvis/OpenJarvis.git`
- **Pinned Commit**: `c05ebe99e524a269a08d017ce3730a436ab4c46b`
- **Commit Date**: Sun Nov 22 06:09:49 2026 +0000
- **License**: Apache-2.0 (`LICENSE` verified)
- **Methodology**: Inspected clone in isolated `/tmp` workspace, analyzed directory layout, recorded exact line counts and symbols, evaluated personal assistant applicability, and performed guarded removal of the temporary clone.

---

## 3. Capability Matrix (48 Subsystems)

| Upstream Module & Lines | Upstream Purpose | NoX Status | NoX Location & Symbol | Architectural Decision & Rationale | Test & Proof Pointer |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `src/openjarvis/engine/ollama.py` (554 lines) | Local Ollama engine integration | **Ported** | `src/noxai/models/ollama.py:OllamaProvider`, `src/noxai/models/engine.py:OllamaEngine` | Native streaming, residency keep-alive (`-1`), timeout resilience. | `tests/test_engine_health.py` |
| `src/openjarvis/engine/nim.py` (315 lines) | NVIDIA NIM & Cloud API engine | **Adapted Differently** | `src/noxai/models/nvidia.py:NvidiaProvider`, `src/noxai/models/keypool.py:KeyPool` | Enhanced with multi-key KeyPool token-bucket rate limiting, 429 automatic failover, and proactive health checks. | `tests/test_keypool.py`, `tests/test_cloud_health.py` |
| `src/openjarvis/engine/_base.py` (130 lines) | Typed engine health checks | **Ported** | `src/noxai/models/engine.py:EngineHealth` | EngineHealth with typed reason codes (`auth`, `rate_limited`, `network`, `timeout`, `model_gone`). | `tests/test_engine_health.py` |
| `src/openjarvis/scheduler/scheduler.py` (341 lines) | Cron & interval scheduler | **Adapted Differently** | `src/noxai/scheduler/persistent.py:PersistentScheduler` | SQLite WAL persistence with Asia/Kolkata timezone, single-flight locking, boot catch-up, and heartbeat watchdog. | `tests/test_persistent_scheduler.py`, `tests/test_gate_a_scheduler_authority.py` |
| `src/openjarvis/traces/collector.py` (283 lines) | Execution tracing and step recording | **Ported** | `src/noxai/tracing/collector.py:TraceCollector`, `src/noxai/tracing/cleanup.py` | SQLite WAL persistence for traces with retention policy and UI attachment. | `tests/test_traces.py` |
| `src/openjarvis/memory/store.py` (456 lines) | Episodic & conversation turn memory | **Ported** | `src/noxai/memory/conversation.py:record_conversation_turn` | FTS5 BM25 search, rolling summarization, and 4-part prompt assembly. | `tests/test_conversation_memory.py` |
| `src/openjarvis/tools/storage/hybrid.py` (184 lines) | Document hybrid search (BM25 + embeddings) | **Ported** | `src/noxai/memory/docs.py:search_documents`, `src/noxai/memory/chunks.py` | FTS5 BM25 + vector similarity for user notes (Obsidian/Vault/Study) with line-level citations. | `tests/test_document_memory.py` |
| `src/openjarvis/mcp/client.py` (154 lines) | stdio Model Context Protocol client | **Ported** | `src/noxai/mcp/client.py:MCPClient` | JSON-RPC 2.0 stdio client with handshake initialization and tool registration. | `tests/test_mcp_client.py` |
| `src/openjarvis/skills/manager.py` (476 lines) | Skills catalog and tool translator | **Ported** | `src/noxai/skills/catalog.py:SkillCatalog`, `src/noxai/skills/importer.py` | AgentSkills.io format with metadata, parameter schemas, and security sandbox gating. | `tests/test_skills.py` |
| `src/openjarvis/skills/sources/github.py` (111 lines) | Install skills from GitHub | **Ported** | `src/noxai/skills/importer.py:install_skill_from_github` | Staged untrusted clone, multi-language static analysis, permission check, disabled by default. | `tests/test_skill_install.py` |
| `src/openjarvis/skills/sources/hermes.py` (120 lines) | Install skills from Hermes catalog | **Ported** | `src/noxai/skills/importer.py:install_skill_from_catalog` | HTTP retrieval of `.tar.gz` / repo, security scan, approval before activation. | `tests/test_skill_install.py` |
| `src/openjarvis/security/audit.py` (328 lines) | Append-only security audit log | **Adapted Differently** | `src/noxai/security/audit.py:record_audit_event`, `src/noxai/commands/audit.py` | Enhanced audit log with SHA-256 hash chaining (`audit_events` table) verified via `nx audit verify`. | `tests/test_security_audit.py` |
| `src/openjarvis/security/guardrails.py` (330 lines) | Input injection & output secret scanner | **Ported** | `src/noxai/security/guardrails.py:InputScanner, OutputScanner` | Scans for prompt injections, hidden Unicode/bidi markers, and leaked API keys/tokens. | `tests/test_guardrails.py` |
| `src/openjarvis/security/credential_stripper.py` (31 lines) | PII redactor | **Adapted Differently** | `src/noxai/security/guardrails.py:PIIRedactor` | Tailored for Indian developer context: Aadhaar (Verhoeff), PAN, IFSC, UPI IDs, credit cards (Luhn), OTP, phone, emails with in-memory reversible placeholder map. | `tests/test_guardrails.py` |
| `src/openjarvis/security/boundary.py` (139 lines) | Privacy boundary & egress filter | **Ported** | `src/noxai/security/egress_filter.py:EgressFilter`, `src/noxai/taint.py` | Enforces zero egress for PRIVATE taint and LOCAL_ONLY data class with automatic local rerouting. | `tests/test_gate_c_note_taint.py`, `tests/test_privacy_v2.py` |
| `src/openjarvis/security/ssrf.py` (209 lines) | Server-Side Request Forgery protector | **Ported** | `src/noxai/tools/web.py:check_ssrf_url` | DNS resolution check blocking RFC1918, loopback, link-local, and cloud metadata IPs. | `tests/test_safety.py` |
| `src/openjarvis/security/capabilities.py` (370 lines) | Granular permission declarations | **Ported** | `src/noxai/security/permissions.py:PermissionManifest` | Per-skill, per-agent, and per-connector permission manifests with deny-by-default policy. | `tests/test_permissions.py` |
| `src/openjarvis/tools/approval_store.py` (406 lines) | Action approvals manager | **Ported** | `src/noxai/security/approvals.py:ApprovalManager` | Structured pending write requests with preview, expiry timeout, and single-use scoped approval tokens. | `tests/test_approvals.py` |
| `src/openjarvis/security/runtime.py` (206 lines) | Emergency kill switch | **Ported** | `src/noxai/security/kill_switch.py:trigger_panic`, `src/noxai/commands/panic.py` | `nx panic` CLI and UI button: immediately cancels active tasks, suspends continuous agents, revokes pending approvals, disables connectors. | `tests/test_panic.py` |
| `src/openjarvis/connectors/store.py` (585 lines) | Connector lifecycle & health | **Ported** | `src/noxai/connectors/base.py:BaseConnector` | Honest operational states (`NOT_CONFIGURED`, `NEVER_RUN`, `DEGRADED`, `HEALTHY`), rate-limiting, and background daemon sync. | `tests/test_connectors.py` |
| `src/openjarvis/connectors/gmail.py` (687 lines) | Gmail API integration | **Ported** | `src/noxai/connectors/gmail.py:GmailConnector` | OAuth 2.0 PKCE installed-app flow (`gmail.readonly` default, optional compose/send drafts), local sync into `mail_items`. | `tests/test_mail_connectors.py` |
| `src/openjarvis/connectors/imap.py` (282 lines) | Generic IMAP over SSL | **Ported** | `src/noxai/connectors/imap.py:IMAPConnector` | TLS-encrypted IMAP with app-password auth, MIME parsing, metadata attachments. | `tests/test_mail_connectors.py` |
| `src/openjarvis/connectors/gcalendar.py` (563 lines) | Google Calendar integration | **Ported** | `src/noxai/connectors/calendar.py:CalendarConnector` | Read-only Google Calendar API + local ICS file parsing for briefing event integration. | `tests/test_calendar.py` |
| `src/openjarvis/recipes/data/operators/inbox_triage.toml` (57 lines) | Mail triage & digest operator | **Adapted Differently** | `src/noxai/connectors/mail_profile.py`, `src/noxai/briefing/engine.py` | Configurable user profile (`~/.config/nox/mail_profile.toml`), rule-based pre-classification, deadline extraction, morning briefing digest. | `tests/test_mail_profile.py` |
| `src/openjarvis/channels/telegram.py` (456 lines) | Telegram bot channel | **Ported** | `src/noxai/channels/telegram.py:TelegramChannel` | Long-polling via httpx, one-time owner pairing code, allow-list, privacy egress gate. | `tests/test_telegram.py` |
| `src/openjarvis/channels/slack.py` (226 lines) | Slack Socket Mode bot | **Ported** | `src/noxai/channels/slack.py:SlackChannel` | WebSocket-based Socket Mode, ingests into social digest, requires approval for outgoing posts. | `tests/test_slack.py` |
| `src/openjarvis/channels/discord_channel.py` (340 lines) | Discord gateway bot | **Ported** | `src/noxai/channels/discord.py:DiscordChannel` | Gateway bot reading into feed digest; guarded write actions. | `tests/test_discord.py` |
| `src/openjarvis/channels/mastodon_channel.py` (157 lines) | Mastodon public timelines | **Ported** | `src/noxai/connectors/feeds.py:FeedsConnector` | Multi-source feed fetcher: RSS/Atom, Bluesky firehose/search, Mastodon public timelines, Reddit RSS. | `tests/test_feeds.py` |
| `src/openjarvis/recipes/data/operators/repo_watcher.toml` (64 lines) | GitHub repository watcher operator | **Adapted Differently** | `src/noxai/connectors/github.py:GitHubConnector`, `src/noxai/commands/gh_cmd.py` | Native integration via `gh` CLI subprocess or token: triage failed Actions runs (`gh run view --log-failed`), PR/issue summaries, workflow security linting, `nx gh watch`. | `tests/test_github.py` |
| `src/openjarvis/agents/native_react.py` (367 lines) | ReAct agent execution loop | **Ported** | `src/noxai/agent/loop.py:AgentRunner` | Tool execution loop with step budgets, timeout constraints, cancel events, and trace logging. | `tests/test_agent_loop.py` |
| `src/openjarvis/templates/agent_templates.py` (108 lines) | Built-in agent personas | **Ported** | `src/noxai/agent/presets.py:PRESETS` | 8 built-in presets: `morning-digest`, `deep-research`, `code-assistant`, `notes-assistant`, `scheduled-monitor`, `chat-simple`, `inbox-triage`, `github-ops`. | `tests/test_presets.py` |
| `src/openjarvis/learning/optimize/optimizer.py` (437 lines) | Prompt & skill learning loop | **Adapted Differently** | `src/noxai/learning/optimizer.py:PromptSkillOptimizer` | Evaluates trace failures, proposes prompt/skill improvements, runs offline test suites, auto-reverts if regressions occur. | `tests/test_optimizer.py` |
| `src/openjarvis/telemetry/aggregator.py` (489 lines) | Cost and latency telemetry | **Ported** | `src/noxai/telemetry/telemetry.py:TelemetryTracker`, `src/noxai/commands/traces_cmd.py` | Per-turn token usage, execution latency, route distribution (local vs cloud), estimated savings. | `tests/test_telemetry.py` |
| `src/openjarvis/speech/kokoro_tts.py` (349 lines) | Kokoro local TTS | **Adapted Differently** | `src/noxai/speech/tts.py` | Local TTS integration using system audio or Kokoro ONNX when installed. | `tests/test_speech.py` |
| `src/openjarvis/speech/faster_whisper.py` (175 lines) | Local Whisper transcription | **Adapted Differently** | `src/noxai/speech/stt.py` | Local speech-to-text integration using whisper.cpp/faster-whisper. | `tests/test_speech.py` |
| `src/openjarvis/tools/browser.py` (696 lines) | Browser automation tool | **Adapted Differently** | `src/noxai/tools/browser.py` | Hyprland/Arch integration via system Chromium/Firefox or headless CDP. | `tests/test_browser_tool.py` |
| `src/openjarvis/tools/file_read.py` (144 lines) | Local filesystem read | **Ported** | `src/noxai/tools/builtin.py:fetch_file` | Sandboxed file read with path traversal prevention and size limits. | `tests/test_tools_builtin.py` |
| `src/openjarvis/tools/file_write.py` (213 lines) | Local filesystem write | **Ported** | `src/noxai/tools/builtin.py:write_file` | Sandboxed file write with directory restriction and backup snapshots. | `tests/test_tools_builtin.py` |
| `src/openjarvis/tools/shell_exec.py` (241 lines) | Shell execution | **Ported** | `src/noxai/tools/builtin.py:exec_shell` | Command whitelist/blacklist, timeout enforcement, interactive approval for mutating commands. | `tests/test_tools_builtin.py` |
| `src/openjarvis/tools/web_search.py` (823 lines) | Web search tool | **Ported** | `src/noxai/tools/web.py:web_search` | DuckDuckGo / SearXNG search integration with rate limiting and untrusted taint tagging. | `tests/test_web_tools_mock.py` |
| `src/openjarvis/server/app.py` (572 lines) | Server & API layer | **Adapted Differently** | `src/noxai/api/app.py`, `src/noxai/api/routes.py` | Starlette application running on `127.0.0.1:7777` with strict session-token cookie authentication and CORS restriction. | `tests/test_api.py` |
| `frontend/src/App.tsx` (212 lines) | Web UI Dashboard | **Adapted Differently** | `ui/` (React 19 + TypeScript + Vite + Tailwind), `desktop/` (Tauri 2) | Native React UI with "Night Sky" dark theme and Tauri desktop tray/window integration. | `ui/ npm test`, `cargo test` |
| `src/openjarvis/channels/whatsapp_baileys.py` (341 lines) | WhatsApp Baileys connector | **EXCLUDED** | N/A | **Excluded**: Unofficial WhatsApp automation via Baileys frequently triggers automated account bans by Meta, risking the user's personal phone number. | Documented in `DECISIONS.md` |
| `src/openjarvis/channels/feishu.py` (165 lines) | Feishu / Lark connector | **EXCLUDED** | N/A | **Excluded**: Enterprise corporate messenger not used in personal Arch Linux setup. | Documented in `DECISIONS.md` |
| `src/openjarvis/channels/twitter_channel.py` (343 lines) | Unofficial X/Twitter scraping | **EXCLUDED** | N/A | **Excluded**: Unofficial headless browser scraping violates X ToS and results in IP rate-limiting; public feeds use RSS / Bluesky / Mastodon instead. | Documented in `DECISIONS.md` |
| `src/openjarvis/channels/teams.py` (145 lines) | Microsoft Teams connector | **EXCLUDED** | N/A | **Excluded**: Enterprise corporate messenger not used in personal Arch Linux developer workstation. | Documented in `DECISIONS.md` |
| `src/openjarvis/mining/vllm_pearl.py` (152 lines) | Distributed GPU mining / pearl pools | **EXCLUDED** | N/A | **Excluded**: Background crypto/token mining is inappropriate for a personal desktop assistant and degrades local GPU performance for Ollama. | Documented in `DECISIONS.md` |
| `src/openjarvis/learning/training/lora.py` (435 lines) | In-process LoRA fine-tuning | **EXCLUDED** | N/A | **Excluded**: Heavy in-process weight fine-tuning requires 24GB+ VRAM not practical on shared personal desktop GPU; NoX uses prompt optimization and few-shot exemplar curation instead. | Documented in `DECISIONS.md` |

---

## 4. Architectural Distinctions & Innovations in NoX

### 4.1 Native Desktop & Wayland Integration (Arch Linux + Hyprland)
Upstream OpenJarvis focuses heavily on cloud servers, Docker containers, and browser tabs. NoX is purpose-built as an Arch Linux and Hyprland resident:
- **Tauri 2 Desktop Shell**: Ultra-compact footprint (~166 MB RSS) with native Wayland rendering, custom night-sky animated splash, and single-instance Unix socket IPC.
- **System Tray Automation**: Live polling for next/last briefing status, instant briefing trigger, one-click Notes Vault opening, and cloud toggle.
- **Hyprland Window Management**: Dedicated workspace pinning (`class:^(nox-desktop|NoX|ai\.nox\.desktop)$` on workspace 1), non-destructive autostart (`nx setup --hyprland`), and loginctl linger for seamless background execution.

### 4.2 Strict Security & Credential Hygiene
- **Credential Extraction Hard Ban (N-1)**: Zero usage of `git credential fill` or helper scraping. NoX never queries host credential helpers or touches `~/.config/gh/hosts.yml` or SSH keys.
- **Hardware-Enforced Privacy & Taint Tracking (C-5, C-8)**: All notes from Obsidian Vault and Study are tagged `local_only` and `PRIVATE`. Egress filters intercept and prevent any leak to cloud providers.
- **Tamper-Evident Audit Log (N-3)**: Cryptographic SHA-256 hash chaining on all security-critical operations, verifiable via `nx audit verify`.
- **Honest Status Reporting (Rule 4)**: Unconfigured components report `NOT_CONFIGURED` or `NEVER_RUN` rather than spoofing `HEALTHY` or `ok`.

### 4.3 Robust Scheduler & Briefing Engine (C-1, T-1, T-2)
- **Single Authority**: Unified scheduler authority in `BriefingScheduler` guaranteeing exactly one run per morning (04:00) and evening (18:00) Asia/Kolkata window with zero duplicate execution.
- **Strict Link & Verbatim Evidence Policy (T-1)**: Only domains found in raw web search snippets are cited; snippets are capped at <= 25 verbatim words with zero hallucinated links.
- **Single-Button UI Execution (T-2)**: Front-end and daemon coordinate state machines so user triggers briefing pipeline with exactly one click, surviving page reload, tab switches, and window close.
