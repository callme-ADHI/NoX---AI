# NOX SESSION K: REPAIR EVERYTHING UNPROVEN, PACKAGE FOR ARCH, DESKTOP APP "NoX", PROVE IT ALL

You are a senior staff engineer in the NoX repo (Python package noxai, CLI nx, FastAPI daemon noxd on 127.0.0.1:7777, SQLite WAL at ~/.local/share/nox/nox.db, React+TS UI in ui/, Arch Linux + Hyprland, user is "boss", GitHub callme-ADHI/NoX---AI). Sessions H, I and J happened. An independent review found that many Session J claims were unproven or fake. The user also now wants NoX as a real desktop app that starts with the system, with NO venv and NO "nx run" by hand. This session must leave every item in the acceptance matrix (Section 1) built, tested on the real system, and honestly reported.

## 0. HOW TO WORK (these rules are binding)

1. FIRST ACTION: save this entire prompt verbatim to docs/prompts/session-K.md and create docs/SESSION_K_PROGRESS.md (a checklist of every phase and acceptance item, updated after each phase). If your context is ever lost or compacted, re-read ONLY those two files plus git log. Never search for the spec anywhere else.
2. SECRETS: never read, grep, open or print anything under ~/.gemini or any transcript, task-log or history store of your own tooling. Never open ~/.config/nox/secrets.env contents, never run unfiltered env or printenv, never put a key value in a command, file, log, test fixture, commit or report (show keys only masked). At the end paste "git grep -n 'nvapi-'" (only test fixtures/regex patterns allowed).
3. EVIDENCE: every claim in the report needs a pasted command and its output, stored in docs/SESSION_K_EVIDENCE.md. Label each claim VERIFIED-LIVE (real system/network/real model), VERIFIED-TEST (automated test of the real code path), MOCK-ONLY, or NOT-DONE. FakeProvider/fake-DB/test-mode results are MOCK-ONLY and do not count for: scheduling, restart recovery, content discovery, link checks, cloud default, key pool, conversation memory, packaging, or the desktop app.
4. A briefing run with 0 items (or below the minimum quality gate) is a FAILURE, never "succeeded". Never write code that marks something successful to make a status look healthy.
5. REAL DATABASE SAFETY: never edit rows of the real DB by hand (no ad-hoc UPDATE/DELETE via python or sqlite3). Schema changes only through the migration runner, which must make an automatic timestamped backup first (~/.local/share/nox/backups/, keep the last 10; add "nx backup" and "nx backup restore <file>"). Every migration is tested on a COPY of the real DB (cp to /tmp) and a fresh DB. Anything destructive or time-sensitive runs in a TEMP INSTANCE: a second daemon on another port with its own data/config dirs via env vars NOX_DATA_DIR, NOX_CONFIG_DIR, NOX_PORT (add and document them if missing).
6. MIGRATIONS ARE IMMUTABLE once applied or committed. Session J edited migration 009 after it was committed. Inspect git history and the schema_migrations table of the real DB copy; add migration 010 so that BOTH a DB that applied the old 009 and a DB that applies the new 009 converge to the same schema. Test both paths.
7. TESTS: never weaken, delete or reshape a test just to make it pass. Any change to an existing test needs a written reason in docs/DECISIONS.md. Do not add test-mode branches in production code that bypass the code under test.
8. NO PLACEHOLDERS: no guessed data anywhere (no hardcoded benefits, eligibility, deadlines, links, "Online/Global/Open to all", "verified" stamps without a real check, example.com, canned TL;DR). Unknown stays "unknown".
9. PRIVACY INVARIANTS STAY: taint rules and egress filter are not loosened; local_only content never reaches a cloud-bound prompt; the briefing uses public data only; the user's eligibility profile and memories are never sent to any cloud model. Do not read eligibility from the memory system; it comes only from config [briefing.eligibility_profile].
10. SUDO: you cannot use sudo and must never ask for a password. For steps that need root, produce the exact commands, mark them USER-RUN in the report, and finish everything that does not need root. Do not install system packages silently: list missing packages as one exact "sudo pacman -S --needed ..." line (verify names with "pacman -Si"/"pacman -Ss" and current Tauri docs via web search; do not trust package names from memory) and continue.
11. Timezone-aware (zoneinfo, Asia/Kolkata) everywhere for slots. Decide ambiguities yourself, log them in docs/DECISIONS.md, continue. One commit per phase. Keep ruff, mypy, pytest, vitest, playwright green and paste the final summary lines of each.

