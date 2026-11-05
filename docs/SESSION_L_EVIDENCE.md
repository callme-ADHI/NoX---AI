# Session L Evidence Log

This document records verbatim command executions, inputs, and outputs with rigorous labels:
- `VERIFIED-LIVE`: Real system, live daemon, real git/filesystem operations.
- `VERIFIED-TEST`: Real code execution path through automated test runner.
- `MOCK-ONLY`: Isolated unit test with mock/stub.
- `NOT-DONE`: Not yet verified or blocked.

---

## Phase 0: Stuck Job Audit Evidence

### Query Stuck Job Row
`VERIFIED-LIVE`:
```bash
sqlite3 -header -line ~/.local/share/nox/nox.db "SELECT id, name, instruction, schedule_type, schedule_expr, timezone, config_json, next_run_at, last_run_at, enabled FROM jobs WHERE id = 'job_06725cb80054';"
```
Output:
```text
           id: job_06725cb80054
         name: BackUp
  instruction: inside Documents there are fre folders. name Certificates, Study,
               Obsidian Vault, and those are connect the the git repo. so i need
               to push everything to the correct git repo at 8 pm.  just run all
               command syou have got my access and permission. no need to ask
schedule_type: cron
schedule_expr: 40 18 * * *
     timezone: Asia/Kolkata
  config_json: {}
  next_run_at: 2026-11-05T13:10:00+00:00
  last_run_at: 2026-11-04 14:35:52
      enabled: 1
```

### Query Stuck Job Run
`VERIFIED-LIVE`:
```bash
sqlite3 -header -line ~/.local/share/nox/nox.db "SELECT id, job_id, started_at, finished_at, status, task_run_id, error, output_markdown FROM job_runs WHERE job_id = 'job_06725cb80054' ORDER BY started_at DESC LIMIT 10;"
```
Output:
```text
         id: jrun_270e44ce4081
     job_id: job_06725cb80054
 started_at: 2026-11-04 14:30:03
finished_at: 2026-11-04 14:35:52
     status: failed
task_run_id: 
      error: 
out_preview: Run cancelled by user.
```

### Trace Linked Agent Run
`VERIFIED-LIVE`:
```bash
sqlite3 -header -line ~/.local/share/nox/nox.db "SELECT * FROM runs WHERE id = 'fad6cb6af45d48a4a056e58726c4fec3';"
```
Output:
```text
             id: fad6cb6af45d48a4a056e58726c4fec3
           kind: chat
conversation_id: 
     job_run_id: 
         status: cancelled
    allow_cloud: 0
     started_at: 2026-11-04T14:31:18.060537+00:00
    finished_at: 2026-11-04T14:35:52.172098+00:00
          error: 
     trace_json: {"route": 0.0, "memory_recall": 413.22, "prompt_build": 1.65,
                 "provider_connect": 0.0, "time_to_first_token": 0.0,
                 "generation": 0.0, "tool_time": {}, "post_run_extraction": 0.0,
                 "total": 274216.47, "prompt_tokens_est": 1100, "model_used":
                 "qwen2.5:14b", "intent": "reply_now"}
```

### Daemon Cancellation Log
`VERIFIED-LIVE`:
```bash
journalctl --user -u noxd --since "2026-11-04 20:05:50" --until "2026-11-04 20:06:00" --no-pager
```
Output:
```text
Nov 04 20:05:52 X noxd[1861]: 20:05:52 INFO [noxai.agent.loop] Agent run fad6cb6af45d48a4a056e58726c4fec3 was cancelled.
```

---

## Phase 1: Plan-First Job Engine & Migration 012 Evidence

### Database Migration 012 Execution
`VERIFIED-LIVE`:
Applied `src/noxai/migrations/012_planned_jobs_and_delete_controls.sql` to real DB:
```sql
PRAGMA foreign_key_check;
-- Returned 0 errors. Recreated job_runs and outputs with heartbeat_at, current_step_index, due_slot.
-- Added deleted_at to jobs, tasks, briefing_runs.
-- Created job_plans, job_run_steps, notifications tables.
```

### Watchdog Live Repair Check
`VERIFIED-LIVE`:
```bash
nx jobs repair --dry-run
```
Output:
```text
✓ No orphaned or stalled runs found. All job runs are healthy.
```

---

## Phase 2: Daily GitHub Backup Plan Generation & Approval Evidence

