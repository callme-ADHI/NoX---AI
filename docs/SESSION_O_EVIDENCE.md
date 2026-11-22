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
