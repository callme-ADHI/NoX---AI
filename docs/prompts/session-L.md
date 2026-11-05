# NOX SESSION L: PLANNED JOBS (PLAN FIRST, RUN STEP BY STEP), DAILY GITHUB BACKUP, NOTIFICATIONS, DELETE CONTROLS

You are a senior staff engineer in the NoX repo (package noxai, CLI nx, FastAPI daemon noxd on 127.0.0.1:7777, SQLite WAL at ~/.local/share/nox/nox.db, React+TS UI in ui/, Arch Linux + Hyprland, systemd user service, desktop app in desktop/). Sessions H-K are binding where this prompt does not override them. This session adds ONE feature end to end and proves it on the real system.

## 1. WHAT THE USER WANTS (his words, condensed)

 1. NoX pushes these folders to GitHub every day after 20:00 Asia/Kolkata: ~/Documents/Certificates, ~/Documents/Study, ~/Documents/"Obsidian Vault". If the PC was off after 20:00, the FIRST thing NoX does at the next start is the push.
 2. His existing job "BackUp" (job_06725cb80054) started at 20:00 on 2026-11-04 and stayed "running" with no finish and last_run_at null. NoX told him it "is running right now". That is a bug: a run that never finishes must never look healthy.
 3. When a job is created, NoX must first use the cloud API key (KeyPool) to analyse the job and produce an ordered list of steps (a PLAN), right after creation or before the first run. When the job executes, it follows the plan ONE STEP AT A TIME.
 4. Any error sends a desktop notification. Completion sends a desktop notification.
 5. There must be DELETE buttons for jobs, tasks and briefings (UI + CLI + API).

## 2. RULES (binding)

 1. FIRST ACTION: save this prompt verbatim to docs/prompts/session-L.md and create docs/SESSION_L_PROGRESS.md (checklist). After any context loss re-read only those two files and git log.
 2. SECRETS AND TRANSCRIPTS: never read anything under ~/.gemini or any transcript/log/history store of your own tooling; never open ~/.config/nox/secrets.env contents; never run unfiltered env/printenv; never read ~/.ssh private keys; never put a credential in a command, file, log, test or report. At the end paste "git grep -n 'nvapi-'".
 3. DO NOT PUSH the NoX repo during this session (commit locally only, one commit per phase). The user pushes himself.
 4. EVIDENCE: every claim needs a pasted command + output in docs/SESSION_L_EVIDENCE.md. Labels: VERIFIED-LIVE (real system/daemon/real git), VERIFIED-TEST (real code path), MOCK-ONLY, NOT-DONE. FakeProvider, fake DB, fake clock-only or test-mode shortcuts are MOCK-ONLY and do not count for: scheduling, catch-up, git operations, notifications, delete behaviour. No test-mode branches in production code.
 5. REAL DB SAFETY: never hand-edit the real DB (no ad-hoc UPDATE/DELETE). Schema changes only via a new migration (011 or next free number; applied migrations are immutable); the migration runner backs up first; test every migration on a COPY of the real DB (cp to /tmp) and a fresh DB. Destructive or time-based proofs run in a TEMP INSTANCE (second daemon, own NOX_DATA_DIR, NOX_CONFIG_DIR, NOX_PORT, a fake HOME for git/ssh config).
 6. Existing tests are not weakened or reshaped to pass; any change to an existing test needs a written reason in docs/DECISIONS.md.
 7. PRIVACY: the cloud model must never receive file names, file contents, folder listings, remote URLs or commit contents. It receives only the user's job instruction with every path and URL replaced by placeholders ({{folder_1}}, {{remote_1}}). Taint/egress rules are not loosened. Add a test that records the real outbound request body and asserts no path or URL is present.
 8. No sudo. No silent system package installs. Decide ambiguities yourself, log in docs/DECISIONS.md. Timezone-aware (zoneinfo Asia/Kolkata) everywhere. Keep ruff, mypy, pytest, vitest, playwright, cargo test green and paste final summary lines.

## 3. PHASE 0: PROVE WHY THE RUN IS STUCK (read-only, with evidence)

Write docs/audits/job-stuck-audit.md with command output, not guesses:
 1. Read-only SQL on the real DB: the jobs row for job_06725cb80054 (prompt, schedule_expr, next_run_at, last_run_at, catchup, enabled), its last 10 job_runs (status, started_at, finished_at, error), any tasks/approvals created at 2026-11-04 14:30 UTC (pending approvals, tasks in waiting_approval), and the daemon log lines around that time (journalctl --user -u noxd).
 2. Code trace with file:line: how a job is executed today (agent loop with tools?), which tool calls it makes (run_command, file writes), where the human-approval gate parks the work, what job_runs.status becomes while parked, why no finish/heartbeat/timeout exists, and why the chat assistant reported "running" as healthy.
 3. State the root cause with evidence (likely: the agent called a write/exec tool that needs approval and the run stayed "running" forever; confirm or refute).
 4. Do not fix the real stuck row by SQL. The new watchdog/repair (Phase 2) must reconcile it on the next daemon start; show that as proof.