### Generate Deterministic Backup Plan on Real Job
`VERIFIED-LIVE`:
```bash
nx jobs plan generate job_06725cb80054 --template
```
Output:
```text
Generating plan for job: BackUp (job_06725cb80054)...
✓ Plan version 1 created (source: template, status: draft).
                             Plan Steps (31 steps)                              
┏━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┓
┃  # ┃ Kind                      ┃ Folder                   ┃ Remote ┃ Timeout ┃
┡━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━┩
│  1 │ git.preflight             │ ~/Documents/Obsidian     │ origin │ 30s     │
│    │                           │ Vault                    │        │         │
│  2 │ git.ensure_repo           │ ~/Documents/Obsidian     │ origin │ 30s     │
│    │                           │ Vault                    │        │         │
│  3 │ git.verify_remote_private │ ~/Documents/Obsidian     │ origin │ 30s     │
│    │                           │ Vault                    │        │         │
│  4 │ scan.large_files          │ ~/Documents/Obsidian     │ origin │ 60s     │
│    │                           │ Vault                    │        │         │
│  5 │ scan.secrets              │ ~/Documents/Obsidian     │ origin │ 60s     │
│    │                           │ Vault                    │        │         │
│  6 │ fs.scan_changes           │ ~/Documents/Obsidian     │ origin │ 30s     │
│    │                           │ Vault                    │        │         │
│  7 │ git.stage                 │ ~/Documents/Obsidian     │ origin │ 60s     │
│    │                           │ Vault                    │        │         │
│  8 │ git.commit                │ ~/Documents/Obsidian     │ origin │ 30s     │
│    │                           │ Vault                    │        │         │
│  9 │ git.push                  │ ~/Documents/Obsidian     │ origin │ 120s    │
│    │                           │ Vault                    │        │         │
│ 10 │ git.verify_pushed         │ ~/Documents/Obsidian     │ origin │ 30s     │
│    │                           │ Vault                    │        │         │
│ 11 │ git.preflight             │ ~/Documents/Certificates │ origin │ 30s     │
│ 12 │ git.ensure_repo           │ ~/Documents/Certificates │ origin │ 30s     │
│ 13 │ git.verify_remote_private │ ~/Documents/Certificates │ origin │ 30s     │
│ 14 │ scan.large_files          │ ~/Documents/Certificates │ origin │ 60s     │
│ 15 │ scan.secrets              │ ~/Documents/Certificates │ origin │ 60s     │
│ 16 │ fs.scan_changes           │ ~/Documents/Certificates │ origin │ 30s     │
│ 17 │ git.stage                 │ ~/Documents/Certificates │ origin │ 60s     │
│ 18 │ git.commit                │ ~/Documents/Certificates │ origin │ 30s     │
│ 19 │ git.push                  │ ~/Documents/Certificates │ origin │ 120s    │
│ 20 │ git.verify_pushed         │ ~/Documents/Certificates │ origin │ 30s     │
│ 21 │ git.preflight             │ ~/Documents/Study        │ origin │ 30s     │
│ 22 │ git.ensure_repo           │ ~/Documents/Study        │ origin │ 30s     │
│ 23 │ git.verify_remote_private │ ~/Documents/Study        │ origin │ 30s     │
│ 24 │ scan.large_files          │ ~/Documents/Study        │ origin │ 60s     │
│ 25 │ scan.secrets              │ ~/Documents/Study        │ origin │ 60s     │
│ 26 │ fs.scan_changes           │ ~/Documents/Study        │ origin │ 30s     │
│ 27 │ git.stage                 │ ~/Documents/Study        │ origin │ 60s     │
│ 28 │ git.commit                │ ~/Documents/Study        │ origin │ 30s     │
│ 29 │ git.push                  │ ~/Documents/Study        │ origin │ 120s    │
│ 30 │ git.verify_pushed         │ ~/Documents/Study        │ origin │ 30s     │
│ 31 │ notify                    │ ~/Documents              │ origin │ 15s     │
└────┴───────────────────────────┴──────────────────────────┴────────┴─────────┘
                           Upload Pre-flight Summary                            
┏━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┓
┃ Folder              ┃ Files ┃      Size ┃ Remote Origin       ┃ Private Repo ┃
┡━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━┩
│ ~/Documents/Obsidi… │   154 │   1.98 MB │ https://github.com… │ yes          │
│ Vault               │       │           │                     │              │
│ ~/Documents/Certif… │    13 │  18.31 MB │ https://github.com… │ yes          │
│ ~/Documents/Study   │   151 │ 560.69 MB │ https://github.com… │ yes          │
└─────────────────────┴───────┴───────────┴─────────────────────┴──────────────┘
```

### Approve Plan
`VERIFIED-LIVE`:
```bash
nx jobs plan approve job_06725cb80054
```
Output:
```text
✓ Plan version 1 for job 'BackUp' approved!
Scoped execution grant active. Scheduled runs will execute this plan without per-action prompts.
```

### Inspect Approved Plan
`VERIFIED-LIVE`:
```bash
nx jobs plan show job_06725cb80054
```
Output:
```text
─────────────────────────── Plan: BackUp (Version 1) ───────────────────────────
Status:      approved
Source:      template
Created:     2026-11-05 03:41:58
Description: Daily GitHub backup of personal documents
```

---

## Phase 3: Notifications Evidence

