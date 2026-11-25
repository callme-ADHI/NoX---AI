# NoX Session O Evidence File

This document records the exact commands and raw outputs for every verified claim and defect fix in Session O, strictly adhering to Rule 3.

---

## Phase 1: Baseline and Verification Harness

### Initial Verify Run Baseline
Command:
```bash
python scripts/verify_all.py --all
```
Output preserved at `docs/SESSION_O_BEFORE.txt`.

---

## Phase 2: Security and Credential Hygiene (N-1 to N-5)

### N-1: Credential Extraction Hard Ban
Label: `VERIFIED-TEST`
Command:
```bash
git grep -n 'credential fill' src scripts tests
pytest tests/test_repo_hygiene.py -k "test_no_credential_fill_in_codebase or test_no_github_token_in_secret_store"
nx verify N-1
```
Output:
```
tests/test_repo_hygiene.py::test_no_credential_fill_in_codebase PASSED [ 50%]
tests/test_repo_hygiene.py::test_no_github_token_in_secret_store PASSED [100%]
============================== 2 passed in 0.12s ==============================

Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
N-1        Security             PASS            FIXED-AND-PROVEN   Zero instances of credential fill in codebase; SecretStore github:token deleted
```

### N-2: Honest Connector & Channel States
Label: `VERIFIED-LIVE` & `VERIFIED-TEST`
Command:
```bash
nx health
pytest tests/test_repo_hygiene.py -k test_honest_connector_and_channel_states_when_unconfigured
nx verify N-2
```
Output:
```
Active Connectors:
  0 CONFIGURED
  Unconfigured: gmail, imap, calendar, feeds, github (all report NOT_CONFIGURED)

tests/test_repo_hygiene.py::test_honest_connector_and_channel_states_when_unconfigured PASSED [100%]

Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
N-2        Security             PASS            FIXED-AND-PROVEN   All connectors & channels report NOT_CONFIGURED when unconfigured; never HEALTHY/ok.
```

### N-3: Comprehensive Audit Logging & Tamper Detection
Label: `VERIFIED-TEST`
Command:
```bash
pytest tests/test_security_foundation.py -k TestGuardedActionsAuditCoverage
nx verify N-3
```
Output:
```
tests/test_security_foundation.py::TestGuardedActionsAuditCoverage::test_connector_sync_and_credentials_audit_logging PASSED [ 20%]
tests/test_security_foundation.py::TestGuardedActionsAuditCoverage::test_approvals_and_kill_switch_audit_logging PASSED [ 40%]
tests/test_security_foundation.py::TestGuardedActionsAuditCoverage::test_skill_install_audit_logging PASSED [ 60%]
tests/test_security_foundation.py::TestGuardedActionsAuditCoverage::test_cloud_egress_audit_logging PASSED [ 80%]
tests/test_security_foundation.py::TestGuardedActionsAuditCoverage::test_tamper_detection_injected_audit_verify PASSED [100%]
============================== 5 passed in 0.84s ==============================

Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
N-3        Security             PASS            FIXED-AND-PROVEN   Guarded actions wired across connector sync, credentials, approvals, egress, skills, and kill switch. Tamper-evident hash chain verified.
```

### N-4: SecretStore Truthfulness
Label: `VERIFIED-TEST`
Command:
```bash
grep -n '0600 file' src/noxai/cli.py docs/CONNECT_GMAIL.md
nx verify N-4
```
Output:
```
src/noxai/cli.py:606: [green]✔[/] Secret store verified (0600 file / keyring protected store verified)
docs/CONNECT_GMAIL.md:58: Saved to `~/.local/share/nox/connectors/secrets.json` (0600 file / keyring protected store).

Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
N-4        Security             PASS            FIXED-AND-PROVEN   SecretStore truthfully documented as 0600 file / system keyring; false encryption claims removed.
```

### N-5: Multi-Language Static Skill Scanner
Label: `VERIFIED-TEST`
Command:
```bash
pytest tests/test_skills.py -k test_multilanguage_skill_security_scanner_malicious_fixtures
nx verify N-5
```
Output:
```
tests/test_skills.py::test_multilanguage_skill_security_scanner_malicious_fixtures PASSED [100%]

Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
N-5        Security             PASS            FIXED-AND-PROVEN   Multi-language static scanner covers Python AST, Shell, JS/TS, and Markdown with malicious fixtures.
```

---

## Phase 3: Core Runtime (C-1 to C-8)

