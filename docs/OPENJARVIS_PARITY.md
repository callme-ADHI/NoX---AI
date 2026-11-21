# OpenJarvis vs NoX Capability & Parity Matrix

Comprehensive gap analysis and architectural evaluation comparing upstream OpenJarvis (studied at commit `77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b`, with updates through `a9e59e8`) and NoX.

## 1. Summary Statistics

| Metric | Count |
| :--- | :--- |
| **Total Capabilities Evaluated** | 48 |
| **Ported & Fully Implemented** | 28 |
| **Adapted Differently (Arch/Hyprland Optimized)** | 14 |
| **Partially Ported** | 0 |
| **Excluded with Documented Reason** | 6 |
| **Parity Index (Useful Features)** | **100% (42 / 42 actionable personal assistant features)** |

---

## 2. Capability Matrix

| Upstream Module & Symbol | Upstream Purpose | NoX Status | NoX Location & Symbol | Architectural Decision & Rationale | Test & Proof Pointer |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `src/openjarvis/engine/ollama.py` | Local Ollama engine integration | **Ported** | `src/noxai/models/ollama.py:OllamaProvider`, `src/noxai/models/engine.py:OllamaEngine` | Native streaming, timeout handling, and residency model keep-alive (`-1`). | `tests/test_engine_health.py` |
| `src/openjarvis/engine/nim.py` | NVIDIA NIM & Cloud API engine | **Adapted Differently** | `src/noxai/models/nvidia.py:NvidiaProvider`, `src/noxai/models/keypool.py:KeyPool` | NoX enhances NIM with multi-key KeyPool token-bucket rate limiting, automatic 429 failover, and proactive health checks. | `tests/test_keypool.py`, `tests/test_cloud_health.py` |
| `src/openjarvis/engine/health.py` | Typed engine health checks | **Ported** | `src/noxai/models/engine.py:EngineHealth` | EngineHealth with typed reason codes (`auth`, `rate_limited`, `network`, `timeout`, `model_gone`). | `tests/test_engine_health.py` |
| `src/openjarvis/scheduler/scheduler.py` | Cron & interval scheduler | **Adapted Differently** | `src/noxai/scheduler/persistent.py:PersistentScheduler` | Upstream uses generic cron in SQLite; NoX adapts to persistent async SQLite WAL with Asia/Kolkata timezone, single-flight locking, boot catch-up, and heartbeat watchdog. | `tests/test_persistent_scheduler.py`, `tests/test_gate_a_scheduler_authority.py` |
| `src/openjarvis/traces/collector.py` | Execution tracing and step recording | **Ported** | `src/noxai/tracing/collector.py:TraceCollector`, `src/noxai/tracing/cleanup.py` | SQLite WAL persistence for traces with retention cleanup and UI viewing. | `tests/test_traces.py` |
| `src/openjarvis/memory/store.py` | Episodic & conversation turn memory | **Ported** | `src/noxai/memory/conversation.py:record_conversation_turn` | FTS5 BM25 search, rolling summarization, and 4-part prompt assembly. | `tests/test_conversation_memory.py` |
| `src/openjarvis/tools/storage/hybrid.py` | Document hybrid search (BM25 + embeddings) | **Ported** | `src/noxai/memory/docs.py:search_documents`, `src/noxai/memory/chunks.py` | FTS5 BM25 + vector similarity for user notes (Obsidian/Vault/Study) with line-level citations. | `tests/test_document_memory.py` |
| `src/openjarvis/mcp/client.py` | stdio Model Context Protocol client | **Ported** | `src/noxai/mcp/client.py:MCPClient` | JSON-RPC 2.0 stdio client with handshake initialization and tool registration into ToolRegistry. | `tests/test_mcp_client.py` |
| `src/openjarvis/skills/manager.py` | Skills catalog and tool translator | **Ported** | `src/noxai/skills/catalog.py:SkillCatalog`, `src/noxai/skills/importer.py` | AgentSkills.io format with metadata, parameter schemas, and security sandbox gating. | `tests/test_skills.py` |
| `src/openjarvis/skills/sources/github.py` | Install skills from GitHub | **Ported** | `src/noxai/skills/importer.py:install_skill_from_github` | Staged untrusted clone, AST static analysis, permission check, disabled by default. | `tests/test_skill_install.py` |
| `src/openjarvis/skills/sources/hermes.py` | Install skills from Hermes catalog | **Ported** | `src/noxai/skills/importer.py:install_skill_from_catalog` | HTTP retrieval of `.tar.gz` / repo, security scan, approval before activation. | `tests/test_skill_install.py` |
| `src/openjarvis/security/audit.py` | Append-only security audit log | **Adapted Differently** | `src/noxai/security/audit.py:record_audit_event`, `src/noxai/commands/audit.py` | NoX enhances audit log with SHA-256 hash chaining (`audit_events` table) verified via `nx audit verify`. | `tests/test_security_audit.py` |
| `src/openjarvis/security/guardrails.py` | Input injection & output secret scanner | **Ported** | `src/noxai/security/guardrails.py:InputScanner, OutputScanner` | Scans for prompt injections, hidden Unicode/bidi markers, and leaked API keys/tokens. | `tests/test_guardrails.py` |
| `src/openjarvis/security/credential_stripper.py` | PII redactor | **Adapted Differently** | `src/noxai/security/guardrails.py:PIIRedactor` | Tailored for Indian developer context: Aadhaar (Verhoeff), PAN, IFSC, UPI IDs, credit cards (Luhn), OTP, phone, emails with in-memory reversible placeholder map. | `tests/test_guardrails.py` |
| `src/openjarvis/security/boundary.py` | Privacy boundary & egress filter | **Ported** | `src/noxai/security/egress_filter.py:EgressFilter`, `src/noxai/taint.py` | Enforces zero egress for PRIVATE taint and LOCAL_ONLY data class with automatic local rerouting. | `tests/test_gate_c_note_taint.py`, `tests/test_privacy_v2.py` |
| `src/openjarvis/security/ssrf.py` | Server-Side Request Forgery protector | **Ported** | `src/noxai/tools/web.py:check_ssrf_url` | DNS resolution check blocking RFC1918, loopback, link-local, and cloud metadata IPs. | `tests/test_safety.py` |
| `src/openjarvis/security/capabilities.py` | Granular permission declarations | **Ported** | `src/noxai/security/permissions.py:PermissionManifest` | Per-skill, per-agent, and per-connector permission manifests with deny-by-default policy. | `tests/test_permissions.py` |
| `src/openjarvis/tools/approval_store.py` | Action approvals manager | **Ported** | `src/noxai/security/approvals.py:ApprovalManager` | Structured pending write requests with preview, expiry timeout, and single-use scoped approval. | `tests/test_approvals.py` |
| `src/openjarvis/security/runtime.py` (Kill switch) | Emergency kill switch | **Ported** | `src/noxai/security/kill_switch.py:trigger_panic`, `src/noxai/commands/panic.py` | `nx panic` CLI and UI button: immediately cancels active tasks, suspends continuous agents, revokes pending approvals, disables connectors. | `tests/test_panic.py` |
| `src/openjarvis/connectors/base.py` | Connector lifecycle & health | **Ported** | `src/noxai/connectors/base.py:BaseConnector` | Typed health, rate-limiting, retry with exponential backoff, and non-blocking background daemon sync. | `tests/test_connectors.py` |
| `src/openjarvis/connectors/gmail.py` | Gmail API integration | **Ported** | `src/noxai/connectors/gmail.py:GmailConnector` | OAuth 2.0 PKCE installed-app flow (`gmail.readonly` default, optional compose/send drafts), local sync into `mail_items`. | `tests/test_mail_connectors.py` |
| `src/openjarvis/connectors/imap.py` | Generic IMAP over SSL | **Ported** | `src/noxai/connectors/imap.py:IMAPConnector` | TLS-encrypted IMAP with app-password auth, MIME parsing, metadata attachments. | `tests/test_mail_connectors.py` |
| `src/openjarvis/connectors/calendar.py` | Google Calendar & ICS | **Ported** | `src/noxai/connectors/calendar.py:CalendarConnector` | Read-only Google Calendar API + local ICS file parsing for briefing event integration. | `tests/test_calendar.py` |
| `src/openjarvis/recipes/data/operators/inbox_triage.toml` | Mail triage & digest | **Adapted Differently** | `src/noxai/connectors/mail_profile.py`, `src/noxai/briefing/engine.py` | Configurable user profile (`~/.config/nox/mail_profile.toml`), rule-based pre-classification, deadline extraction, morning briefing digest. | `tests/test_mail_profile.py` |
| `src/openjarvis/channels/telegram.py` | Telegram bot channel | **Ported** | `src/noxai/channels/telegram.py:TelegramChannel` | Long-polling via httpx, one-time owner pairing code, allow-list, privacy egress gate. | `tests/test_telegram.py` |
| `src/openjarvis/channels/slack.py` | Slack Socket Mode bot | **Ported** | `src/noxai/channels/slack.py:SlackChannel` | WebSocket-based Socket Mode, ingests into social digest, requires approval for outgoing posts. | `tests/test_slack.py` |
| `src/openjarvis/channels/discord.py` | Discord gateway bot | **Ported** | `src/noxai/channels/discord.py:DiscordChannel` | Gateway bot reading into feed digest; guarded write actions. | `tests/test_discord.py` |
| `src/openjarvis/connectors/feeds/` | RSS/Atom & Public Social Feeds | **Ported** | `src/noxai/connectors/feeds.py:FeedsConnector` | Multi-source feed fetcher: RSS/Atom, Bluesky firehose/search, Mastodon public timelines, Reddit RSS. | `tests/test_feeds.py` |
| `src/openjarvis/recipes/data/operators/repo_watcher.toml` | GitHub repository watcher | **Adapted Differently** | `src/noxai/connectors/github.py:GitHubConnector`, `src/noxai/commands/gh_cmd.py` | Native integration via `gh` CLI subprocess or token: triage failed Actions runs (`gh run view --log-failed`), PR/issue summaries, workflow security linting, `nx gh watch`. | `tests/test_github.py` |
| `src/openjarvis/agent/loop.py` | ReAct agent execution loop | **Ported** | `src/noxai/agent/loop.py:AgentRunner` | Tool execution loop with step budgets, timeout constraints, cancel events, and trace logging. | `tests/test_agent_loop.py` |
| `src/openjarvis/templates/agent_templates.py` | Built-in agent personas | **Ported** | `src/noxai/agent/presets.py:PRESETS` | 8 built-in presets: `morning-digest`, `deep-research`, `code-assistant`, `notes-assistant`, `scheduled-monitor`, `chat-simple`, `inbox-triage`, `github-ops`. | `tests/test_presets.py` |
| `src/openjarvis/learning/optimize/` | Prompt & skill learning loop | **Adapted Differently** | `src/noxai/learning/optimizer.py:PromptSkillOptimizer` | Evaluates trace failures, proposes prompt/skill improvements, runs offline test suites, auto-reverts if regressions occur. | `tests/test_optimizer.py` |
| `src/openjarvis/telemetry/aggregator.py` | Cost and latency telemetry | **Ported** | `src/noxai/telemetry/telemetry.py:TelemetryTracker`, `src/noxai/commands/traces_cmd.py` | Per-turn token usage, execution latency, route distribution (local vs cloud), estimated savings. | `tests/test_telemetry.py` |
| `src/openjarvis/speech/kokoro_tts.py` | Kokoro local TTS | **Adapted Differently** | `src/noxai/speech/tts.py` | Local TTS integration using system audio or Kokoro ONNX when installed. | `tests/test_speech.py` |
| `src/openjarvis/speech/faster_whisper.py` | Local Whisper transcription | **Adapted Differently** | `src/noxai/speech/stt.py` | Local speech-to-text integration using whisper.cpp/faster-whisper. | `tests/test_speech.py` |
| `src/openjarvis/tools/browser.py` | Browser automation tool | **Adapted Differently** | `src/noxai/tools/browser.py` | Hyprland/Arch integration via system Chromium/Firefox or headless CDP. | `tests/test_browser_tool.py` |
| `src/openjarvis/tools/file_read.py` | Local filesystem read | **Ported** | `src/noxai/tools/builtin.py:fetch_file` | Sandboxed file read with path traversal prevention and size limits. | `tests/test_tools_builtin.py` |
| `src/openjarvis/tools/file_write.py` | Local filesystem write | **Ported** | `src/noxai/tools/builtin.py:write_file` | Sandboxed file write with directory restriction and backup snapshots. | `tests/test_tools_builtin.py` |
| `src/openjarvis/tools/shell_exec.py` | Shell execution | **Ported** | `src/noxai/tools/builtin.py:exec_shell` | Command whitelist/blacklist, timeout enforcement, interactive approval for mutating commands. | `tests/test_tools_builtin.py` |
| `src/openjarvis/tools/web_search.py` | Web search tool | **Ported** | `src/noxai/tools/web.py:web_search` | DuckDuckGo / SearXNG search integration with rate limiting and untrusted taint tagging. | `tests/test_web_tools_mock.py` |
| `src/openjarvis/server/app.py` | Server & API layer | **Adapted Differently** | `src/noxai/api/app.py`, `src/noxai/api/routes.py` | Starlette application running on `127.0.0.1:7777` with strict session-token cookie authentication and CORS restriction. | `tests/test_api.py` |
| `src/openjarvis/server/dashboard.py` | Web UI Dashboard | **Adapted Differently** | `ui/` (React 19 + TypeScript + Vite + Tailwind), `desktop/` (Tauri 2) | Native React UI with "Night Sky" dark theme and Tauri desktop tray/window integration. | `ui/ npm test`, `cargo test` |
| `src/openjarvis/channels/whatsapp.py` | WhatsApp Baileys connector | **EXCLUDED** | N/A | **Excluded**: Unofficial WhatsApp automation via Baileys frequently triggers automated account bans by Meta, risking the user's personal phone number. | Documented in `DECISIONS.md` |
| `src/openjarvis/channels/wechat.py` | WeChat bot connector | **EXCLUDED** | N/A | **Excluded**: Requires WeChat official account or hooks that risk account suspension; not applicable to personal Arch Linux workstation. | Documented in `DECISIONS.md` |
| `src/openjarvis/channels/feishu.py` | Feishu / Lark connector | **EXCLUDED** | N/A | **Excluded**: Enterprise corporate messenger not used in personal setup. | Documented in `DECISIONS.md` |
| `src/openjarvis/channels/twitter.py` (Puppeteer) | Unofficial X/Twitter scraping | **EXCLUDED** | N/A | **Excluded**: Unofficial headless browser scraping violates X ToS and results in IP rate-limiting; public feeds use RSS / Bluesky / Mastodon instead. | Documented in `DECISIONS.md` |
| `src/openjarvis/mining/` | Distributed GPU token mining / pearl pools | **EXCLUDED** | N/A | **Excluded**: Background crypto/token mining is inappropriate for a personal desktop assistant and degrades local GPU performance for Ollama. | Documented in `DECISIONS.md` |
| `src/openjarvis/learning/training/lora.py` | In-process LoRA fine-tuning | **EXCLUDED** | N/A | **Excluded**: Heavy in-process weight fine-tuning requires 24GB+ VRAM not practical on shared personal desktop GPU; NoX uses prompt optimization and few-shot exemplar curation instead. | Documented in `DECISIONS.md` |

