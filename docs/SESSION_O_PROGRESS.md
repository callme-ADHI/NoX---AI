# NoX Session O Progress & Defect Checklist

## Status Legend
- `[ ]` Not Started / In Progress
- `[x]` Completed, Verified & Proven

---

## 1. Defect Tracker

### Security & Credential Hygiene (Session N)
- [ ] **N-1**: Remove "git credential fill" / token extraction code paths, delete stored `github:token` by name via `nx secrets delete github:token`, add regression & repo-hygiene tests.
- [ ] **N-2**: Introduce honest connector/channel states (`NOT_CONFIGURED`, `CONFIGURED_UNTESTED`, `HEALTHY`, `DEGRADED`, `AUTH_REQUIRED`, `FAILED`, `DISABLED`) in `nx health`, `nx doctor`, `nx selftest`, and UI.
- [ ] **N-3**: Complete audit log wiring for every guarded action (connector read/sync, write, approval, denial, cloud egress, delete, panic, skill install) with test enumerating call sites.
- [ ] **N-4**: Fix SecretStore terminology (genuine keyring or 0600 file text; no false "encrypted" claims).
- [ ] **N-5**: Multi-language static skill scanner (Python, shell, JS, Markdown with embedded commands) with malicious test fixtures.
- [ ] **N-6**: Rebuild `docs/OPENJARVIS_PARITY.md` from a fresh pinned shallow clone in `/tmp` with cited upstream paths and proof.
- [ ] **N-7**: Add UI component tests for all 7 new pages and Playwright flows against real temp-instance daemon.
- [ ] **N-8**: GitHub Actions CI workflows and local CI-equivalent (`scripts/ci-local.sh`).

### Core Runtime (Sessions J, K, M)
- [ ] **C-1**: Single authoritative scheduler path per job type (no duplicate briefing/backup runs).
- [ ] **C-2**: Live automation proof in temp instance: 04:00 & 18:00 with catch-up, UI closed, 0 items = FAILED, no false reconciler, resume under same run id.
- [ ] **C-3**: Resume after SIGKILL (real subprocess daemon) live proof.
- [ ] **C-4**: KeyPool allocator genuinely wired in provider/engine; token bucket refills; live `nx keys status`; 429/401/404/410 handling with mock server.
- [ ] **C-5**: Cloud default ON with persisted toggle; prove local_only content never egresses via recording transport.
- [ ] **C-6**: Conversation memory evaluation with REAL models (cloud and local) across 8 scenarios without artificial token caps.
- [ ] **C-7**: Migration 009 convergence and test both upgrade paths on a copy of the real DB.
- [ ] **C-8**: Retrieval taint: notes (Vault, Study) chunks strictly `local_only`.

### Content (Session J)
- [ ] **T-1**: Live-proven briefing content: news, funded internships with <=25-word verbatim evidence snippets from fetched pages, hackathons as individual events, CTFs <= 3, strict link checks, remove mocked search from production code (`tools/builtin/web.py`).
- [ ] **T-2**: One "Run briefing" button surviving refresh, nav, and closing; Playwright proof against real run.

### Jobs & Daily GitHub Backup (Session L)
- [ ] **L-1**: Stuck "BackUp" job run audit (`docs/audits/job-stuck-audit.md`) and repair via watchdog/repair, not manual SQL.
- [ ] **L-2**: Plan-first job engine, daily GitHub backup of Certificates/Study/Vault after 20:00 with catch-up, private repo check, secrets/large-file skips, fast-forward only, delete controls; live proofs in temp HOME with bare repos.

### Desktop App & Packaging (Session K)
- [ ] **D-1**: Fix tray endpoints (`/api/briefing/run`, cloud toggle endpoint, error handling).
- [ ] **D-2**: "Open Notes Vault" path sourced dynamically from daemon config.
- [ ] **D-3**: Tray dynamic labels (Next briefing, Cloud status).
- [ ] **D-4**: Secure token flow (no token in argv/curl, no cross-origin storage, exchange token).
- [ ] **D-5**: Navigation allow-list, external link validation, path allow-list, no remote origin IPC.
- [ ] **D-6**: Window class / app-id consistency across desktop entry, docs, and Hyprland rules.
- [ ] **D-7**: Cargo tests and real Hyprland smoke test (hyprctl, hide-on-close, tray actions, offline recovery).
- [ ] **P-1**: PKGBUILD fixes (dependencies, checksums, pkgver, license, systemd unit without sh -c, namcap, verify-install.sh).
- [ ] **P-2**: `nx setup` fixes (no edit without `--apply`, backup, diff, no silent linger, enable unit, 0600 secrets, idempotent, temp HOME tests).

### Session N Features Verification
- [ ] **F-1**: Mail/Calendar protocol-level fakes + USER-RUN live checks.
- [ ] **F-2**: Telegram, Slack, Discord, Feeds protocol-level fakes + USER-RUN live steps.
- [ ] **F-3**: GitHub intelligence gh-first + read-only live checks against `callme-ADHI/NoX---AI`.
- [ ] **F-4**: Agents framework, presets, learning loop, telemetry.

---

## 2. Phase Checklist

- [x] **Phase 1: Baseline and the Verification Harness**
  - [x] Record baseline metrics (pytest, mypy, ruff, vitest, cargo test, nx doctor/health/selftest).
  - [x] Back up real DB (`nx backup create`).
  - [x] Read-only real DB dump of migrations, schedules, runs, tasks, audit counts.
  - [x] Build `nx verify` harness (`scripts/verify_all.py`) with temp-instance launcher, bare repo helper, mock HTTP server, recorder.
  - [x] Run initial harness to produce `docs/SESSION_O_BEFORE.txt`.
  - [x] Populate `docs/CLAIMS_LEDGER.md`.
- [ ] **Phase 2: Security and Credential Hygiene (N-1 to N-5)**
- [ ] **Phase 3: Core Runtime (C-1 to C-8)**
- [ ] **Phase 4: Briefing Content (T-1, T-2)**
- [ ] **Phase 5: Jobs and Daily GitHub Backup (L-1, L-2)**
- [ ] **Phase 6: Connectors, Channels, Agents (N-2, N-7, F-1, F-2, F-4)**
- [ ] **Phase 7: GitHub Intelligence (N-1, F-3)**
- [ ] **Phase 8: Desktop App and Packaging (D-1 to D-7, P-1, P-2)**
- [ ] **Phase 9: OpenJarvis Parity, Rebuilt Honestly (N-6)**
- [ ] **Phase 10: Repo Hygiene, CI and the PR (N-8)**
- [ ] **Final Verification & Report (Sections 12, 14)**
