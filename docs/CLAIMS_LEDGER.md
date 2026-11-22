# NoX Claims Ledger (Sessions H to N)

This ledger tracks every substantive capability and architectural claim made across Sessions H through N, detailing its source session, the defect or integrity ID, the verification method, its status, and the evidence reference.

## Status Legend
- **VERIFIED-LIVE**: Proven on real running daemon, OS, hardware, or network.
- **VERIFIED-TEST**: Proven via protocol-level fake server or deterministic recorded test.
- **MOCK-ONLY**: Unproven in production; relies on fake DB, mocked clocks, or test-only stubs.
- **USER-RUN**: Requires user credentials, active browser login, or root access.
- **NOT-DONE**: Acknowledged incomplete, unproven, or overclaimed.

---

## Baseline Initial Run (docs/SESSION_O_BEFORE.txt)
Recorded: 2026-11-22 06:50:56 IST. All 33 defect items began as FAIL / NOT-DONE.

---

## Claims & Verification Matrix

| Claim ID | Source Session | Defect ID | Claim Summary | Verification Method | Initial Baseline | Final Status | Evidence Reference |
|---|---|---|---|---|---|---|---|
| **CLM-01** | Session N | N-1 | GitHub token storage & auth | Regression test for no token extraction; `nx secrets delete` | FAIL (NOT-DONE) | FIXED-AND-PROVEN | Phase 2 (`nx verify N-1`) |
| **CLM-02** | Session N | N-2 | Connector health reporting | State machine (`NOT_CONFIGURED`, `CONFIGURED_UNTESTED`, `HEALTHY`, etc.) | FAIL (NOT-DONE) | FIXED-AND-PROVEN | Phase 2 (`nx verify N-2`) |
| **CLM-03** | Session N | N-3 | Audit log completeness | Audit events logged on all guarded calls; test enumerates call sites | FAIL (NOT-DONE) | FIXED-AND-PROVEN | Phase 2 (`nx verify N-3`) |
| **CLM-04** | Session N | N-4 | SecretStore encryption claim | Text & code audit (0600 file vs genuine keyring) | FAIL (NOT-DONE) | FIXED-AND-PROVEN | Phase 2 (`nx verify N-4`) |
| **CLM-05** | Session N | N-5 | Skill security scanner | Multi-language static scanner with malicious test fixtures | FAIL (NOT-DONE) | FIXED-AND-PROVEN | Phase 2 (`nx verify N-5`) |
| **CLM-06** | Session N | N-6 | OpenJarvis parity counts | Rebuilt from pinned shallow clone in `/tmp` with source citations | FAIL (NOT-DONE) | PENDING | Phase 9 |
| **CLM-07** | Session N | N-7 | 7 New UI Pages test coverage | Vitest component tests + Playwright flows | FAIL (NOT-DONE) | PENDING | Phase 6 |
| **CLM-08** | Session N | N-8 | GitHub Actions CI green | Push workflows if scope granted, local CI script `scripts/ci-local.sh` | FAIL (NOT-DONE) | PENDING | Phase 10 |
| **CLM-09** | Session M | C-1 | Single scheduler authority | Temp instance proof: 04:00 & 18:00 fire once | FAIL (NOT-DONE) | FIXED-AND-PROVEN | Phase 3 (`nx verify C-1`) |
| **CLM-10** | Session J/M| C-2 | Unattended briefing automation | Temp instance proof: UI closed, catch-up, 0-item = FAILED | FAIL (NOT-DONE) | FIXED-AND-PROVEN | Phase 3 (`nx verify C-2`) |
| **CLM-11** | Session M | C-3 | Subprocess SIGKILL recovery | Real daemon SIGKILL mid-task and restart recovery | FAIL (NOT-DONE) | FIXED-AND-PROVEN | Phase 3 (`nx verify C-3`) |
| **CLM-12** | Session M | C-4 | KeyPool allocation in provider | Real lease cycle, token bucket refill, mock 429/401/500 server | FAIL (NOT-DONE) | FIXED-AND-PROVEN | Phase 3 (`nx verify C-4`) |
| **CLM-13** | Session M | C-5 | Cloud default ON + taint isolation | Fresh instance proof with recording transport (notes/mail/chat) | FAIL (NOT-DONE) | FIXED-AND-PROVEN | Phase 3 (`nx verify C-5`) |
| **CLM-14** | Session M | C-6 | Conversation memory persistence | Real model 8-scenario evaluation transcripts without token cap | FAIL (NOT-DONE) | FIXED-AND-PROVEN | Phase 3 (`nx verify C-6`) |
| **CLM-15** | Session M | C-7 | Migration 009 convergence | DB copy test of both pre- and post-edit upgrade paths | FAIL (NOT-DONE) | FIXED-AND-PROVEN | Phase 3 (`nx verify C-7`) |
| **CLM-16** | Session M | C-8 | Retrieval taint on notes | Chunks strictly `local_only` in DB & retrieval queries | FAIL (NOT-DONE) | FIXED-AND-PROVEN | Phase 3 (`nx verify C-8`) |
| **CLM-17** | Session J | T-1 | Briefing content quality & links | Live network run, <=25-word quotes, CTFs <=3, HTTP link validator | FAIL (NOT-DONE) | PENDING | Phase 4 |
| **CLM-18** | Session J | T-2 | Single Run Briefing button | Playwright test surviving refresh, nav, and tab closing | FAIL (NOT-DONE) | PENDING | Phase 4 |
| **CLM-19** | Session L | L-1 | Stuck BackUp job reconciliation | Audit document `docs/audits/job-stuck-audit.md` + watchdog fix | FAIL (NOT-DONE) | PENDING | Phase 5 |
| **CLM-20** | Session L | L-2 | Daily GitHub backup & plan-first | Live bare repos, private check, ff-only, delete/undo controls | FAIL (NOT-DONE) | PENDING | Phase 5 |
| **CLM-21** | Session K | D-1 | Desktop tray endpoints | Correct endpoints in Rust (`/api/briefing/run`, cloud toggle) | FAIL (NOT-DONE) | PENDING | Phase 8 |
| **CLM-22** | Session K | D-2 | Notes Vault path resolution | Sourced dynamically from daemon config | FAIL (NOT-DONE) | PENDING | Phase 8 |
| **CLM-23** | Session K | D-3 | Dynamic tray status labels | Live polling of next briefing slot & cloud state | FAIL (NOT-DONE) | PENDING | Phase 8 |
| **CLM-24** | Session K | D-4 | Desktop token flow security | Secure handshake, no token in argv or visible URL | FAIL (NOT-DONE) | PENDING | Phase 8 |
| **CLM-25** | Session K | D-5 | Desktop IPC & navigation safety | Origin allowlist, path allowlist, no window.eval | FAIL (NOT-DONE) | PENDING | Phase 8 |
| **CLM-26** | Session K | D-6 | Desktop window class & autostart | Consistent app-id across .desktop, Hyprland rules, and docs | FAIL (NOT-DONE) | PENDING | Phase 8 |
| **CLM-27** | Session K | D-7 | Desktop test suite & smoke test | Extended cargo unit tests, Hyprland smoke test via hyprctl | FAIL (NOT-DONE) | PENDING | Phase 8 |
| **CLM-28** | Session K | P-1 | Arch PKGBUILD integrity | Dependencies, real checksums, namcap audit, verify-install.sh | FAIL (NOT-DONE) | PENDING | Phase 8 |
| **CLM-29** | Session K | P-2 | `nx setup` non-destructive safety | Backup, diff, no silent linger, 0600 permissions, idempotent | FAIL (NOT-DONE) | PENDING | Phase 8 |
| **CLM-30** | Session N | F-1 | Mail & Calendar connectors | Protocol fakes + USER-RUN instructions | FAIL (NOT-DONE) | PENDING | Phase 6 |
| **CLM-31** | Session N | F-2 | Channels (Telegram, Slack, Discord) | Protocol fakes + USER-RUN instructions | FAIL (NOT-DONE) | PENDING | Phase 6 |
| **CLM-32** | Session N | F-3 | GitHub Intelligence gh-first | Live read-only via `gh`, failure triage, workflow linter | FAIL (NOT-DONE) | PENDING | Phase 7 |
| **CLM-33** | Session N | F-4 | Agents framework & telemetry | Runner budgets, presets, optimizer auto-rollback | FAIL (NOT-DONE) | PENDING | Phase 6 |