---

## 3. Detailed Architectural Decisions

### 3.1 Daemon Framework & Auth
- **Upstream OpenJarvis**: Uses FastAPI with Uvicorn and optional bearer tokens, exposing endpoints broadly.
- **NoX**: Uses Starlette with strict local loopback binding (`127.0.0.1:7777`), rotating `session_token` stored in `~/.local/share/nox/session_token` (0600 permissions), HTTP-only SameSite=Strict session cookies, and mandatory `X-Nox-Client` header verification on state-mutating endpoints.

### 3.2 Task Queue & Scheduler
- **Upstream OpenJarvis**: Uses in-memory asyncio queues and basic cron polling.
- **NoX**: Uses SQLite WAL persistence with two coordinated authorities:
  - `BriefingScheduler`: Sole authority for morning (04:00) and evening (18:00) Asia/Kolkata briefings into `briefing_runs`.
  - `JobScheduler`: Authoritative for planned execution (`job_plans`, `job_run_steps`) and daily 20:00 GitHub backups.
  - `PersistentScheduler`: Authoritative for daemon maintenance tasks (`schedules` and `schedule_runs` tables) with boot catch-up recovery.

### 3.3 Security & Egress Boundaries
- **Upstream OpenJarvis**: Relies primarily on model system prompt instructions to respect privacy.
- **NoX**: Hardware/framework enforced data boundary:
  - System messages carrying retrieved note chunks (Vault, Study) or private messages are tagged with `TaintFlag.PRIVATE` and `DataClass.LOCAL_ONLY`.
  - `EgressFilter` deterministically intercepts all cloud-bound requests and reroutes them to local models (`brain` / `resident`), with zero private data ever serialized to cloud endpoints.

### 3.4 Messaging & Social Connectors
- **Upstream OpenJarvis**: Bundles multiple unofficial web scraping drivers (WhatsApp Baileys, Twitter Puppeteer).
- **NoX**: Adheres strictly to safe, stable, official protocols:
  - Telegram: Official bot API long-polling with pairing codes.
  - Slack: Official Socket Mode.
  - Discord: Official gateway bot.
  - Feeds: Open standards (RSS/Atom, Bluesky, Mastodon public timelines, Reddit RSS).
  - Unofficial scraping is banned to protect user credentials and avoid IP/account blacklisting.
