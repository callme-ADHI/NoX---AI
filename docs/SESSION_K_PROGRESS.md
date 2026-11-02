# Session K Progress Checklist

Tracking execution across all Session K phases and the Acceptance Matrix.

## Acceptance Matrix (Section 1)

- [x] **A1**: One package install; `nx`, `noxd`, and `nox-app` on PATH; no venv activation, no `uv run`, no `nx run` by hand.
- [x] **A2**: `noxd` starts at login as a systemd user service and restarts after a crash (`kill -9` test).
- [x] **A3**: NoX desktop app opens automatically at login (autostart configured and launch command tested).
- [x] **A4**: Closing the window does not stop the daemon; tray menu works; single instance enforced.
- [x] **A5**: Briefings fire by themselves at 04:00 and 18:00 (+ catch-up after boot/suspend), proven live in temp instance; real instance has self-audit exposing failures.
- [x] **A6**: ONE "Run briefing" button; run survives refresh, navigation, and window close; proven with REAL run.
- [x] **A7**: Content: all existing news kept; funded internships (free accommodation, abroad, stipend; all CS fields) and individual hackathons added; CTFs capped at 3; every benefit claim has validated page evidence; links strictly checked.
- [x] **A8**: Cloud/NVIDIA ON by default, persisted toggle, taint protection proven with real HTTP recording.
- [x] **A9**: KeyPool really used by chat and briefing (leases, refill, release, daemon-side status).
- [x] **A10**: Conversation memory proven with REAL models (cloud and local), not a fake provider.
- [x] **A11**: Desktop notifications from pipeline; clicking opens app at briefing (or documented fallback).
- [x] **A12**: Arch packaging verified (PKGBUILD builds, namcap, unit verified, contents correct, user-run verify script).
- [x] **A13**: DB safety: automatic backups, migration 010 converges both paths, tested on a copy of the real DB.
- [x] **A14**: Docs updated (README Arch install, INSTALL, DESKTOP, OPERATIONS, ARCHITECTURE, DECISIONS, PROGRESS, CHANGELOG).
- [ ] **A15**: Full test suites green with pasted output (pytest, mypy, ruff, vitest, playwright, cargo test).
- [ ] **A16**: Secrets hygiene proven (`git grep -n 'nvapi-'`).

---

## Phase Execution Checklist

- [x] **Phase 0: Integrity Review and Safety Net**
  - [x] Git status, git log, git diff inspection
  - [x] Automatic timestamped DB backup (`~/.local/share/nox/backups/`, keep last 10, `nx backup`, `nx backup restore <file>`)
  - [x] Read real DB read-only: schema_migrations, last 15 briefing_runs, tasks of kind briefing, opportunity_registry
  - [x] Write `docs/audits/session-j-review.md` (verdicts & evidence on migration 009, scheduler reconciliation, programs.example.toml, eligibility claims, manual SQL, test edits)
  - [x] Add `nx briefing repair` (marks orphaned runs interrupted, re-queues them, never marks success)
  - [x] Phase 0 git commit

- [x] **Phase 1: Automation That Is Really Proven**
  - [x] State machine: Succeeded ONLY if task succeeded AND items >= min_items_total AND news >= 1 AND opp >= 1; else partial/failed with stored reason
  - [x] Resume: Interrupted runs re-queued under SAME run_id, skip finished topics, rebuild handlers from registry on boot. Subprocess SIGKILL mid-run test
  - [x] Scheduler: 30s tick, morning 04:00-17:59, evening 18:00-03:59 (belonging to evening date), missed window recorded as state='missed'
  - [x] LIVE PROOF A: Temp instance with slots 2m & 4m ahead, UI closed, logs & rows pasted
  - [x] LIVE PROOF B: Catch-up & missed window in temp instance
  - [x] Self-audit: `nx briefing audit` + doctor check listing last 6 slots with notifications on failure/miss
  - [x] Single Run button: Real briefing run in temp instance with Playwright reload/navigation re-attach test & screenshots
  - [x] Phase 1 git commit

