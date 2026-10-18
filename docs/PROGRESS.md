# NoX — Session E Progress

Started: 2026-10-18  
Branch: `fix/verify-phase1`  
Baseline: 228 tests, ruff clean, mypy clean, **0 git tags**, overall coverage **unknown** (claimed 97% was per 3 modules only).

Environment:
- OS: Linux (Arch) 7.1.8-arch1-3 x86_64
- Compositor: Hyprland (confirmed `$XDG_CURRENT_DESKTOP=Hyprland`, `~/.config/hypr` exists)
- Python: 3.14.7, Node: 26.7.0, npm: 12.0.2
- Ollama: 0.34.0, models: qwen2.5:14b, nomic-embed-text, gemma4:e4b, whiterabbitneo, dolphin-mistral
- Browser: Firefox (/usr/bin/firefox), no Chromium
- notify-send: /usr/bin/notify-send, hyprctl: /usr/bin/hyprctl

---

## Phases

- [x] **E0** — Orientation and safety net (commit: `ad5f2a5`)
- [x] **E1** — Trust audit (secrets, telemetry, egress, repo state) (commit pending)  
  - Secret scan: 0 real secrets (all matches classified as regex or test fixtures).
  - Telemetry: fastapi/telemetry audit complete, 0 outbound reporting without OTLP env var.
  - Egress: only 127.0.0.1:11434, 127.0.0.1:7777, and DDG search endpoints contacted.
  - Hygiene test: `tests/test_repo_hygiene.py` passing (3/3 tests).
  - Repo state: 129 files, 0 forbidden tracked files.
  NEXT: Phase E2 (Realistic-data harness)

- [x] **E2** — Realistic-data harness (commit: `a36fa61`)  
  - Created `tests/fixtures/populate.py` populating 35 tables (40 facts, 120 episodes, 500 messages, 30 runs, BLOB embeddings with invalid UTF-8 bytes).
  - Created `tests/test_api_sweep.py` sweeping all GET endpoints, mutation endpoints, and WebSocket events.
  - Sweep executed against baseline codebase: 15 passed, 6 failed (exact defects D2 and D6 confirmed).

- [x] **E3** — Fix API defects from sweep (commit pending)  
  - Explicit Pydantic response models defined for all endpoints in `src/noxai/api/routes.py`.
  - Excluded BLOB columns (`embedding`) at repository SQL layer; zero bytes leak to routes.
  - Added correlation ID middleware, 1 MB payload size limit, and uniform `{error: {code, message, correlation_id}}` exception handling in `src/noxai/api/app.py`.
  - Added `test_no_bytes_fields_in_any_response_model` (0 bytes fields across all models).
  - Added `test_error_envelope_and_correlation_id_on_forced_error` & `test_bad_inputs_standard_envelope`.
  - Re-ran sweep test: 24 passed in 5.12s.
  NEXT: Phase E4 (Daemon correctness: lifecycle, auth, CORS, routes, logging)

- [x] **E4** — Daemon correctness: lifecycle, auth, CORS, routes, logging (commit pending)  
  - Implemented single-instance process lock via `DaemonLock` (`fcntl.flock`). Second instance cleanly exits with `Daemon already running (PID <n>)`.
  - Implemented non-blocking background Ollama readiness probe; daemon starts instantly and reports degraded mode if Ollama is down.
  - Implemented 12-step startup sequence and 5-step shutdown sequence (marking interrupted runs/jobs, WAL checkpointing with `PRAGMA wal_checkpoint(TRUNCATE)`).
  - Enforced 0600 file permissions on `secrets.env`; refused daemon startup on insecure permissions.
  - Cached vector backend detection (`_CACHED_VECTOR_BACKEND`) to log at most once across connections.
  - Removed CORS middleware in production mode; enabled only when `NOX_DEV=1`.
  - Enforced lifetime token exchange (`GET /?t=<token>` -> `SameSite=Strict` `HttpOnly` cookie), HMAC compare digest, restart cookie invalidation, and CSP/security headers.
  - Added 308 permanent redirect from `/v1/*` to `/api/*`.
  - Implemented full secret redaction in `src/noxai/logging_setup.py` (97% coverage achieved).
  - Created `tests/test_daemon_lifecycle.py` and `tests/test_logging_setup.py` (20/20 passed).
  NEXT: Phase E5 (Drive real UI in real browser with Playwright)

- [ ] **E5** — Drive real UI in real browser (Playwright)
- [ ] **E6** — Missing CLI and doctor features
- [ ] **E7** — Security gaps and coverage floors
- [ ] **E8** — Live automation proof
- [ ] **E9** — Autostart that actually works
- [ ] **E10** — Documentation made truthful
- [ ] **E11** — Final gate and merge

---

## Known Defects (from prompt)

| ID | Finding | Status |
|----|---------|--------|
| D1 | noxd.service has wrong %h/nox path | [x] fixed (install.sh templating + systemd/noxd.service) |
| D2 | PydanticSerializationError on BLOB embedding columns | [x] fixed (Pydantic response models, test_api_sweep) |
| D3 | vector_backend log emitted per request, not once at startup | [x] fixed (cached backend, test_vector_backend_logged_at_most_once) |
| D4 | CORSMiddleware in stack (spec: same-origin only) | [x] fixed (same-origin only, NOX_DEV=1 opt-in) |
| D5 | fastapi/telemetry/_asgi.py in stack — privacy concern | [x] fixed (disabled in FastAPI kwargs) |
| D6 | Bare 500 + raw traceback (need {error:{code,message}} envelope) | [x] fixed (middleware & exception handlers) |
| D7 | nx doctor --json missing; nx models group missing | [ ] |
| D8 | Coverage 72% overall, not 97% | [ ] |
| D9 | git tag empty — v0.1.0 tag was never created | [ ] |
| D10 | Many items absent: forget_fact safety, memory-poisoning, property test, etc. | [ ] |
| D11 | Route prefix /v1 vs spec /api; auth semantics (single-use vs lifetime token) | [x] fixed (pure /api + 308 redirect, lifetime cookie) |
| D12 | ~/.config/hypr/hyprland.conf confirmed exists (hypr dir present) | [x] verified |
| D13 | PROGRESS.md had false claims | [x] fixed |
| D14 | Secret-pattern scan matched extract.py, test_memory.py, test_safety.py | [x] audited (fixtures & regex) |