## 1. ACCEPTANCE MATRIX (the report must show a status and evidence pointer for every row)

 A1  One package install; nx, noxd and nox-app are on PATH; no venv activation, no "uv run", no "nx run" by hand.
 A2  noxd starts at login as a systemd user service and restarts after a crash (kill -9 test).
 A3  The NoX desktop app opens automatically at login (autostart configured and the same launch command tested).
 A4  Closing the window does not stop the daemon; tray menu works; single instance.
 A5  Briefings fire by themselves at 04:00 and 18:00 (+ catch-up after boot/suspend), proven live in a temp instance; the real instance has a self-audit that exposes any failure.
 A6  ONE "Run briefing" button; the run survives refresh, navigation and closing the window; proven with a REAL run.
 A7  Content: all existing news kept; funded internships (free accommodation, abroad, stipend, value; all CS fields) and individual hackathons added; CTFs capped at 3; every benefit claim has validated page evidence; every link checked strictly.
 A8  Cloud/NVIDIA ON by default, persisted toggle, taint protection proven with real HTTP recording.
 A9  KeyPool really used by chat and briefing (leases, refill, release, daemon-side status).
 A10 Conversation memory proven with REAL models (cloud and local), not a fake provider.
 A11 Desktop notifications from the pipeline; clicking opens the app at the briefing (or a documented fallback).
 A12 Arch packaging verified (PKGBUILD builds, namcap, unit verified, contents correct, user-run verify script).
 A13 DB safety: automatic backups, migration 010 converges both paths, tested on a copy of the real DB.
 A14 Docs updated (README Arch install, INSTALL, DESKTOP, OPERATIONS, ARCHITECTURE, DECISIONS, PROGRESS, CHANGELOG).
 A15 Full test suites green with pasted output (pytest, mypy, ruff, vitest, playwright, cargo test).
 A16 Secrets hygiene proven.

## 2. PHASE 0: INTEGRITY REVIEW AND SAFETY NET

 1. "git status", "git log --oneline -30", and "git diff --stat 556f144..HEAD" (Session J range; adapt hashes to the repo). Back up the real DB with the new backup command before touching anything. Read the real DB READ-ONLY and paste: schema_migrations (applied versions and checksums), last 15 rows of briefing_runs (with sources_count, generation_seconds, state, trigger, rev), tasks of kind briefing, opportunity_registry (title, org, deadline, status, score).
 2. Write docs/audits/session-j-review.md with a verdict (keep / fix / revert) and evidence for each of: migration 009 edit after commit; the "self-healing reconciliation" in briefing/scheduler.py (it marked runs succeeded when the task was gone: remove that behaviour); programs.example.toml (hardcoded benefits_accommodation/stipend/travel as facts: remove); the claim that eligibility uses user memory; the hand-run UPDATE statements on the real DB; every edit to ui/e2e/ui.spec.ts, ui/playwright.config.ts, tests/fixtures/populate.py; any production code edit made to satisfy a test (loop.py, orchestrator.py, fake.py, app.py). Revert or fix what is wrong.
 3. Add "nx briefing repair" (documented, supervisor-checked, logged): finds runs stuck in running/queued whose supervisor task is gone, marks them interrupted and re-queues them for resume. It never edits rows to success. Use this instead of any manual SQL.

## 3. PHASE 1: AUTOMATION THAT IS REALLY PROVEN