## 4. PHASE 1: PLAN-FIRST JOB ENGINE

### 4.1 Plan creation (uses the cloud API key through the KeyPool)
 - When a job is created or its instruction edited, a PLANNER task runs in the daemon (TaskSupervisor, never in a request). It sends the cloud model ONLY the sanitized instruction (placeholders for paths/URLs, see rule 7) and a schema of allowed step kinds, and gets back a JSON plan. If cloud is off or unavailable it falls back to a deterministic template planner (the backup template below) and says so in the plan metadata.
 - A static VALIDATOR checks every plan: only whitelisted step kinds; parameters typed; every path inside an allow-listed root (config [jobs.allowed_roots], default ~/Documents); no shell strings (argv lists only); no delete/force/rewrite operations; at most N steps; per-step timeout and retry policy present. Invalid plans are rejected with reasons; the planner gets one repair attempt, then the template planner is used.
 - Plans are stored as versioned rows (job_plans: job_id, version, steps_json, source[cloud|template], status[draft|approved|superseded], created_at). The UI shows a PLAN PREVIEW with the real paths filled in locally, the step list, and for the backup template a first-run summary: number of files and total size per folder, the remote, and "private repo: yes/no". The user approves once; the approval is a scoped grant covering exactly that plan's steps and roots (this replaces the per-action human-approval prompt for those steps, which stays in force for everything else). Editing the instruction creates a new draft plan that needs approval again.
 - CLI: nx jobs plan <job_id> (generate/regenerate), nx jobs plan show <job_id>, nx jobs plan approve <job_id>. Existing job_06725cb80054 gets a plan through this path (no SQL).

### 4.2 Step kinds (registry; each is a typed Python action using argv subprocesses, never a shell)
 fs.scan_changes, scan.secrets, scan.large_files, git.preflight (git installed, auth works non-interactively), git.ensure_repo, git.verify_remote_private, git.stage, git.commit, git.push, git.verify_pushed, notify. Add others only if needed and whitelist them.
 Environment for all git calls: GIT_TERMINAL_PROMPT=0, no askpass, GIT_SSH_COMMAND with "-o BatchMode=yes -o ConnectTimeout=15", a hard timeout per step. NoX never stores or reads git credentials or SSH keys; it relies on the user's existing git/ssh/credential-helper setup. Remember the daemon runs as a systemd USER service: SSH_AUTH_SOCK may be missing, so git.preflight must test auth with "git ls-remote" and, on failure, produce a clear notification with remediation (for example "ssh-agent not reachable from the daemon" or "credential helper missing"), never hang.

### 4.3 Executor
 - Runs the approved plan step by step, in order, under the TaskSupervisor. Each step persists to job_run_steps (run_id, step_index, kind, params_redacted, state[pending|running|ok|failed|skipped], attempts, started_at, finished_at, output_excerpt_redacted, error). A step starts only after the previous one is ok (or skipped by rule).
 - Run state machine: queued, running (with current step), succeeded, partial (some folders ok, some failed), failed, needs_setup (missing remote/auth/user input), waiting_approval, stalled, cancelled. job_runs.last_run_at updates only when a run finishes; the UI and chat show "running since X, step 3/9: git.push" and never just "running".
 - Per-folder isolation: a failure in one folder does not stop the others; the run ends partial and the notification lists which folders succeeded/failed.
 - Retries: network/transient errors retry with backoff and jitter (max 5 over 6 hours when offline: wait for network at boot, retry every 5 minutes); permanent errors (auth, diverged remote, public repo, secret found) fail fast with a specific reason.
 - Watchdog: a heartbeat every 5 s; a run with no heartbeat for 3 minutes becomes stalled (and is resumed if resumable); a hard run timeout (config, default 45 min) fails it. A run can never stay "running" forever. Daemon restart resumes the same run from the first unfinished step; finished steps are not repeated (prove with a SIGKILL test).
 - "nx jobs repair" (supervisor-checked, logged) reconciles orphaned runs; the daemon calls it automatically at startup. This is what fixes the existing stuck run.

## 5. PHASE 2: THE DAILY GITHUB BACKUP (template plan, per folder)

