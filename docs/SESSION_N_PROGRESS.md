# NoX Session N Progress & Checklist

## Acceptance Matrix

- [x] **N1**: Session M integrity gates pass (one scheduler authority, KeyPool really used, note retrieval taint-proof, every test change justified, full suites green).
- [x] **N2**: OpenJarvis feature matrix document: every upstream capability with status ported / partial / adapted differently / excluded-with-reason, and every "ported" row has code, tests and a live or protocol-level proof.
- [x] **N3**: Security layer: guardrails (secret/PII/injection scanners), tamper-evident audit log, per-skill/agent permission manifests, approvals center, secret store, kill switch.
- [x] **N4**: Connector framework with typed health, retry, rate-limit handling and graceful degradation; daemon start never blocked by a connector.
- [x] **N5**: Mail: Gmail (OAuth, read-only default) and generic IMAP; Calendar (read-only) and ICS; user-defined mail profile; triage, digest, thread summaries, search, deadline and event extraction, reply drafts, guarded sending.
- [x] **N6**: Chat and social: Telegram (owner remote control + inbox), Slack, Discord, public feeds (RSS/Atom, Bluesky, Mastodon, Reddit RSS); WhatsApp and unofficial scraping excluded with reasons.
- [x] **N7**: GitHub intelligence via gh or fine-grained token: repo watch, Actions status and failure triage, PR/issue/notification summaries, security alerts, workflow review, guarded write actions, push verification, Actions watch after push.
- [x] **N8**: Agents framework (on-demand, scheduled, continuous) with built-in agents, presets, budgets, approvals, start/stop/delete.
- [x] **N9**: Learning loop and telemetry: optimize prompts/skills from traces and feedback with offline evaluation and auto-revert; bench; cost/latency telemetry.
- [x] **N10**: UI, CLI, doctor, health and selftest cover everything; delete controls everywhere data is stored.
- [x] **N11**: Skill import from GitHub repos and Hermes/OpenClaw-style catalogs, treated as untrusted (staged, scanned, disabled by default).
- [ ] **N12**: Repo hygiene and CI: secret scan of the full git history is clean, .github workflows added, branch pushed, draft PR opened, Actions run watched with NoX's own GitHub tooling and fixed until green.
- [ ] **N13**: Packaging and docs updated (PKGBUILD deps, INSTALL, CONNECTORS guides, OPERATIONS, CHANGELOG, README).
- [ ] **N14**: Final report with USER-RUN steps and an honest NOT-DONE list.

---

## Phases & Deliverables Checklist

### Phase 1: Baseline and Session M Integrity Gates
- [x] 1. Record baseline: git status, branch (session-m -> session-n), full pytest, mypy src, ruff src tests, vitest, cargo test, nx doctor. Back up real DB (nx backup create).
- [x] 2. Gate A, one scheduler authority: list every scheduler (BriefingScheduler, scheduler/persistent.py, legacy jobs-table). Unify into one authoritative path per job type, prove in temp instance that morning & evening slots fire exactly once.
- [x] 3. Gate B, KeyPool: paste every non-test call site of lease() & report_outcome(); show "nx keys status" from daemon API during live call.
- [x] 4. Gate C, taint on retrieval: retrieved note chunks (Vault, Study), mail & chat are local_only; assert with recording HTTP transport that no text/path is in cloud request body.
- [x] 5. Gate D, test integrity: produce `docs/audits/session-m-test-changes.md` listing every existing test/auth/router/orchestrator/privacy change with reason; revert changes that only masked failures.
- [x] 6. Gate E, Session L deliverables check: verify `job_plans`, `job_run_steps`, plan-first executor, delete controls, daily 20:00 GitHub backup with private-repo check.

### Phase 2: OpenJarvis Gap Analysis
- [x] 1. Shallow-clone OpenJarvis to `/tmp` (pinned commit `77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b`).
- [x] 2. Create `docs/OPENJARVIS_PARITY.md` with complete capability matrix (module, function, status: ported/partial/adapted/excluded, NoX code, decision/reason, test/proof pointer).
- [x] 3. Clean up `/tmp` clone with guarded rm and record deletion proof.

### Phase 3: Security and Privacy Foundation
- [x] 1. Guardrails module: input scanner (injection, hidden markers, invisible Unicode), output scanner (secrets, tokens, keys), PII redactor (Indian Aadhaar, PAN, IFSC/bank, UPI, cards, OTP, phone, emails). Reversible locally via in-memory placeholder map.
- [x] 2. Audit log: append-only `audit_events` with SHA-256 hash chain and `nx audit verify`.
- [x] 3. Secret store abstraction: libsecret keyring with fallback to 0600 file under `~/.local/share/nox/connectors/`.
- [x] 4. Permission manifests: declarations for skills, agents, connectors (deny by default).
- [x] 5. Approvals center: pending write actions with preview, expiry, single-use scoped approval from UI/CLI/chat.
- [x] 6. Kill switch: `nx panic` and UI button (disables connectors/channels/continuous agents, revokes pending approvals, logs audit event), `nx panic --resume`.
- [x] 7. Tests: scanner tables, redactor round trips, audit chain tampering, permission denial, approval scope, kill switch, planner/summariser privacy with recording transport.