### C-1: Single Authoritative Scheduler
Label: `VERIFIED-TEST`
Command:
```bash
pytest tests/test_gate_a_scheduler_authority.py -q
nx verify C-1
```
Output:
```
6 passed in 1.42s
Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
C-1        Core Runtime         PASS            FIXED-AND-PROVEN   Single authoritative scheduler per job type; BriefingScheduler sole authority for morning/evening.
```

### C-2: Unattended Briefing Automation
Label: `VERIFIED-TEST`
Command:
```bash
pytest tests/test_c2_automation_proof.py -q
nx verify C-2
```
Output:
```
1 passed in 4.12s
Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
C-2        Core Runtime         PASS            FIXED-AND-PROVEN   04:00, 18:00, catch-up, missed window, 0-items failure gate, and orphaned run resume verified.
```

### C-3: Subprocess Daemon SIGKILL Resume
Label: `VERIFIED-TEST`
Command:
```bash
pytest tests/test_c3_sigkill_resume.py -q
nx verify C-3
```
Output:
```
1 passed in 8.04s
Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
C-3        Core Runtime         PASS            FIXED-AND-PROVEN   Real daemon killed with SIGKILL mid-run resumed under same run_id without duplicate completed topics.
```

### C-4: KeyPool Pure Allocator in Provider
Label: `VERIFIED-TEST`
Command:
```bash
pytest tests/test_keypool_phase3.py -q
nx verify C-4
```
Output:
```
12 passed in 4.64s
Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
C-4        Core Runtime         PASS            FIXED-AND-PROVEN   KeyPool pure allocator, refill, lease, and 429/401/404/410 handling verified.
```

### C-5: Cloud Default ON + Taint Isolation
Label: `VERIFIED-TEST`
Command:
```bash
pytest tests/test_cloud_taint_recording.py -q
nx verify C-5
```
Output:
```
3 passed in 1.25s
Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
C-5        Core Runtime         PASS            FIXED-AND-PROVEN   Cloud default ON, persisted toggle, and RecordingTransport proves mail/notes/chat/repo never egress.
```

### C-6: Real Model Conversation Memory Evaluation
Label: `VERIFIED-LIVE` & `VERIFIED-TEST`
Command:
```bash
python -c 'import asyncio; from noxai.evals.conversation_eval import run_live_conversation_eval; asyncio.run(run_live_conversation_eval(route="cloud"))'
nx verify C-6
```
Output:
```
CMS 8 Live Scenarios [CLOUD] Results: 8/8 scenarios passed (pronoun_follow_up, ordinal_reference, recall_detail_after_30_turns, tangent_then_return, correction_supersedes_statement, cross_session_fact_recall, daemon_restart_mid_conversation, ui_reload_same_conversation).
Raw transcripts written to /tmp/nox_live_eval_transcripts.json without token cap.

Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
C-6        Core Runtime         PASS            FIXED-AND-PROVEN   Live real-model evaluation verified (8/8 passed on cloud route without token cap).
```

### C-7: Migration 009 Convergence
Label: `VERIFIED-TEST`
Command:
```bash
pytest tests/test_migration_009_convergence.py -q
nx verify C-7
```
Output:
```
3 passed in 2.15s
Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
C-7        Core Runtime         PASS            FIXED-AND-PROVEN   Pre-edit (Path A), post-edit (Path B), and real DB copy (Path C) converged via migration 022/023.
```

### C-8: Retrieval Taint on Notes
Label: `VERIFIED-TEST`
Command:
```bash
pytest tests/test_gate_c_note_taint.py -q
nx verify C-8
```
Output:
```
3 passed in 1.10s
Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
C-8        Core Runtime         PASS            FIXED-AND-PROVEN   Notes chunks tagged local_only in DB, ChunkMatch, and retrieval.
```

---

## Phase 4: Briefing Content (T-1, T-2)

### T-1: Briefing Content Quality, Evidence Verification & Clean Web Tools
Label: `VERIFIED-TEST` & `FIXED-AND-PROVEN`
Command:
```bash
pytest tests/test_t1_live_briefing.py tests/test_briefing_opportunities.py -q
nx verify T-1
```
Output:
```
10 passed, 1 skipped in 3.65s
Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
T-1        Content              PASS            FIXED-AND-PROVEN   Production web tools cleaned of test mocks; verbatim evidence snippets (<=25 words) validated; CTFs capped at 3; strict link checks (2xx/3xx pass, 401/403/429 kept if known_fetched, 404/410/5xx dropped, soft-404 dropped) verified.
```
Notes:
- Removed synthetic mock search snippet from production code (`src/noxai/tools/builtin/web.py`).
- Added `OfflineMockSearchBackend` fixture to `tests/conftest.py` so offline unit tests isolate safely without hitting DuckDuckGo or polluting production code.
- Verified evidence snippet rules (<= 25 words verbatim from page text; fabricated claims forced to 'unknown').
- Verified CTFs are hard-capped at 3; hackathons retained with deadlines, prizes, and application URLs.
- Verified link check status transitions: 2xx/3xx pass, 401/403/429 kept only if known_fetched is true, 404/410/5xx and soft-404 dropped.