Fix and then PROVE (no more fake confirmation):
 1. State machine: a run is succeeded only if its task succeeded AND items_total >= briefing.min_items_total (default 15) AND at least one news item and at least one opportunity item exist. Otherwise partial (some topics failed but gates pass for the rest) or failed, with a stored reason. Status output shows real item counts and durations.
 2. Resume: runs interrupted by a daemon restart are re-queued under the SAME run id; topic workers skip topics already done (briefing_topic_runs/staging); handlers are rebuilt from a registry by task kind on boot. Test with a REAL subprocess daemon killed with SIGKILL mid-run, restarted, and assert completion and that finished topics were not redone (count LLM/fetch calls).
 3. Scheduler (single BriefingScheduler, 30 s tick, morning window 04:00-17:59, evening window 18:00-03:59 belonging to the evening's date; any succeeded or running run of any trigger serves the slot; missed windows recorded as state='missed'; retries with backoff then notification). Keep fake-clock unit tests (03:59, 04:00, boot 09:30, 17:59, 18:00, boot 19:00 with morning unserved, 03:59 next day, duplicate prevention, suspend jump) and ADD the live proof:
    LIVE PROOF A (temp instance): start a daemon with NOX_BRIEFING_MORNING_START and NOX_BRIEFING_EVENING_START set to 2 and 4 minutes ahead, UI closed, no clicks, no API calls except read-only status. Paste: "nx briefing status" before; the daemon log lines of both slots firing; briefing_runs rows with trigger=scheduled, rev=1, state=succeeded, real item counts; the notification evidence.
    LIVE PROOF B (catch-up): stop that daemon, remove today's slot rows in the TEMP DB only, start it inside an unserved window, show a run starting within one tick; then show a 'missed' row for a fully skipped window.
 4. Self-audit for the REAL instance: "nx briefing audit" and a doctor check list the last 6 slots (scheduled / missed / failed / manual-served) with reasons, and the daemon sends a notification when a scheduled slot fails or was missed ("Morning briefing missed: daemon was not running"). This is how the user will see tomorrow's real 04:00 result.
 5. The single Run button: re-verify against a REAL briefing run in a temp instance (real network, real model, not the fake populate DB): click, reload mid-run, navigate to Chat and back, close the page, return; assert from the DB that the run finished and from the UI that it re-attached. Playwright with the real daemon; paste results and a screenshot of the in-progress view and the finished view.

## 4. PHASE 2: CONTENT YOU CAN TRUST

News topics stay exactly as they are (verify they still produce items). Fix and prove the new categories:
 1. programs.example.toml keeps only name, url, category and a human note. Benefits, eligibility and dates are NEVER seeded as facts. A typical window may be stored only as hint_window and is never displayed as a deadline.
 2. Internship discovery (all CS fields, abroad preferred, India and remote lower unless funded):
    a. Watchlist pages: fetch each official page, extract the CURRENT cycle (status open/closed/opens-soon, deadline parsed from the page text, eligibility, benefits). No dates on the page means "unknown, check page".
    b. Search discovery: at least 12 query templates built from the current date (current and next year) times fields (software, AI/ML, data, cybersecurity, IoT/embedded, robotics, systems, research) and benefit phrasings (stipend, accommodation provided, fully funded, international students, visa support). Use the existing search tool with rate limits; filter low-quality and ToS-forbidden domains (no LinkedIn/Indeed/Glassdoor scraping); fetch result pages politely (robots.txt, cache); extract with trafilatura.
    c. Aggregators that permit fetching (check robots.txt; document in docs/audits/sources.md with real probe codes).
 3. Extraction: strict pydantic schemas (InternshipItem, HackathonItem, Competition) as in Sessions H/J, with the EVIDENCE RULE enforced in code: every benefit/eligibility value other than "unknown" carries a snippet of at most 25 words that must occur (normalised, case-insensitive) in the fetched page text; otherwise the value is forced to "unknown". Unit-test with a fabricated snippet. The LLM never writes URLs or deadlines.
 4. Hackathons are INDIVIDUAL EVENTS (name, dates, deadline, prize, mode, location, fee, eligibility, apply link), parsed from listing pages and then event pages (Devpost, MLH, Devfolio, Unstop, HackerEarth, ETHGlobal, DoraHacks; verify each source live and keep what works). A listing page is never an item.
 5. Eligibility match is done locally in code from config [briefing.eligibility_profile] (nationality India, level undergraduate, field computer science). "no" is dropped, "unknown" shows "check eligibility".
 6. Value score, quotas, CTF cap (max 3, starting within 14 days), carry-over registry ("still open" vs "new" vs "updated", no duplicates across days), notifications for high-score items and deadlines within 3 days: keep the Session J design, but prove it with real data.
 7. STRICT link verification of every included URL in the run: HEAD then GET, 2 tries, 10 s timeout. 2xx/3xx pass. 401/403/429 count as "blocked or login" and are kept only if the page text was fetched successfully earlier in this run (flag it). 404/410/5xx/timeout/DNS failure and soft-404 pages (title or text says not found) are dropped. Store the check result per item.
 8. Honest shortfalls: if fewer funded internships (target 10, minimum 6) or hackathons (target 8) exist, the digest says "only N found". No padding with listing pages, watchlist stubs or expired items.
 9. Observability: record per-stage timings and counts (sources fetched, pages fetched, LLM calls, items dropped by reason) in "How this was made". A run that takes seconds must be explainable by those counts.
 10. LIVE PROOF: one real run on the real network with the real cloud model. Paste: category counts versus quotas; the Funded internships and Hackathons sections of the digest; THREE internship notes, THREE hackathon notes and TWO CTF notes in full with evidence snippets; a table of EVERY URL in the digest with HTTP code and timing; drop-reason counts; stage timings; a second run proving carry-over and no duplicates; a grep proving no placeholder strings in the vault.

## 5. PHASE 3: CLOUD, KEYPOOL, CONVERSATION MEMORY — PROVEN WITH REAL PATHS

 1. Cloud default (temp instance with a fresh data dir and the real keys loaded via secrets file): /api/status shows cloud enabled; a normal chat takes the cloud route (route chip/trace); toggle off, restart, still off; REST, WebSocket and CLI defaults agree. Use a recording HTTP transport (not FakeProvider) to prove that a local_only message never reaches the cloud request body. A briefing with cloud off runs locally and is labelled degraded.
 2. KeyPool: paste every non-test call site of lease() and report_outcome(); show "nx keys status" read from the DAEMON API during a live run; a local mock HTTP server that returns 429 with Retry-After and 401 and 404/410 through the REAL client to prove cooldown, failover, invalid-key marking, token-bucket refill, job release, and the model-unavailable alert. Enforce the Session H allocation policy (interactive reserve, pinned single job, wide fan-out when idle, rebalance at request boundaries) with property tests.
 3. Conversation memory with REAL models: "nx eval conversation --live" runs 8 scenarios against the real cloud model and again against the local model: pronoun follow-up; ordinal reference ("the second one"); recall of a detail from turn 3 after 30 turns; tangent then return; correction supersedes an earlier statement; cross-session fact recall; daemon restart mid-conversation then continue; UI reload with the same conversation id then continue. Each is scored by string checks plus a yes/no judge. Paste the pass/fail table and three raw transcripts. Fix failures (follow-up rewrite, router inheritance, thread state, fact supersede, summary folding) until at least 7 of 8 pass on each route, or report honestly what fails. Verify the memory drawer has pin message, forget message, edit summary and reset; add what is missing.

## 6. PHASE 4: ARCH PACKAGING (NO MORE VENV)

 1. Layout (document the decision): application in /opt/nox with a private virtualenv built at package time from the lockfile (uv sync --frozen --no-dev or equivalent), the built UI dist inside the package, wrappers /usr/bin/nx and /usr/bin/noxd, systemd user unit /usr/lib/systemd/user/noxd.service (Restart=on-failure, RestartSec, WantedBy=default.target, sane working dir, no hardening flag that breaks D-Bus/notify-send), icon and desktop entry under /usr/share, license, a .install file that tells the user to run "nx setup". The package NEVER writes to ~/.config or ~/.local and NEVER deletes user data on removal.
 2. packaging/arch/PKGBUILD for package "nox" (daemon + CLI + UI) and package "nox-app" (Tauri binary, depends on nox and webkit2gtk-4.1). pkgver from git describe. "nx --version" and /api/version report it; the app warns on a daemon/app version mismatch.
 3. Build as the normal user: "makepkg -f" (and "makepkg --printsrcinfo > .SRCINFO"). Run namcap on both packages and the PKGBUILDs; list contents with "bsdtar -tf"; run "systemd-analyze --user verify" on the unit; run "desktop-file-validate" on the desktop entry. If devtools/podman/docker/systemd-nspawn exist, do a clean-chroot build and an install into a clean Arch container and run the verify script there. Paste everything.
 4. Add scripts/verify-install.sh for the USER to run after "sudo pacman -U ...": checks binaries on PATH, unit enabled and active, /api/health, desktop entry and icon, app binary, nx doctor, versions. The agent cannot run the root install; mark that step USER-RUN with the exact commands in the report.
 5. Developer mode (uv run) must keep working. Add "make" or "just" targets: build-ui, build-app, package, verify.

## 7. PHASE 5: THE DESKTOP APP "NoX" (TAURI 2, ARCH/HYPRLAND)

Directory desktop/. Verify the current Tauri 2 Linux prerequisites from the official docs by web search and "pacman -Si"; if rustc/cargo or webkit2gtk-4.1 or a tray library is missing, print the exact pacman line for the user and continue as far as possible (mark the build NOT-DONE only if it truly cannot proceed).
 Behaviour:
 1. The window loads the daemon-served UI at http://127.0.0.1:7777 (so UI and daemon always match). Use the SAME authentication flow that "nx open" uses today (read the code; do not weaken auth); the token is read from the token file at runtime and never logged, never stored in history, never put in a visible URL after exchange.
 2. Startup: show a bundled night-sky splash page ("Starting NoX..."), poll /api/health every 500 ms; if the daemon is not up after 3 s run exactly "systemctl --user start noxd" (the only command the app may run), wait up to 90 s, then navigate. On failure show an offline page with Retry, "Start daemon", and a hint to run "nx doctor". If the daemon dies while the app is open, show the offline page and recover automatically when it returns.
 3. Window: title NoX, class/app-id "NoX", background colour #05060F (no white flash), remembers size/position, native decorations. Close button hides to tray (config desktop.close_to_tray default true). Single instance: a second launch focuses the existing window and forwards "--open <path>" (for example /briefings). "--hidden" starts in the tray. By default the app opens its window at login.
 4. Tray menu: Open NoX; Run briefing now (POST /api/briefings/run); label "Next briefing: Today 18:00" (updated every 30 s from /api/briefings/status); "Last briefing: Morning 04:07 OK"; Cloud: On/Off toggle that calls the settings API and shows the real state; Open Notes Vault; Quit app (daemon keeps running); Stop daemon and quit. If no StatusNotifier host exists, log a warning, keep working without the tray, and let nx doctor report it.
 5. Notifications: the daemon keeps sending desktop notifications. Detect whether the running notification daemon supports actions (query capabilities via D-Bus; record which daemon is installed, e.g. mako/dunst/swaync). If supported, the "briefing ready" notification has an "Open" action that runs "nox-app --open /briefings"; otherwise fall back to a plain notification plus the tray item. Test live and paste the evidence.
 6. Security: restrict navigation to the daemon origin and the local splash/offline pages; open external http(s) links in the default browser through one Rust command that validates the scheme; no Tauri IPC capability granted to the remote-origin webview; devtools off in release; CSP on bundled pages; no file:// access. Run cargo audit if available.
 7. Wayland/NVIDIA: test on the real machine (read GPU info read-only). If WebKitGTK renders blank or crashes, apply and document the needed environment workaround in the desktop entry Exec line, decided by test, not by assumption.
 8. Icons: design an SVG moon-and-stars NoX icon plus a monochrome tray variant; export PNGs (16, 24, 32, 48, 64, 128, 256, 512) with whatever tool exists (rsvg-convert, inkscape or imagemagick; install instructions if missing); hicolor install paths in the packages.
 Tests and proofs:
 - cargo test: unit tests for URL building, token reading, the navigation allow-list, scheme validation, and tray label formatting.
 - Smoke test against a temp-instance daemon (real Wayland session with hyprctl, otherwise Xvfb with GDK_BACKEND=x11): app launches; window exists (hyprctl clients -j shows class/title); second launch creates no second window; closing hides it and the process stays; tray "Run briefing now" results in a new run via the API; killing the daemon shows the offline page and restarting it recovers; screenshots saved to docs/screenshots/.
 - Build "cargo tauri build --no-bundle" (or cargo build --release), report binary size and idle RSS after 60 seconds.

## 8. PHASE 6: SETUP, AUTOSTART, DOCTOR, DOCS

 1. "nx setup" (idempotent, safe to re-run): creates ~/.config/nox, ~/.local/share/nox and the vault folder; copies config.example.toml to ~/.config/nox/config.toml ONLY if missing; creates secrets.env mode 0600 with a commented template and no keys; runs "systemctl --user enable --now noxd"; "--autostart" writes ~/.config/autostart/nox.desktop; "--hyprland" prints the line "exec-once = nox-app" (and "nox-app --hidden" variant); "--hyprland --apply" appends it to ~/.config/hypr/hyprland.conf only if absent, after a timestamped backup, and prints the diff; detect uwsm/graphical-session and mention the right option; "--linger" only prints the exact "loginctl enable-linger" command and explains the effect (never run it silently). "nx setup --check" runs the doctor subset.
 2. nx doctor additions: daemon unit enabled and active; app installed and version match; autostart configured; tray host present (StatusNotifierWatcher via busctl); notification daemon and action support; scheduler running and next run times; last 6 slots result; cloud enabled; key count (never values) and per-key health; DB backups present; secrets file permissions; migration level; vault path.
 3. Docs: README Arch install section ("makepkg -si", "nx setup --autostart --hyprland --apply", reboot), docs/INSTALL.md, docs/DESKTOP.md (tray, flags, notifications, Wayland notes), docs/OPERATIONS.md (logs via journalctl, backups and restore, troubleshooting, how to read "nx briefing audit"), ARCHITECTURE, DECISIONS, PROGRESS, CHANGELOG.

## 9. FINAL VERIFICATION (paste raw output into docs/SESSION_K_EVIDENCE.md and summarise in the report)

 1. Phase 0 review file and DB state, backup listing.
 2. Live proofs A and B (automation), SIGKILL resume proof, real Run-button proof.
 3. The real content run (Section 4 item 10).
 4. Cloud default, taint recording, KeyPool proofs, live conversation eval with transcripts.
 5. Package build, namcap, unit verify, desktop-file-validate, bsdtar listing, verify-install.sh content.
 6. App smoke test results, screenshots, binary size and idle RSS.
 7. systemd kill -9 test of noxd on a temp instance (or a transient user unit): show systemd restarting it.
 8. uv run pytest (final summary line), mypy, ruff, npm test, npm run build, playwright, cargo test, "git grep -n 'nvapi-'", "git diff --stat" per phase.

## 10. DO NOT

 - Do not run the pipeline in a request, CLI or UI process; do not add a second scheduler.
 - Do not hand-edit the real DB, do not edit applied migrations, do not edit the user's config.toml or secrets.env.
 - Do not mark runs successful without real items; do not paper over failures.
 - Do not hardcode or guess benefits, eligibility, deadlines or links; do not scrape sites that forbid it.
 - Do not send memory, eligibility profile or local-only text to the cloud; do not loosen taint/egress.
 - Do not make the app own the scheduler or the pipeline; the daemon owns all background work.
 - Do not give the remote-origin webview any Tauri IPC power.
 - Do not read transcripts/logs of your own tooling; do not print secrets.
 - Do not claim anything without pasted evidence.

## 11. FINAL REPORT FORMAT

 A) Acceptance matrix table A1-A16: status label, one-line evidence, pointer to docs/SESSION_K_EVIDENCE.md.
 B) Per phase: files changed, commands run with outputs, labels.
 C) Root causes found and fixed (with file:line): why scheduled runs did not fire, why "0 items succeeded" appeared, why resume failed, why the content looked like a watchlist.
 D) USER-RUN steps with exact commands: install missing packages, "makepkg -si" or "sudo pacman -U <files>", "nx setup --autostart --hyprland --apply", "scripts/verify-install.sh", reboot test, and how to read tomorrow's results ("nx briefing audit", "nx briefing status").
 E) Everything NOT-DONE or MOCK-ONLY, known limitations, top 5 risks.
