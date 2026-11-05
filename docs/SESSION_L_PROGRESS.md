# Session L Progress & Checklist

## Rules & Protocol
- [x] Rule 1: Prompt saved to `docs/prompts/session-L.md` and checklist created at `docs/SESSION_L_PROGRESS.md`
- [ ] Rule 2: Zero secrets/transcripts exposure. Mask all keys. Final check: `git grep -n "nvapi-"`
- [ ] Rule 3: Do not push to remote. Local commits only (one per phase).
- [ ] Rule 4: Evidence logged with tags (VERIFIED-LIVE, VERIFIED-TEST, MOCK-ONLY, NOT-DONE) in `docs/SESSION_L_EVIDENCE.md`
- [ ] Rule 5: Real DB safety: no hand edits. Migrations tested on copy and fresh DB. Temp instance for destructive proofs.
- [ ] Rule 6: No existing tests weakened; log decisions in `docs/DECISIONS.md`.
- [ ] Rule 7: Privacy: Cloud model receives only sanitized instructions with placeholders; test real outbound payload.
- [ ] Rule 8: No sudo, no silent packages, Asia/Kolkata timezone awareness, all linters and tests green.

---

## Phase 0: Prove Why Run Is Stuck (Read-only Audit)
- [x] Step 0.1: Run read-only queries on real DB for `job_06725cb80054` and recent `job_runs`
- [x] Step 0.2: Query tasks/approvals created at 2026-11-04 14:30 UTC / 20:00 IST
- [x] Step 0.3: Inspect `journalctl --user -u noxd` around that time
- [x] Step 0.4: Code trace with file:line on existing execution, tool calls, approval gate, status, and chat reporting
- [x] Step 0.5: Formulate root cause with evidence in `docs/audits/job-stuck-audit.md` (Do not fix real DB row by hand)

---

## Phase 1: Plan-First Job Engine
- [ ] Step 1.1: DB Migration for `job_plans` and `job_run_steps` and extended `job_runs` / `jobs` columns (test on copy & fresh DB)
- [ ] Step 1.2: Privacy sanitizer (placeholders `{{folder_1}}`, `{{remote_1}}`, etc.) + tests
- [ ] Step 1.3: Cloud planner via KeyPool + Deterministic Template Planner fallback
- [ ] Step 1.4: Plan validator (whitelisted step kinds, typed params, allowed roots, no shell strings, max steps, retry/timeout)
- [ ] Step 1.5: Plan versioning, storage, and scoped approval grant
- [ ] Step 1.6: Step kinds registry (argv subprocesses, strict timeouts, `GIT_TERMINAL_PROMPT=0`, BatchMode, no stored creds)
- [ ] Step 1.7: Step-by-step executor under TaskSupervisor with state machine and persistence in `job_run_steps`
- [ ] Step 1.8: Watchdog (5s heartbeat, 3m stall detection, hard timeout, restart resumption)
- [ ] Step 1.9: CLI commands: `nx jobs plan`, `nx jobs plan show`, `nx jobs plan approve`, `nx jobs repair`
- [ ] Step 1.10: Commit Phase 1 locally

---

## Phase 2: Daily GitHub Backup
- [ ] Step 2.1: Default backup folders config (`jobs.backup.folders` in `~/.config/nox/config.toml`)
- [ ] Step 2.2: Backup template plan per folder (`git.preflight` -> `git.ensure_repo` -> `git.verify_remote_private` -> `scan.large_files` -> `scan.secrets` -> `fs.scan_changes` -> `git.stage` -> `git.commit` -> `git.push` -> `git.verify_pushed` -> `notify`)
- [ ] Step 2.3: Verification of private remote (unauth GET 200 = public blocked, 404 + ls-remote = private)
- [ ] Step 2.4: Secret scanner (exclude to `.git/info/exclude`, report skipped names)
- [ ] Step 2.5: Large file scanner (>50MB skip & report; git-lfs if present)
- [ ] Step 2.6: Obsidian Vault excludes (`.obsidian/workspace*.json`, `.trash/`) via `.git/info/exclude`
- [ ] Step 2.7: Deterministic commit messages: `NoX backup <folder-alias> <YYYY-MM-DD HH:MM> (<n> files)`
- [ ] Step 2.8: Edge cases (.git/index.lock, detached HEAD, empty initial repo)
- [ ] Step 2.9: Generalized slot catch-up scheduling rule (due_slot 20:00 Asia/Kolkata)
- [ ] Step 2.10: Commit Phase 2 locally

---

## Phase 3: Notifications
- [ ] Step 3.1: Desktop notifications via `notify-send` (run succeeded, partial, failed, needs_setup, stalled, catch-up, blocked)
- [ ] Step 3.2: Notification deduplication / rate-limiting (no spam, one per outcome)
- [ ] Step 3.3: In-app notification center table (`notifications` table or API endpoint)
- [ ] Step 3.4: Commit Phase 3 locally

---

## Phase 4: Delete Controls (Jobs, Tasks, Briefings)
- [ ] Step 4.1: Soft delete schema & logic (`deleted_at`, 24h purge, `nx purge`)
- [ ] Step 4.2: Cooperative cancellation of running jobs/tasks before deletion
- [ ] Step 4.3: Cascading delete rules (jobs -> schedule, plans, runs; briefings -> runs, items, staging, vault notes handling)
- [ ] Step 4.4: API endpoints: `DELETE /api/jobs/{id}`, `DELETE /api/tasks/{id}`, `DELETE /api/briefings/{id}`, `POST /api/{kind}/{id}/restore`
- [ ] Step 4.5: CLI commands: `nx jobs delete`, `nx tasks delete`, `nx tasks clear-finished`, `nx briefing delete`
- [ ] Step 4.6: UI: Trash icon, confirmation modal, 10s undo toast
- [ ] Step 4.7: Chat intent confirmation safeguard
- [ ] Step 4.8: Commit Phase 4 locally

---

## Phase 5: Tests and Live Proofs
- [ ] Step 5.1: Unit & property tests (plan validator, sanitiser, slot catch-up, secret scanner, large-file, diverged, soft delete)
- [ ] Step 5.2: Outbound request body privacy test
- [ ] Step 5.3: LIVE git proof in temp instance (bare repo, secret skip, large-file skip, Obsidian exclude, diverged check)
- [ ] Step 5.4: LIVE catch-up proof across 20:00 slot
- [ ] Step 5.5: LIVE watchdog/resume proof (SIGKILL mid-step, resume from first unfinished step)
- [ ] Step 5.6: LIVE notification proof (`notify-send` / dbus)
- [ ] Step 5.7: LIVE delete proof (UI / Playwright & CLI)
- [ ] Step 5.8: Real-instance read-only check (`nx jobs repair --dry-run` on real DB copy)
- [ ] Step 5.9: Full suite verification (`pytest`, `mypy`, `ruff`, `npm test`, `npm run build`, `git grep -n "nvapi-"`)
- [ ] Step 5.10: Commit Phase 5 locally

---

## Final Report
- [ ] Compile comprehensive final report with sections A through F