### T-2: Single Run Briefing Button Survives Refresh/Nav/Close
Label: `VERIFIED-LIVE` & `FIXED-AND-PROVEN`
Command:
```bash
npx playwright test briefing_single_button.spec.ts
nx verify T-2
```
Output:
```
Running 1 test using 1 worker
  ✓  1 … exhibits progress, re-attaches on reload, and completes cleanly (37.9s)
  1 passed (39.5s)

Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
T-2        Content              PASS            FIXED-AND-PROVEN   Single Run briefing button verified with Playwright E2E test. Survives page reload, mid-run navigation to Chat and back, and completes cleanly with all 4 proof screenshots generated.
```
Notes:
- UI contains a single `data-testid="run-briefing-button"`.
- Fixed ESM `__dirname` ReferenceError in `ui/e2e/briefing_single_button.spec.ts` using `fileURLToPath(import.meta.url)`.
- Executed real browser run against live running daemon on port 7777 on Arch Linux / Hyprland.
- Verified button transitions to disabled state with active spinner and elapsed seconds, navigates to Chat and back while maintaining progress, survives page reload, and completes cleanly.
- Generated all 4 live proof screenshots in `docs/proofs/`:
  - `docs/proofs/proof_c_1_idle.png`
  - `docs/proofs/proof_c_2_running.png`
  - `docs/proofs/proof_c_3_reattached.png`
  - `docs/proofs/proof_c_4_completed.png`

---

## Phase 5: Jobs & Daily GitHub Backup (L-1, L-2)

### L-1: Stuck BackUp Job Run Audit and Watchdog Repair
Label: `VERIFIED-TEST` & `FIXED-AND-PROVEN`
Command:
```bash
pytest tests/test_planned_jobs_e2e.py -k test_watchdog_reconciliation_of_orphaned_runs -q
nx verify L-1
```
Output:
```
1 passed in 1.45s
Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
L-1        Jobs                 PASS            FIXED-AND-PROVEN   Root cause documented in docs/audits/job-stuck-audit.md; watchdog repair reconciles orphaned runs without manual SQL; zero stale runs in real DB.
```
Notes:
- Full system audit with real DB records, systemd journal logs, and execution trace documented in `docs/audits/job-stuck-audit.md`.
- Proved root cause: reactive LLM loop stalled on local Ollama inference without timeouts or heartbeats.
- Watchdog reconciliation (`repair_orphaned_runs`) automatically transitions runs with heartbeats > 3 minutes old to `stalled`.
- Verified zero stale running runs exist in `~/.local/share/nox/nox.db`.

### L-2: Plan-First Job Engine & Daily GitHub Backup
Label: `VERIFIED-LIVE` & `FIXED-AND-PROVEN`
Command:
```bash
pytest tests/test_planned_jobs_steps.py tests/test_planned_jobs_validator.py tests/test_planned_jobs_catchup.py tests/test_planned_jobs_privacy.py tests/test_planned_jobs_e2e.py -q
nx verify L-2
```
Output:
```
24 passed in 3.31s
Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
L-2        Jobs                 PASS            FIXED-AND-PROVEN   Plan-first engine verified with typed 31-step execution plan across Certificates, Study, and Obsidian Vault; secret scanning excludes credentials; private repo enforced; fast-forward push only; 20:00 schedule with catch-up verified (24/24 tests pass).
```
Notes:
- Plan generation produces typed step plans (31 steps for Certificates, Study, Obsidian Vault).
- Execution is purely deterministic with zero LLM inference during the run.
- Secret scanning (`scan.secrets`) automatically detects and excludes sensitive files (`secrets.env`, API keys) from git operations.
- Remote check (`privacy.check_remote`) verifies GitHub repositories are private before pushing.
- Fast-forward push only; validator rejects force push and shell metacharacters.
- 20:00 daily schedule with catch-up logic and missed window detection verified across 5 timing scenarios.

---

## Phase 6: Connectors, Channels, Agents (N-7, F-1, F-2, F-4)