Folders: ~/Documents/Certificates, ~/Documents/Study, ~/Documents/"Obsidian Vault" (config [jobs.backup.folders], editable in UI and CLI; do NOT include Projects, MY_PIC.jpeg or NoX-Vault unless the user adds them).
Per folder the plan is: git.preflight -> git.ensure_repo -> git.verify_remote_private -> scan.large_files -> scan.secrets -> fs.scan_changes -> (stop with success "nothing to commit" if clean) -> git.stage -> git.commit -> git.push -> git.verify_pushed -> notify.
Rules:
 - Remote: a folder must already have an "origin" remote, or the user supplies one in the job setup form. NoX never invents a remote and never creates GitHub repos. A folder without a remote puts the run in needs_setup for that folder with a notification and a UI form (remote URL).
 - PRIVATE ONLY: for github.com remotes, git.verify_remote_private checks an unauthenticated GET of the repo's API URL: HTTP 200 means public and the push is BLOCKED (failed, reason "repository is public", urgent notification); 404 plus a successful authenticated "git ls-remote" means private; any other result is "unverified" and blocks unless the user set allow_unverified_remote=true for that folder. Non-GitHub remotes are unverified by default.
 - First run per folder requires explicit approval of the upload summary (Phase 1.1); later runs are automatic.
 - Never force-push, never rewrite history, never delete branches. Push is fast-forward only: run "git fetch" then check ancestry; if the remote is ahead or diverged, stop with failed reason "remote diverged, manual action needed" and a notification. No automatic merges or rebases.
 - Secret scan: skip and report any file matching private keys, .env, *.pem, id_rsa*, secrets.env, "nvapi-", GitHub/AWS/Google token patterns; the file is excluded from staging (add to a local .git/info/exclude, not the user's .gitignore), the rest is committed, and the notification names the skipped files (names only).
 - Large files: any file above 50 MB is skipped and reported (GitHub's hard limit is 100 MB); if git-lfs is installed and configured in the repo, use it, otherwise skip. Report the count and names.
 - Obsidian Vault: write a default exclude for volatile workspace files (.obsidian/workspace*.json, .trash/) via .git/info/exclude only.
 - Commit message is deterministic: "NoX backup <folder-alias> <YYYY-MM-DD HH:MM> (<n> files)". The LLM is never used during execution.
 - Handle .git/index.lock (wait up to 60 s, then fail with reason), detached HEAD (fail with reason), and an empty repo (first commit allowed).
 - Schedule: daily 20:00 Asia/Kolkata with the CATCH-UP rule: let due_slot be the most recent 20:00 that is in the past. The job is DUE when no run succeeded or is in progress for due_slot. So: powered off at 20:00 and booted at 09:00 next day, due_slot is yesterday 20:00, unserved, so it runs at start (after waiting for network, low CPU priority, within one tick of the daemon starting); booted at 19:59 with yesterday served, not due; at 20:00 it becomes due; booted at 21:30, due and runs; a manual "Run now" that succeeded after due_slot serves the slot. Any job schedule uses this slot rule (generalise the scheduler's catch-up; briefings keep their own scheduler).
 - Status in the UI: "Last success: 2026-11-04 20:03 | Next: Today 20:00 | Current: step 7/11 git.push (Study)".

## 6. PHASE 3: NOTIFICATIONS

 - The daemon sends desktop notifications itself (notify-send), independent of the UI, for: run succeeded (summary: folders pushed, commit short hashes, files changed, skipped files count, duration); run partial; run failed (failing step, folder alias, reason, one-line remediation); needs_setup; stalled/timeout; catch-up started after boot ("Backup missed yesterday, running now"); backup blocked (public repo, secret skipped, diverged).
 - Never spam: retries are silent, one notification per final outcome per run. Urgency: failed/blocked = critical, others normal.
 - Notification content never contains file names beyond the secret/large-file skip lists (names only, no contents) and never contains tokens. If the notification daemon supports actions, add "Open" which launches "nox-app --open /jobs/<id>" (or the existing fallback); record which notification daemon is installed and test.
 - In-app notification center lists the same events with timestamps (table notifications) and a "clear" action.

## 7. PHASE 4: DELETE CONTROLS (jobs, tasks, briefings)

 - UI: a Delete button (trash icon) on every job, task and briefing row/detail, with a confirmation dialog that states exactly what will happen, and an Undo toast (10 s) backed by soft delete (deleted_at, purged after 24 h by the daemon; nx purge for immediate removal). Multi-select delete for tasks and briefings, plus "Clear finished tasks".
 - Semantics: deleting a RUNNING job or task cancels it first through the supervisor (cooperative, completes within 5 s), then marks it deleted; deleting a job also removes its schedule, plans and (optionally, checkbox default ON) its run history; deleting a briefing removes briefing_runs/items/staging/feedback rows and the markdown file; vault notes created by NoX for that briefing are moved to the vault's .trash only if they still have status "new" and no user text outside the nox markers; anything the user edited is kept and listed in the confirmation result. Opportunity registry entries the user marked interested/applied are never deleted by a briefing delete.
 - API: DELETE /api/jobs/{id}, DELETE /api/tasks/{id}, DELETE /api/briefings/{id}, POST /api/{kind}/{id}/restore, bulk endpoints; all require the session token; return what was removed. CLI: nx jobs delete <id> [--purge], nx tasks delete <id>, nx tasks clear-finished, nx briefing delete <id>.
 - Chat intents: "delete the backup job" must ask for confirmation before acting (never delete on a single ambiguous message).

## 8. TESTS AND LIVE PROOFS (use a temp instance, a fake HOME, and a LOCAL BARE REPO as the remote for git; real GitHub is only touched read-only by the user's own checks)

 1. Unit/property tests: plan validator (rejects shell strings, out-of-root paths, delete/force operations), placeholder sanitiser, slot/catch-up rule with a fake clock (19:59, 20:00, boot 09:00 next day with unserved slot, boot 21:30, already served, suspend jump, offline retry), secret scanner, large-file skip, ahead/diverged detection, public-repo detection with a mock HTTP server, soft delete and undo.
 2. Planner privacy test: record the REAL outbound HTTP request body to the cloud endpoint and assert no path, URL or file name appears.
 3. LIVE git proof: a temp HOME with three small test folders (include a file with a fake private key, a 60 MB file, and an Obsidian-style .obsidian/workspace.json), a local bare repo as origin for each. Run the real executor: show the plan, approval, per-step rows, the resulting commits in the bare repos, the skipped files in the notification, a second run with no changes ("nothing to commit"), a run after modifying a file (new commit pushed), a diverged remote (fails with the right reason, nothing pushed), and a folder without a remote (needs_setup).
 4. LIVE catch-up proof: temp instance daemon stopped across a 20:00 slot (use the time override env var NOX_CLOCK_OFFSET or a documented slot override env like the briefing proofs; no hand edits), started again: the backup starts within one tick and completes; paste logs, job_runs and job_run_steps rows.
 5. LIVE watchdog/resume proof: SIGKILL the daemon mid-step (use a step with an artificial slow local git hook), restart, show resume from the first unfinished step, no repeated finished steps; show a hung step timing out into failed with a notification; show the repair reconciling a stuck "running" row created by killing the daemon (not by SQL).
 6. LIVE notification proof: paste the notification daemon identity and capture evidence of success, failure and needs_setup notifications (for example dbus-monitor output or the daemon's history).
 7. LIVE delete proof: in the temp instance via the UI (Playwright against the real daemon) and CLI: delete a running job (cancelled, schedule gone), delete tasks, delete a briefing with an edited vault note (kept) and an untouched one (moved to .trash), undo within 10 s, purge after expiry.
 8. Real-instance read-only check: after installing nothing, run "nx jobs repair --dry-run" against the REAL DB copy and paste what it would reconcile (the stuck 2026-11-04 run). Then provide the exact USER-RUN commands to apply it on the real daemon and to approve the plan for the real BackUp job.
 9. Final: uv run pytest (summary line), mypy, ruff on all of src and tests, npm test, npm run build, playwright, cargo test (if desktop touched), "git grep -n 'nvapi-'", "git diff --stat" per phase.

## 9. DO NOT

 - Do not let any LLM run during execution or decide commit contents; the LLM only produces the plan at creation time.
 - Do not use run_command or arbitrary shell for steps; only registered step kinds with argv lists.
 - Do not push to a public or unverified repo, force-push, rebase, merge or delete anything on a remote.
 - Do not send paths, file names, URLs or file contents to the cloud.
 - Do not leave any run "running" without a heartbeat; do not mark success without the verify step.
 - Do not hand-edit the real DB or the user's git/ssh/NoX config; do not read private keys.
 - Do not weaken or reshape tests; do not add test-mode shortcuts to production code; do not push the NoX repo.

## 10. FINAL REPORT

 A) Root cause of the stuck run (file:line + evidence). B) Per phase: files changed, commands run with outputs, labels. C) The plan JSON for the real BackUp job (with placeholders) and the user-facing preview. D) Proof table: planning privacy, per-step execution, catch-up, resume, watchdog, notifications, delete. E) NOT-DONE / MOCK-ONLY / limitations. F) USER-RUN steps: how to approve the plan for the real job, how to add remotes for the three folders if missing, how to make sure git auth works from the systemd user service, how to confirm tonight at 20:00 with "nx jobs show <id>" and "nx jobs runs <id>".