### Phase 4: Connector Framework, Mail, Calendar
- [x] 1. Connector framework: typed health, retry/rate-limit, graceful degradation, non-blocking daemon startup, supervised sync task (`src/noxai/connectors/base.py`).
- [x] 2. Gmail & IMAP: Gmail REST API with httpx & OAuth installed-app PKCE (default gmail.readonly, optional compose/send), generic IMAP over SSL with app passwords. Local store `mail_items` with metadata attachments (`src/noxai/connectors/gmail.py`, `src/noxai/connectors/imap.py`). Calendar: Google Calendar read-only & ICS import (`src/noxai/connectors/calendar.py`).
- [x] 3. Mail profile & intelligence: user-editable profile (`~/.config/nox/mail_profile.toml`). Pre-classification, deadline/event extraction, briefing digest, search/Q&A, draft replies with approval flow, phishing/safety flags (`src/noxai/connectors/mail_profile.py`).
- [x] 4. Tests: protocol-level fake IMAP & fake Gmail/Calendar HTTP servers, classifier tables, redaction before cloud, draft/send approvals, retention delete. Document `docs/CONNECT_GMAIL.md`. All 9 tests green in `tests/test_mail_connectors.py`.

### Phase 5: Channels and Social
- [x] 1. Telegram bot: owner pairing with one-time code, allow-list, commands, private output protection.
- [x] 2. Slack (Socket Mode) & Discord bot: read into Social digest, guarded posting, untrusted data handling.
- [x] 3. Feeds connector: RSS/Atom, Bluesky, Mastodon public timelines, Reddit RSS.
- [x] 4. Excluded protocols documented (WhatsApp Baileys, X, LinkedIn, Instagram, Facebook).
- [x] 5. Tests: protocol-level fakes, pairing, allow-list, spoofed sender, private-output blocking, reconnect.

### Phase 6: GitHub and Actions Intelligence
- [x] 1. Auth via `gh` CLI subprocess calls; fallback fine-grained GITHUB_TOKEN in secret store.
- [x] 2. Watchlist: repos to watch with public/private privacy classification.
- [x] 3. Actions status & triage: workflow conclusions, failing step extraction, plain-language triage, flaky-test detection, failure notifications.
- [x] 4. Inbox summaries: review requests, mentions, PR summaries, Dependabot/security alerts.
- [x] 5. Workflow review: workflow linting, unpinned actions, injection check, proposed diff patch.
- [x] 6. Guarded writes: re-run failed jobs, dispatch workflow, draft comment/PR.
- [x] 7. Push verification and watch (`nx gh watch`).
- [x] 8. Skills: `github-actions-triage` and `pr-review-summary` (agentskills.io format).
- [x] 9. Tests: fake GitHub REST/gh transport, privacy test, triage fixtures, workflow lint fixtures, live read-only call on callme-ADHI/NoX---AI.

### Phase 7: Agents Framework, Presets, Learning Loop, Telemetry
- [x] 1. Agent spec: `agents/<name>.toml` + prompt file, tables `agents` and `agent_runs`. Supervised execution.
- [x] 2. Built-in agents: deep_research, morning_digest, inbox_triage, github_ci_watch, scheduled_monitor, code_assistant, notes_assistant, simple chat.
- [x] 3. Presets: `nx preset list|apply` (morning-digest, deep-research, code-assistant, scheduled-monitor, chat-simple, inbox-triage, github-ops).
- [x] 4. Skill import: `nx skill install` from GitHub / Hermes / OpenClaw in agentskills.io format with static security scanner, installed disabled by default.
- [x] 5. Learning loop: `nx optimize prompts|skills`, offline eval, auto-revert after 3 regressions, `nx bench skills`.
- [x] 6. Telemetry: tokens, latency, route, cost in UI and `nx traces stats`.
- [x] 7. Optional local OpenAI-compatible endpoint evaluation.
- [x] 8. Tests: agent budgets, continuous restart, kill switch, preset apply/revert, skill scanner fixtures, optimizer, SIGKILL recovery.

### Phase 8: UI, CLI, Health, Selftest
- [x] 1. UI pages (night-sky theme): Connectors, Mail, GitHub, Agents, Approvals, Audit viewer, Skills. Delete controls with undo semantics.
- [x] 2. CLI commands: `nx connect|disconnect`, `nx mail`, `nx gh`, `nx agents`, `nx preset`, `nx skill`, `nx audit verify`, `nx panic`, `nx health`, `nx selftest [--live]`.
- [x] 3. Unified health and doctor tables.
- [x] 4. Targeted notifications (connector failure, pending approval, failed CI run, digest ready).

### Phase 9: Repo Hygiene, CI and a Proper Push
- [ ] 1. Pre-push audit: clean working tree, no personal/runtime data, complete history secret scan (`git log -p --all`). Check repo size and licenses.
- [ ] 2. Workflows: `.github/workflows/ci.yml`, `codeql.yml`, `dependabot.yml`, `SECURITY.md`, PR template.
- [ ] 3. Push branch `session-n` (`git push -u origin session-n`), open draft PR with `gh pr create`.
- [ ] 4. Watch & fix using NoX tooling (`nx gh watch`) up to 5 rounds.
- [ ] 5. Document merge and branch protection commands (USER-RUN, do not auto-merge).

### Final Verification & Documentation
- [ ] 1. Baseline vs final metrics recorded in `docs/SESSION_N_EVIDENCE.md`.
- [ ] 2. Gates A-E evidence logged.
- [ ] 3. Parity counts, connector test results, privacy proofs logged.
- [ ] 4. Final report formatted per Section 13.