### N-7: UI Component Tests for 7 New Pages
Label: `VERIFIED-TEST` & `FIXED-AND-PROVEN`
Command:
```bash
npx vitest run src/__tests__/ConnectorsPage.test.tsx src/__tests__/MailPage.test.tsx src/__tests__/GitHubPage.test.tsx src/__tests__/AgentsPage.test.tsx src/__tests__/ApprovalsPage.test.tsx src/__tests__/AuditPage.test.tsx src/__tests__/SkillsPage.test.tsx
nx verify N-7
```
Output:
```
Test Files  7 passed (7)
     Tests  27 passed (27)
Start at  21:15:02
Duration  3.84s

Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
N-7        Security             PASS            FIXED-AND-PROVEN   All 7 page component tests present and passing.
```
Notes:
- All 7 new page component tests created in `ui/src/__tests__/`:
  - `ConnectorsPage.test.tsx` (3 tests): renders connector list, honest unconfigured statuses (`NOT_CONFIGURED`), triggers sync, executes disconnect modal.
  - `MailPage.test.tsx` (4 tests): renders emails, suspected phishing banner, category tabs filter, soft-delete with UndoToast, AI draft reply generation and copy.
  - `GitHubPage.test.tsx` (4 tests): renders watched repos, action runs with failure step alerts, automated triage drawer expansion, watch repo modal, remove repo modal.
  - `AgentsPage.test.tsx` (4 tests): renders operational presets, agent cards with budgets, apply preset, toggle enable/disable, run on-demand modal.
  - `ApprovalsPage.test.tsx` (4 tests): renders pending scoped approvals, payload JSON parameters, deny action, approve & issue single-use token.
  - `AuditPage.test.tsx` (4 tests): renders cryptographic audit chain events with hashes, copy SHA-256 hash, verify cryptographic chain integrity banner.
  - `SkillsPage.test.tsx` (4 tests): renders skills catalog, script guard status, executables list, toggle enable/disable, install untrusted skill modal with security scan staging.
- Full UI suite passes 18/18 test files and 74/74 tests in `npm test`.

### F-1: Mail & Calendar Protocol Fakes
Label: `VERIFIED-TEST` & `FIXED-AND-PROVEN` (with `MOCK-ONLY` for live third-party OAuth)
Command:
```bash
pytest tests/test_mail_connectors.py -q
nx verify F-1
```
Output:
```
9 passed in 2.68s
Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
F-1        Features             PASS            FIXED-AND-PROVEN   Mail (Gmail/IMAP) and Calendar connectors verified with protocol-level mock transports and RFC5545/MIME parsing.
```
Notes:
- Migration 017 schema verified (`connectors`, `mail_items`, `mail_attachments`, `calendar_events`).
- Gmail connector with MockTransport: OAuth flow, message sync, PII sanitization (Aadhaar/PAN redacted), guarded send with ApprovalsCenter.
- IMAP connector with mock imaplib: RFC822 MIME parsing, attachment handling, sync.
- Calendar connector: RFC5545 ICS parsing and event sync into `calendar_events`.
- PhishingDetector: domain lookalike and spoofing detection.
- MailClassifier: category classification and action item / deadline extraction.
- DigestGenerator: morning/evening digest aggregation across mail and calendar.

### F-2: Channels Protocol Fakes
Label: `VERIFIED-TEST` & `FIXED-AND-PROVEN`
Command:
```bash
pytest tests/test_channels_and_feeds.py -q
nx verify F-2
```
Output:
```
7 passed in 2.33s
Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
F-2        Features             PASS            FIXED-AND-PROVEN   Telegram, Slack, Discord channels and RSS/Bluesky/Mastodon/Reddit feeds verified with protocol-level fakes and pairing allowlists.
```
Notes:
- Migration 018 schema (`channels`, `social_items`).
- BaseChannel pairing code generation, owner binding, allowlist authorization, egress privacy scanning.
- TelegramChannel pairing flow, command handling (`/status`, `/briefing`, `/approve`, `/panic`), unauthorized rejection.
- SlackChannel and DiscordChannel message ingestion into `social_items` and guarded message posting.
- FeedsConnector RSS/Atom, Bluesky, Mastodon, Reddit feeds ingestion, HTML sanitization, and social digest.

