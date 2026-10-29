# Phase 1: Briefing & Task Architecture Audit

Date: 2026-10-29
Branch: main / Session H

---

## 1. Executive Summary & Exact Root Cause of the "Cancel" Bug

### The Exact Root Cause (in 3 lines):
1. In `src/noxai/api/routes.py` (`POST /jobs/{job_id}/run`, lines 903-915) and `src/noxai/ui/src/pages/BriefingsPage.tsx` (`handleRunNow`, lines 43-57), the daily briefing was invoked synchronously within the HTTP request handler creating a fresh ephemeral `JobScheduler` instance (`JobScheduler(router=router, registry=default_registry, config=cfg)`) rather than running under a detached, daemon-supervised background task.
2. In `src/noxai/scheduler/scheduler.py` (lines 283-290, 400-419), jobs run under an instance semaphore `self._local_job_sem` and rely on an in-memory `asyncio.Task` (`self._task = asyncio.create_task(self._run_loop())`). When a user opens the UI or triggers chat requests, any connection interruption, client timeout, or concurrent UI navigation re-renders/aborts requests or encounters process lock contention (`PRAGMA busy_timeout=30000;`).
3. Furthermore, background tasks in `src/noxai/tasks/center.py` (lines 48-52, 401-449) and `src/noxai/api/routes.py` (`active_runs[run_id] = task`, lines 466-477) were tied to per-request `asyncio.create_task` wrappers with callback cleanup, without a persistent daemon supervisor holding strong references, without heartbeats, and without resume semantics. When UI sessions disconnect or chat runs start (`active_runs`), orphaned tasks were garbage-collected or killed on socket close.

---

## 2. Why the Briefing Required a Manual Click

In `src/noxai/scheduler/seeds.py` (lines 20-70):
1. **Misconfigured Cron Slot**: The default seed for `daily-briefing` was set to `schedule_expr: "30 7 * * *"` (07:30 AM Asia/Kolkata). If a machine was booted after 07:30 AM (e.g. 09:30 AM), `should_catchup` in `src/noxai/scheduler/scheduler.py` (lines 90-125) depended on `catchup: "once"` in the job's config JSON. If the database row was initialized with `next_run_at` calculated only for the *next* day or skipped, the job never automatically executed upon boot.
2. **Missing Evening Slot**: There was no seed or automated scheduler handler for an evening slot (18:00).
3. **No Catch-up Safe Date+Slot State Machine**: The scheduler lacked a dedicated `briefing_runs` table enforcing unique idempotent executions per slot (`morning` vs `evening`) with boot catch-up.
4. **UI Reliance**: In `src/noxai/ui/src/pages/BriefingsPage.tsx`, the primary path presented to the user was an interactive "Run Now" button calling `POST /jobs/{job_id}/run`.

---

## 3. How the Cloud Model Is Chosen & What Happens on HTTP Errors

In `src/noxai/models/router.py` (lines 40-75) and `src/noxai/models/nvidia.py` (lines 220-330):
1. **Model Role Resolution**:
   - The router resolves roles (`front`, `heavy`, `research`, `writer`, `classifier`). In `config.toml`, `front` and `heavy` were pointed to `nvidia/nemotron-3.5-lightning-30b-a3b`.
2. **Key Acquisition**:
   - `NvidiaKeyPool` (`src/noxai/models/key_pool.py`) picked keys based on sliding-window request timestamps.
3. **HTTP 410 / 404 / 401 Behavior**:
   - When an HTTP error occurred (such as HTTP 410 Gone when NVIDIA decommissioned a model, or HTTP 401/403 Invalid Key):
     - In `nvidia.py` lines 306-320, HTTP 401/403 marked the key with an error cooldown.
     - HTTP 410/404 was NOT explicitly caught as a model-decommissioned event; it was raised as a generic `httpx.HTTPStatusError`.
     - In `src/noxai/orchestrator.py` (lines 330-360), any exception raised during cloud chat caught the error and fell back to the local `resident` or `brain` Ollama model (`qwen2.5:14b`). On CPU, this ran at 0.2–1.8 tokens/sec, causing 200+ second hangs without an active model alert or UI banner.
   - For briefings, running on a 2 token/sec CPU model caused 600s timeouts (`TimeoutError`), failing the job silently into `job_runs.status = 'failed'`.

---

## 4. Comprehensive Inventory of All NVIDIA Key Usage Sites

| File | Lines | Purpose / Behavior |
|---|---|---|
| `src/noxai/config.py` | 219–220, 325–357 | Loads `NVIDIA_API_KEY`, `NOX_NVIDIA_API_KEY`, `NVIDIA_API_KEYS`, and `NVIDIA_API_KEY_1..8` from `secrets.env`. |
| `src/noxai/doctor.py` | 170–205 | Checks permissions of `~/.config/nox/secrets.env` (must be 0600) and checks presence of `NVIDIA_API_KEY`. |
| `src/noxai/models/cloud_health.py` | 290–330 | Inspects `secrets.env` and environment variables to detect key changes and initialize health state. |
| `src/noxai/models/nvidia.py` | 100–145, 170–178, 250–315 | Initializes `NvidiaKeyPool`, passes `Bearer {chosen_key}` in headers, marks rate limits (429) and auth errors (401/403). |
| `src/noxai/models/key_pool.py` | 1–221 | Existing sliding-window key tracker; needs upgrade to the Phase 3 `KeyPool` specification (interactive vs pinned vs wide, lease context manager, daily DB counters, EWMA latency). |
| `src/noxai/models/router.py` | 40–55 | Instantiates `NvidiaProvider` passing extracted keys. |
| `src/noxai/orchestrator.py` | 320–335 | Checks key presence to decide if cloud acceleration is possible. |
| `src/noxai/commands/cloud.py` | 30–45, 125–140 | Implements `nx cloud status` and `nx cloud test`. |
| `src/noxai/commands/models.py` | 50–60, 150–165, 280–290 | Implements `nx models bench-cloud` using the configured key. |
| `src/noxai/api/routes.py` | 375–385 | Reports cloud availability in `GET /api/status`. |
| `tests/conftest.py` & test suites | Various | Synthetic fixtures using allowlisted dummy key `nvapi-secret1234567890abcdefghijklmnopqrstuvwxyz`. |

---

## 5. Next Steps for Phase 2 & Beyond

1. **Phase 2 (Detached Task Supervisor)**:
   - Create `src/noxai/tasks/supervisor.py` owned by the daemon lifespan (`app.state.supervisor`).
   - Strong asyncio references in supervisor registry + SQLite table with heartbeat timestamp.
   - Decouple all task lifecycles from HTTP requests, WebSockets, or UI disconnects.
   - Automatic resume on daemon startup for interrupted tasks, resuming only unfinished subtasks.
2. **Phase 3 (KeyPool & Model Validation)**:
   - Implement `src/noxai/models/keypool.py` with `INTERACTIVE`, `PINNED`, `WIDE`, and `MULTI-JOB` allocation modes.
   - Add hot-reload, daily metrics persistence, and live model verification against NVIDIA `/v1/models`.
3. **Phase 4 & 5 (Briefing Engine & Feedback Loop)**:
   - Two daily slots (04:00, 18:00 Asia/Kolkata), automatic catch-up on boot, verified RSS/web sources.
   - Anti-hallucination link validation, staged artifacts, prompt versioning, and feedback auto-tuning.