- [x] **Phase 2: Content You Can Trust**
  - [x] `programs.example.toml` clean (name, url, category, human note, hint_window only; no hardcoded facts/benefits/deadlines)
  - [x] Watchlist discovery & Search discovery (12+ query templates across CS fields, trafilatura extraction, robots.txt compliance)
  - [x] Aggregator probe audit in `docs/audits/sources.md`
  - [x] Evidence Rule enforced in code: <= 25 words verbatim from page text, else "unknown". Unit tests with fabricated snippet
  - [x] Hackathons as INDIVIDUAL events parsed from listings & event pages
  - [x] Eligibility match from config [briefing.eligibility_profile] only
  - [x] Strict link verification (HEAD then GET, 2 tries, 10s timeout, drop 404/5xx/soft-404)
  - [x] Honest shortfalls ("only N found"), stage timings & drop reasons in "How this was made"
  - [x] LIVE PROOF: Real run with cloud model, counts, digest excerpts, 3 internship notes, 3 hackathon notes, 2 CTF notes, URL table, no vault placeholders
  - [x] Phase 2 git commit

- [x] **Phase 3: Cloud, KeyPool, Conversation Memory — Proven With Real Paths**
  - [x] Cloud default in fresh temp instance with real keys, recording HTTP transport proving local_only never egresses, cloud off runs locally degraded
  - [x] KeyPool: paste non-test call sites of lease() & report_outcome(); daemon status; mock HTTP server testing 429/401/404 handling & session H policy property tests
  - [x] Conversation memory with REAL models: `nx eval conversation --live` (8 scenarios scored against cloud and local models, transcripts pasted, memory drawer operations verified)
  - [x] Phase 3 git commit

- [x] **Phase 4: Arch Packaging (No More Venv)**
  - [x] Package layout in `/opt/nox`, wrappers `/usr/bin/nx` and `/usr/bin/noxd`, systemd unit `/usr/lib/systemd/user/noxd.service`, icon and desktop entry
  - [x] `packaging/arch/PKGBUILD` for "nox" and "nox-app", pkgver from git describe, version match warning
  - [x] Non-root build: `makepkg -f`, namcap, `bsdtar -tf`, `systemd-analyze verify`, `desktop-file-validate`, clean chroot/container if available
  - [x] `scripts/verify-install.sh` for user verification
  - [x] Make / just targets: build-ui, build-app, package, verify
  - [x] Phase 4 git commit

- [x] **Phase 5: The Desktop App "NoX" (Tauri 2, Arch/Hyprland)**
  - [x] Directory `desktop/`, check Tauri 2 Linux prereqs via web search & pacman
  - [x] Window loads daemon UI at `http://127.0.0.1:7777` using token file auth (never visible in URL)
  - [x] Bundled night-sky splash page ("Starting NoX..."), poll `/api/health`, run `systemctl --user start noxd` after 3s, offline page with retry
  - [x] Window settings: class "NoX", title NoX, `#05060F`, close hides to tray, single instance with `--open <path>` and `--hidden`
  - [x] Tray menu: Open NoX, Run briefing now, Next briefing, Last briefing, Cloud toggle, Open Notes Vault, Quit app, Stop daemon and quit
  - [x] Notification action support: D-Bus action detection, `nox-app --open /briefings`
  - [x] Security: restrict navigation to daemon origin, external links in browser, no Tauri IPC to remote origin, CSP
  - [x] Wayland/NVIDIA verification, SVG + PNG icons (16 to 512px)
  - [x] Tests & proofs: `cargo test`, smoke test against temp daemon, screenshots, binary size & idle RSS
  - [x] Phase 5 git commit

- [x] **Phase 6: Setup, Autostart, Doctor, Docs**
  - [x] `nx setup` (idempotent, `--autostart`, `--hyprland`, `--hyprland --apply`, `--linger`, `--check`)
  - [x] `nx doctor` additions (unit active, app installed/version match, autostart, tray, notifications, scheduler, last 6 slots, backups, etc.)
  - [x] Documentation: README Arch install, `docs/INSTALL.md`, `docs/DESKTOP.md`, `docs/OPERATIONS.md`, `ARCHITECTURE.md`, `CHANGELOG.md`
  - [x] Phase 6 git commit

- [ ] **Phase 7: Final Verification and Report**
  - [ ] Paste all raw outputs into `docs/SESSION_K_EVIDENCE.md`
  - [ ] Green test suites: pytest, mypy, ruff, vitest, playwright, cargo test
  - [ ] Secrets check `git grep -n 'nvapi-'`
  - [ ] Deliver Section 11 Final Report format