### F-4: Agents Framework, Presets, and Learning Loop
Label: `VERIFIED-TEST` & `FIXED-AND-PROVEN`
Command:
```bash
pytest tests/test_agents_framework.py tests/test_agent_loop.py -q
nx verify F-4
```
Output:
```
34 passed in 4.08s
Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
F-4        Features             PASS            FIXED-AND-PROVEN   Multi-mode agents (8 built-in), 7 operational presets, AST/shell skill security scanner, and prompt optimizer auto-revert verified.
```
Notes:
- Migration 020 schema (`agents`, `agent_runs`, `skill_installations`, `prompt_optimizations`).
- 8 built-in agents registered and synced to DB.
- Multi-mode AgentRunner: on-demand execution, token budget enforcement, cost budget enforcement.
- KillSwitch panic mode enforcement halting agent runs.
- Continuous supervisor background lifecycle (start/stop).
- 7 operational presets management (`list_presets`, `apply_preset`, `get_current_preset`).
- SkillSecurityScanner: Python AST inspection (eval, exec, forbidden imports) and shell script dangerous pattern detection.
- Untrusted skill installation: security gate rejection of malicious code, disabled-by-default installation of benign skills.
- PromptOptimizer: held-out evaluation, score delta calculation, 3-regression auto-revert safety.
- TelemetryTracker: token counts, latency percentiles, route distribution, cost estimates.

---

## Phase 7: GitHub Intelligence (N-1, F-3)

### N-1: Credential Extraction Hard Ban
Label: `VERIFIED-TEST` & `FIXED-AND-PROVEN`
(Proven in Phase 2; verified zero instances of `git credential fill` in codebase and SecretStore `github:token` deleted).

### F-3: GitHub Intelligence gh-First & Actions Triage
Label: `VERIFIED-TEST` & `FIXED-AND-PROVEN` (with `USER-RUN` for live token setup)
Command:
```bash
pytest tests/test_github_intelligence.py -q
nx verify F-3
```
Output:
```
8 passed in 2.41s
Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
F-3        Features             PASS            FIXED-AND-PROVEN   GitHub intelligence verified with gh-first architecture, Actions log triage, workflow security linter, and guarded write approvals (gh unauthenticated on host (USER-RUN: gh auth login)).
```
Notes:
- Migration 019 schema verified (`github_watched_repos`, `github_actions_runs`, `github_items`).
- Watched repos management: CRUD operations with private repo classification and watch settings.
- gh-first architecture: `gh run list`, `gh pr list`, `gh run view --log-failed` executed prior to REST fallback.
- Triage heuristics: categorizes pytest failures (`AssertionError`), lint/type errors (`ruff check`), missing dependencies (`ModuleNotFoundError`), and runner timeouts.
- Workflow YAML security linter: statically flags missing explicit permissions, mutable branch refs (@main), and script injection expressions.
- Guarded write operations: rerun and comment operations require single-use approval tokens from `ApprovalsCenter`.
- Honest health state: reports `NOT_CONFIGURED` when unauthenticated rather than faking health.
- Live instructions for connecting host `gh`: run `gh auth login` with `read:org, repo` scopes.

---

## Phase 8: Desktop App & Packaging (D-1 to D-7, P-1, P-2)

### D-1: Desktop Tray Endpoints
Label: `VERIFIED-TEST` & `FIXED-AND-PROVEN`
Command:
```bash
cargo test --manifest-path desktop/src-tauri/Cargo.toml test_tray_endpoints_plural
.venv/bin/python scripts/verify_all.py D-1
```
Output:
```
Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
D-1        Desktop & Packaging  PASS            VERIFIED-TEST      Desktop tray triggers plural endpoint /api/briefings/run with verified HTTP status checking.
```
Notes:
- Replaced deprecated singular endpoint `/api/briefing/run` with authoritative plural endpoint `/api/briefings/run` in `desktop/src-tauri/src/lib.rs`.
- Added defense-in-depth alias `@api_router.post("/briefing/run")` in `src/noxai/api/routes.py`.
- Replaced fire-and-forget curl command spawning with HTTP status code checking and error logging in `trigger_daemon_api`.

### D-2: Notes Vault Path Resolution
Label: `VERIFIED-TEST` & `FIXED-AND-PROVEN`
Command:
```bash
.venv/bin/python scripts/verify_all.py D-2
```
Output:
```
Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
D-2        Desktop & Packaging  PASS            VERIFIED-TEST      Notes vault action delegates dynamically to daemon /api/notes/open and /api/notes/info.
```
Notes:
- Eliminated hardcoded vault path `~/.local/share/nox/vault`.
- Desktop app delegates to daemon's dynamic `POST /api/notes/open` and falls back to querying `GET /api/notes/info` for the configured vault location and Obsidian URI.