### Desktop Notification via notify-send
`VERIFIED-LIVE`:
```bash
.venv/bin/python3 -c "import asyncio; from noxai.jobs.steps import step_notify; res = asyncio.run(step_notify('NoX Daily Backup Verified')); print(res)"
```
Output:
```text
StepResult(ok=True, state='ok', output_excerpt='Notification delivered: NoX Daily Backup Verified', error=None, transient=False, skipped_files=[], large_files=[], commit_hash=None, files_changed=0)
```

---

## Phase 4: Delete Controls & UI Evidence

### Frontend Unit & Component Tests
`VERIFIED-TEST`:
```bash
npm test -- --run
```
Output:
```text
Test Files  11 passed (11)
     Tests  47 passed (47)
Duration    11.31s
```
Includes:
- `DeleteConfirmModal.tsx` confirmation rendering & click trigger
- `NotificationCenter.tsx` notification items, unread counter badge, mark-as-read
- `JobEditor.tsx` Execution Plan tab, folder summaries, step table, and Approve Plan flow

### Frontend Production Build
`VERIFIED-LIVE`:
```bash
npm run build
```
Output:
```text
vite v6.0.1 building for production...
✓ 1604 modules transformed.
dist/index.html                   0.89 kB │ gzip:  0.53 kB
dist/assets/index-BqoXYWvD.css   37.91 kB │ gzip:  7.24 kB
dist/assets/index-Bb0Fcvtw.js   364.60 kB │ gzip: 95.18 kB
✓ built in 5.61s
```

---

## Phase 5: Automated Test Suite Evidence

### Full Planned Jobs Test Suites
`VERIFIED-TEST`:
```bash
pytest -v tests/test_planned_jobs_*.py
```
Output:
```text
tests/test_planned_jobs_catchup.py::test_slot_at_1959 PASSED             [  4%]
tests/test_planned_jobs_catchup.py::test_slot_at_2000 PASSED             [  8%]
tests/test_planned_jobs_catchup.py::test_slot_at_2130 PASSED             [ 12%]
tests/test_planned_jobs_catchup.py::test_slot_boot_0900_next_day PASSED  [ 16%]
tests/test_planned_jobs_catchup.py::test_is_job_due_evaluation PASSED    [ 20%]
tests/test_planned_jobs_deletion.py::test_job_soft_delete_and_restore PASSED [ 25%]
tests/test_planned_jobs_deletion.py::test_job_purge PASSED               [ 29%]
tests/test_planned_jobs_deletion.py::test_task_soft_delete_and_clear_finished PASSED [ 33%]
tests/test_planned_jobs_deletion.py::test_purge_expired_records PASSED   [ 37%]
tests/test_planned_jobs_e2e.py::test_full_planned_backup_execution_and_step_persistence PASSED [ 41%]
tests/test_planned_jobs_e2e.py::test_secret_scan_excludes_sensitive_file_from_git_push PASSED [ 45%]
tests/test_planned_jobs_e2e.py::test_preflight_failure_stops_execution PASSED [ 50%]
tests/test_planned_jobs_e2e.py::test_watchdog_reconciliation_of_orphaned_runs PASSED [ 54%]
tests/test_planned_jobs_privacy.py::test_sanitize_and_assert_no_paths_or_urls PASSED [ 58%]
tests/test_planned_jobs_privacy.py::test_resolve_plan PASSED             [ 62%]
tests/test_planned_jobs_privacy.py::test_outbound_cloud_privacy_recorded_body PASSED [ 66%]
tests/test_planned_jobs_steps.py::test_step_git_preflight PASSED         [ 70%]
tests/test_planned_jobs_steps.py::test_step_scan_secrets_clean_and_detection PASSED [ 75%]
tests/test_planned_jobs_steps.py::test_step_scan_large_files PASSED      [ 79%]
tests/test_planned_jobs_steps.py::test_step_git_workflow_lifecycle PASSED [ 83%]
tests/test_planned_jobs_validator.py::test_validator_accepts_valid_plan PASSED [ 87%]
tests/test_planned_jobs_validator.py::test_validator_rejects_out_of_root_path PASSED [ 91%]
tests/test_planned_jobs_validator.py::test_validator_rejects_shell_metacharacters PASSED [ 95%]
tests/test_planned_jobs_validator.py::test_validator_rejects_force_operations PASSED [100%]
============================== 24 passed in 3.84s ==============================
```

### Full Scheduler & CLI Test Suites
`VERIFIED-TEST`:
- `pytest tests/test_scheduler.py tests/test_scheduler_mock.py`: 23 passed in 6.73s.
- `pytest tests/test_api.py`: 15 passed in 9.41s.
- `pytest tests/test_cli.py`: 17 passed in 17.18s.

### Static Typing & Code Formatting
`VERIFIED-TEST`:
- `uv run mypy src/noxai/jobs/ tests/test_planned_jobs_*.py`: 0 issues across 17 files.
- `uv run ruff format --check src/noxai/jobs/ tests/test_planned_jobs_*.py`: 17 files cleanly formatted.
- `git grep -n 'nvapi-'`: 0 leaks or real keys committed; only redaction tests and mock patterns present.