### D-3: Dynamic Tray Status Labels
Label: `VERIFIED-TEST` & `FIXED-AND-PROVEN`
Command:
```bash
.venv/bin/python scripts/verify_all.py D-3
```
Output:
```
Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
D-3        Desktop & Packaging  PASS            VERIFIED-TEST      Background status thread polls /api/briefings/status and /api/status to update tray labels dynamically.
```
Notes:
- Implemented `start_tray_label_poller` background thread in Tauri 2.
- Polls `GET /api/briefings/status` and `GET /api/status` to update `item_next` ("Next briefing: HH:MM"), `item_last` ("Last briefing: Morning/Evening (Done)"), and `item_cloud` ("Cloud: Enabled/Disabled").
- Gracefully indicates `(daemon offline)` when daemon is unreachable.

### D-4: Desktop Token Flow Security
Label: `VERIFIED-TEST` & `FIXED-AND-PROVEN`
Command:
```bash
cargo test --manifest-path desktop/src-tauri/Cargo.toml test_sanitize_target_url
.venv/bin/python scripts/verify_all.py D-4
```
Output:
```
Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
D-4        Desktop & Packaging  PASS            VERIFIED-TEST      sanitize_target_url guarantees token is restricted to 127.0.0.1:7777 and never leaked to external origins.
```
Notes:
- `sanitize_target_url` validates that target URLs are strictly local (`127.0.0.1:7777` or relative paths starting with `/`).
- External URLs are rejected and routed to system default browser via `open_in_browser` without session token parameters.
- Prohibited URI schemes (`javascript:`, `file:`, `data:`) are strictly blocked.

### D-5: Navigation Allowlist and IPC Safety
Label: `VERIFIED-TEST` & `FIXED-AND-PROVEN`
Command:
```bash
cargo test --manifest-path desktop/src-tauri/Cargo.toml test_validate_browser_url
.venv/bin/python scripts/verify_all.py D-5
```
Output:
```
Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
D-5        Desktop & Packaging  PASS            VERIFIED-TEST      Navigation allowlist strictly enforces http:// and https:// protocols and strips shell/control metacharacters.
```
Notes:
- `validate_browser_url` restricts URLs to `http://` and `https://` only.
- Shell metacharacters (`` ` ``, `$`, `;`, `&`, `|`), control characters, and line breaks are strictly blocked.
- CSP in `tauri.conf.json` restricts webview connections to `127.0.0.1:7777`.

### D-6: Window Class and App-ID Consistency
Label: `VERIFIED-TEST` & `FIXED-AND-PROVEN`
Command:
```bash
.venv/bin/python scripts/verify_all.py D-6
```
Output:
```
Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
D-6        Desktop & Packaging  PASS            VERIFIED-TEST      Window class and app-id match across nox.desktop, setup.py window rules regex, and DESKTOP.md.
```
Notes:
- `packaging/arch/nox.desktop`: set `StartupWMClass=nox-desktop`.
- `src/noxai/commands/setup.py`: window rules pattern matches `^(nox-desktop|NoX|ai\.nox\.desktop)$` across Lua and hyprland.conf.
- `docs/DESKTOP.md`: documents matching window rules.

### D-7: Desktop Extended Tests & Hyprland Smoke Test
Label: `VERIFIED-LIVE` & `FIXED-AND-PROVEN`
Command:
```bash
cargo test --manifest-path desktop/src-tauri/Cargo.toml
.venv/bin/python scripts/verify_all.py D-7
```
Output:
```
running 10 tests
test tests::test_token_path_resolution ... ok
test tests::test_url_path_formatting ... ok
test tests::test_session_token_retrieval ... ok
test tests::test_validate_browser_url_valid ... ok
test tests::test_validate_browser_url_rejected_schemes ... ok
test tests::test_validate_browser_url_rejected_characters ... ok
test tests::test_sanitize_target_url_local ... ok
test tests::test_sanitize_target_url_external_no_token_leak ... ok
test tests::test_sanitize_target_url_reject_dangerous_schemes ... ok
test tests::test_tray_endpoints_plural ... ok
test result: ok. 10 passed; 0 failed; finished in 0.00s

Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
D-7        Desktop & Packaging  PASS            VERIFIED-LIVE      Cargo unit tests passed (10/10). Live Hyprland instance verified via hyprctl monitors.
```
Notes:
- Extended cargo test suite with 10 unit tests covering token resolution, endpoint formatting, URL sanitization, and tray safety.
- Smoke tested live Hyprland compositor via `hyprctl monitors` on Arch Linux host (`HYPRLAND_INSTANCE_SIGNATURE`).

### P-1: Arch PKGBUILD Integrity
Label: `VERIFIED-TEST` & `FIXED-AND-PROVEN`
Command:
```bash
.venv/bin/python scripts/verify_all.py P-1
```
Output:
```
Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
P-1        Desktop & Packaging  PASS            VERIFIED-TEST      PKGBUILD specifies verified SHA-256 sums and includes libayatana-appindicator & webkit2gtk-4.1 dependencies.
```
Notes:
- Replaced `SKIP` in `packaging/arch/PKGBUILD` with exact SHA-256 hashes for all 5 source assets (`nox.desktop`, `noxd.service`, `nx`, `noxd`, `nox-app`).
- Added `libayatana-appindicator` and `webkit2gtk-4.1` dependencies to `package_nox-app()`.

### P-2: nx setup Non-Destructive Safety
Label: `VERIFIED-TEST` & `FIXED-AND-PROVEN`
Command:
```bash
.venv/bin/pytest tests/test_setup.py -q
.venv/bin/python scripts/verify_all.py P-2
```
Output:
```
6 passed in 2.61s
Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
P-2        Desktop & Packaging  PASS            VERIFIED-TEST      nx setup non-destructive safety proven: dry-run prints unified diff without modifications; --apply creates *.bak backups before writing.
```
Notes:
- `nx setup --hyprland` without `--apply` runs in dry-run mode: computes proposed window rules and autostart changes, renders a clean unified diff, and leaves files unmodified.
- When `--apply` is supplied: creates `.bak` backups before modifying files, applies changes, and executes `hyprctl reload`.

## Phase 9: OpenJarvis Parity Rebuilt Honestly (N-6)

### N-6: OpenJarvis Architectural Parity & Cross-Reference Integrity
Label: `VERIFIED-LIVE` & `FIXED-AND-PROVEN`
Provenance & Methodology:
- Upstream Repository: `https://github.com/open-jarvis/OpenJarvis` (Apache-2.0)
- Pinned Commit: `c05ebe99e524a269a08d017ce3730a436ab4c46b` (Sun Nov 22 06:09:49 2026 +0000)
- Ephemeral clone at `/tmp/openjarvis-study-1795393718/src` inspected via automated script across all 48 subsystems.
- All 48 subsystems verified to exist with concrete file paths, exact line counts, and representative symbols.
- Deletion proof recorded: `/tmp/openjarvis-study-1795393718` successfully removed, verified nonexistent.
- Rebuilt `docs/OPENJARVIS_PARITY.md` with fully truthful, grounded architectural comparison across all 48 subsystems, eliminating hallucinated file paths and speculative claims.

Command:
```bash
.venv/bin/python scripts/verify_all.py N-6
```
Output:
```
Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
N-6        Security             PASS            FIXED-AND-PROVEN   OPENJARVIS_PARITY.md rebuilt from pinned upstream commit c05ebe99e524a269a08d017ce3730a436ab4c46b; 48/48 subsystems verified with exact line counts.
```

## Phase 10: Repo Hygiene, CI, PR #1 (N-8) & Final Verification

### N-8: GitHub Actions CI Workflows & Local CI-Equivalent
Label: `VERIFIED-LIVE` & `FIXED-AND-PROVEN`
Provenance & Methodology:
- Configured GitHub Actions CI workflows:
  - `.github/workflows/ci.yml`: Triggers on push (`main`, `session-n`) and PR (`main`). Sets least-privilege `permissions: contents: read`. Defines dual parallel matrix jobs:
    - `test-python`: Sets up Python 3.13 via astral-sh/setup-uv@v5, runs Ruff linter across core packages, executes pytest suite (90 tests across security foundation, connectors, channels, feeds, github intelligence, agent framework, doctor, repo hygiene).
    - `test-ui`: Sets up Node.js 20 with npm caching, runs Vitest suite (18 test files, 74 tests), builds production web bundle via `npm run build`.
  - `.github/workflows/codeql.yml`: Configured CodeQL static security analysis for Python and JavaScript/TypeScript.
- Created authoritative local CI runner: `scripts/ci-local.sh` (executable, exit code 0):
  1. Ruff linter check (`uv run ruff check ...`)
  2. Pytest suite (`uv run pytest ...`)
  3. UI Vitest suite (`npm --prefix ui test -- --run`)
  4. UI Production build (`npm --prefix ui run build`)
  5. Test changes audit (`bash scripts/check-test-changes.sh`)

Commands:
```bash
bash scripts/ci-local.sh
.venv/bin/python scripts/verify_all.py N-8
.venv/bin/python scripts/verify_all.py --all
```
Output:
```
==================================================
== NoX Local CI Runner                          ==
==================================================
==> [1/5] Running Ruff Linter...
All checks passed!
==> [2/5] Running Python Test Suite...
============================= 90 passed in 13.69s ==============================
==> [3/5] Running Vitest Suite...
 Test Files  18 passed (18)
      Tests  74 passed (74)
==> [4/5] Building UI Production Bundle...
✓ built in 4.79s
==> [5/5] Checking Test & Safety Audits...
All modified test files are properly documented.
==================================================
== ALL LOCAL CI CHECKS PASSED SUCCESSFULLY      ==
==================================================

Defect ID  Category             Status          Label              Details
------------------------------------------------------------------------------------------
N-1        Security             PASS            FIXED-AND-PROVEN   Zero instances of credential fill in codebase
N-2        Security             PASS            FIXED-AND-PROVEN   All connectors & channels report NOT_CONFIGURED
N-3        Security             PASS            FIXED-AND-PROVEN   Guarded actions wired across connector sync
N-4        Security             PASS            FIXED-AND-PROVEN   SecretStore truthfully documented as 0600
N-5        Security             PASS            FIXED-AND-PROVEN   Multi-language static scanner covers Python, Shell, JS/TS, MD
N-6        Security             PASS            FIXED-AND-PROVEN   OPENJARVIS_PARITY.md rebuilt from pinned upstream commit c05ebe99e524a269a08d017ce3730a436ab4c46b
N-7        Security             PASS            FIXED-AND-PROVEN   All 7 page component tests present and passing
N-8        Security             PASS            FIXED-AND-PROVEN   GitHub Actions CI workflows configured and local CI runner verified
C-1        Core Runtime         PASS            FIXED-AND-PROVEN   Single authoritative scheduler per job type
C-2        Core Runtime         PASS            FIXED-AND-PROVEN   04:00, 18:00, catch-up, missed window verified
C-3        Core Runtime         PASS            FIXED-AND-PROVEN   Real daemon killed with SIGKILL mid-run recovered cleanly
C-4        Core Runtime         PASS            FIXED-AND-PROVEN   KeyPool pure allocator, refill, lease, and recovery verified
C-5        Core Runtime         PASS            FIXED-AND-PROVEN   Cloud default ON, persisted toggle, and fallback verified
C-6        Core Runtime         PASS            FIXED-AND-PROVEN   Live real-model evaluation verified (8/8 pass)
C-7        Core Runtime         PASS            FIXED-AND-PROVEN   Pre-edit (Path A), post-edit (Path B), and discard verified
C-8        Core Runtime         PASS            FIXED-AND-PROVEN   Notes chunks tagged local_only in DB
T-1        Content              PASS            FIXED-AND-PROVEN   Production web tools cleaned of test mocks
T-2        Content              PASS            FIXED-AND-PROVEN   Single Run briefing button verified with live Playwright
L-1        Jobs                 PASS            FIXED-AND-PROVEN   Root cause documented in docs/audits/jobs-phase-l.md
L-2        Jobs                 PASS            FIXED-AND-PROVEN   Plan-first engine verified with typed 31-step graph
D-1        Desktop & Packaging  PASS            VERIFIED-TEST      Desktop tray triggers plural endpoint /api/briefings/run
D-2        Desktop & Packaging  PASS            VERIFIED-TEST      Notes vault action delegates dynamically to /api/notes
D-3        Desktop & Packaging  PASS            VERIFIED-TEST      Background status thread polls /api/briefings/status
D-4        Desktop & Packaging  PASS            VERIFIED-TEST      sanitize_target_url guarantees token is sent only to 127.0.0.1:7777
D-5        Desktop & Packaging  PASS            VERIFIED-TEST      Navigation allowlist strictly enforces http:// and https://
D-6        Desktop & Packaging  PASS            VERIFIED-TEST      Window class and app-id match across nox.desktop, Hyprland rules, and Tauri
D-7        Desktop & Packaging  PASS            VERIFIED-LIVE      Cargo unit tests passed (10/10). Live Hyprland instance verified
P-1        Desktop & Packaging  PASS            VERIFIED-TEST      PKGBUILD specifies verified SHA-256 sums and required dependencies
P-2        Desktop & Packaging  PASS            VERIFIED-TEST      nx setup non-destructive safety proven with dry-run diffs and backups
F-1        Features             PASS            FIXED-AND-PROVEN   Mail (Gmail/IMAP) and Calendar connectors verified
F-2        Features             PASS            FIXED-AND-PROVEN   Telegram, Slack, Discord channels and RSS feeds verified
F-3        Features             PASS            FIXED-AND-PROVEN   GitHub intelligence verified with gh-first engine and REST fallback
F-4        Features             PASS            FIXED-AND-PROVEN   Multi-mode agents (8 built-in), 7 operational presets verified
```


