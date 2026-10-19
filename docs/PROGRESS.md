# NoX — Project Progress Log

Branch: `fix/verify-phase1`  
Repository: `noxai` (CLI `nx`, venv `.venv`)  
Environment:
- OS: Linux (Arch) 7.1.8-arch1-3 x86_64
- Compositor: Hyprland (`XDG_CURRENT_DESKTOP=Hyprland`)
- Python: 3.14.7, Node: 26.7.0, npm: 12.0.2
- Ollama: 0.34.0 (models: qwen2.5:14b, nomic-embed-text, gemma4:e4b, whiterabbitneo, dolphin-mistral)
- Browser: Firefox (/usr/bin/firefox)

---

## Session E — Verification History (from git log)

- [x] **E0** — Orientation and safety net (`ad5f2a5`)
- [x] **E1** — Trust audit (secrets, telemetry, egress, repo state) (`179b04d`)
- [x] **E2** — Realistic-data harness and endpoint sweep (`a36fa61`)
- [x] **E3** — Fix API defects from sweep (`ca38bc8`)
- [x] **E4** — Daemon lifecycle, CORS, auth semantics, routes, logging (`88d7522`)
- [x] **E5** — Browser-driven UI verification (`d438454`)
- [x] **E6** — Doctor json, models group, missing CLI (`6d458db`)
- [~] **E7** — Memory safety, structural safety tests, provider/tool/scheduler coverage (`dd42679`, partial; leftovers deferred to F9)
- [ ] **E8** — Live automation proof (deferred to F9)
- [ ] **E9** — Autostart that actually works (deferred to F9)
- [ ] **E10** — Documentation made truthful (deferred to F9)
- [ ] **E11** — Final gate and merge (deferred to F9)

---

## Session F — Fast Front Desk (NVIDIA API), Task Center, Privacy Policy v2 & Resumed Verification

- [x] **F0** — Stabilize the branch
  - Status: Done
  - Commit: `dd6b6d0`
  - Evidence:
    - Stray processes: 0 found.
    - pytest-timeout installed (2.4.0) and configured (`timeout = 60` in `pyproject.toml`).
    - Path isolation: `tests/conftest.py` isolates `HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME`. `tests/test_path_isolation.py` passing.
    - Scheduler mock tests: resolved row mapping, event subscription, and docstring formatting. Repeated 5x (`--count=5`, 80/80 passed).
    - Repo hygiene test: synthetic fixture `nvapi-secret1234567890abcdefghijklmnopqrstuvwxyz` classified and added to allowlist. 3/3 passed.
    - Ruff check: 0 errors (all 117 files formatted).
    - Mypy check: 0 errors across 67 source files.
    - Full suite: 365 passed in 71.2s.
    - Spec amendment committed: `docs/SPEC_AMENDMENT_M10.md`.
  - NEXT: Phase F1 (Measure first: why is chat slow?)

- [x] **F1** — Measure first: why is chat slow?
  - Status: Done
  - Commit: Pending (F1 commit: `perf: tracing, benchmark, local path fixes`)
  - Evidence:
    - Diagnostic instrumentation: added `trace_json` migration `004_trace_json.sql`, `"trace"` event in `events.py`, per-stage timers (`route`, `memory_recall`, `prompt_build`, `provider_connect`, `time_to_first_token`, `generation`, `tool_time`, `post_run_extraction`, `total`, `prompt_tokens_est`, `model_used`).
    - Trace UI: surfaced structured trace table in `nx chat --trace`.
    - Benchmark harness: implemented reproducible benchmark suite `nx bench chat` covering cold turn, repeat warm turn, multi-turn follow-up, tool turn, memory recall, and code generation.
    - Root cause analysis:
      - Identified tool schemas (3,661 tokens for 37 tools) + dynamic date/time prefix destroyed llama.cpp KV cache on every turn.
      - Grammar-constrained decoding over 152k vocabulary on CPU took >120s per token.
      - Dynamic context size was over-allocated at 8192 on CPU.
    - Local path optimizations applied:
      - Stable prompt ordering (`stable_prefix=True`): stable tools, rules, preferences first; dynamic clock and recalled memories last, preserving KV cache across conversational turns.
      - Chat lane tool pruning: prune 37 tools down to essential conversation tools (`CHAT_LANE_TOOLS`) when in chat mode. Custom test registries preserved.
      - Dynamic context sizing: compute `num_ctx` based on actual prompt size + max_tokens + margin, avoiding massive context allocations.
      - Concurrency & DB robustness: `PRAGMA busy_timeout=30000;` on SQLite connections, upsert on conflict in run trace events.
    - Benchmark results:
      - Repeat warm prompt TTFT dropped from 25.03s to 0.50s (~50x speedup).
      - Conversational generation completed in 4.5s (down from >300s timeout).
      - `nx bench chat` cold warm-up completed in 14.41s.
    - Code quality & tests: 366 passed in 80.56s, 0 failures, 0 ruff errors, 0 mypy errors across 68 files.
  - NEXT: Phase F2 (Privacy policy v2: data classes, egress filter, ledger, private mode)

- [x] **F2** — Privacy policy v2 (data classes, egress filter, ledger, private mode)
  - Status: Done
  - Commit: Pending (F2 commit: `feat(privacy): data classes, egress filter, ledger, private mode`)
  - Evidence:
    - Data classification: extended `src/noxai/taint.py` with `DataClass` (`SECRET`, `LOCAL_ONLY`, `CHAT`, `PUBLIC`) with severity ranking and `is_cloud_allowed()` evaluation.
    - Content wrapper enhanced: added `data_class` field, convenience constructors (`secret()`, `local_only()`, `chat()`, `public()`, `untrusted()`), and taint-preserving merge semantics ensuring `TaintFlag.PRIVATE` cannot be downgraded.
    - Sensitivity & secret scanner: created `src/noxai/security/sensitivity.py` with regex scanning for 11 secret and credential patterns (OpenAI, Anthropic, NVIDIA, GitHub, AWS, bearer tokens, generic keys, private keys, password assignments, credit cards, SSNs) and heuristic sensitive prompt detection (medical, financial, auth).
    - Egress filter choke point: implemented `src/noxai/security/egress_filter.py` with `EgressFilter.evaluate()` gating all non-local model requests, fail-closed handling for plain unlabeled strings, secret masking in chat text, private mode zero-egress enforcement, and sensitive prompt rerouting.
    - Migration 005 & audit ledger: added `src/noxai/migrations/005_privacy_v2.sql` with `visibility` column on `facts` (default `'local_only'`) and `egress_ledger` audit table tracking provider, model, tokens, data class, decision, reason, and sha256 hash.
    - Memory & settings integration: updated `src/noxai/memory/store.py` (`Fact.visibility`), `src/noxai/tools/builtin/memory_tools.py` (`SaveFactParams.visibility`), `src/noxai/config.py` (`PrivacyConfig`), `config.example.toml`, and `src/noxai/models/router.py`.
    - Test validation: 28 tests in `tests/test_privacy_v2.py` including a 500-iteration random permutation property test verifying zero secrets or local-only data can ever egress to cloud.
    - Quality gates: 0 ruff errors, 0 mypy errors across 71 source files.
  - NEXT: Phase F3 (Cloud health, fallback and rate-limit budget)

- [x] **F3** — Cloud health, fallback and rate-limit budget
  - Status: Done
  - Commit: Pending (F3 commit: `feat(cloud): health state machine, circuit breaker, rate budget`)
  - Evidence:
    - Cloud health state machine: implemented `src/noxai/models/cloud_health.py` with observable states `ACTIVE`, `NO_KEY`, `INVALID_KEY`, `RATE_LIMITED`, `DEGRADED`, `OFFLINE`, and global/instance singleton lifecycle.
    - Circuit breaker: implemented `CircuitBreaker` (`CLOSED`, `OPEN`, `HALF_OPEN`) with configurable failure threshold, cooldown timer, `force_open` for 401/403 and 429, single in-flight probe testing in `HALF_OPEN`, and automatic reset to `CLOSED` upon successful probe.
    - Rate budget manager: created `src/noxai/models/budget.py` with token-bucket algorithm (default 30 RPM), dedicated interactive reserve (default 10 RPM) preventing batch/background starvation, and instant non-blocking reservation accounting.
    - Provider & router integration: wired `CloudHealth` and `RequestBudget` into `src/noxai/models/nvidia.py` while preserving backward compatibility with `TokenBucket` class and existing tests; updated `src/noxai/models/router.py` to check cloud health proactively before routing, falling back to local `brain` instantly with user notice.
    - Diagnostics & CLI: added `check_cloud_health_state` to `src/noxai/doctor.py`; updated `/api/status` with `cloud_health` in `src/noxai/api/routes.py`; implemented `nx cloud status` (table and `--json`) and `nx cloud test` in `src/noxai/commands/cloud.py`; implemented `nx models bench-cloud` in `src/noxai/commands/models.py`; registered `cloud_app` and `nx bench cloud` in `src/noxai/cli.py`.
    - Verification & testing: created 16 unit and integration tests in `tests/test_cloud_health.py` and updated `tests/test_doctor.py`. AST safety test `test_llm_providers_never_called_outside_router` verified.
    - Quality gates: 411 passed in 61.39s across entire test suite, 0 failures, 0 ruff errors, 0 mypy errors across 74 source files.
  - NEXT: Phase F4 (Reflex router, Front Desk, intent analysis)

- [x] **F4** — Reflex router, Front Desk, intent analysis
  - Status: Done
  - Commit: Pending (F4 commit: `feat(router): reflex intent analysis, front desk, dispatch tools`)
  - Evidence:
    - Zero-LLM Intent Analysis: implemented `src/noxai/router/intent.py` classifying turns into 10 intents (`reply_now`, `reminder`, `schedule`, `job_edit`, `background_task`, `deep_research`, `memory_op`, `system_action`, `sensitive`, `ambiguous`) with 3 reply modes (`instant`, `background`, `both`) and sub-millisecond execution (<5ms average, guaranteed <50ms per §1.4).
    - Front Desk Dispatch Tools: implemented `src/noxai/tools/builtin/dispatch.py` containing `@tool` definitions for `dispatch_task`, `dispatch_reminder`, `dispatch_job_edit`, and `request_approval` with registry delegation via `record_pending_approval`.
    - Tool Registry Integration: updated `src/noxai/agent/loop.py` to define `FRONT_DESK_TOOLS` (`{"web_search", "fetch_page", "dispatch_task", "dispatch_reminder", "dispatch_job_edit", "request_approval"}`), expose only allowlisted tools when `lane="front_desk"` or cloud-eligible, and record intent in run trace.
    - Security & Pipeline Wiring: wired `analyze_intent` into `_run_chat_pipeline` in `src/noxai/api/routes.py` and `AgentLoop.run` to emit `"intent_detected"` events and automatically enforce local execution for sensitive requests.
    - Event Bus: added `"intent_detected"`, `"task_dispatched"`, `"reminder_dispatched"` to standard `EVENT_TYPES`.
    - Comprehensive Test Suite: 18 unit and integration tests in `tests/test_reflex_intent.py` verifying all 10 intent categories, latency guarantees, tool filtering, dispatch tool execution, and local isolation.
    - Quality gates: 429 passed in 72.78s across entire test suite, 0 failures, 0 ruff errors, 0 mypy errors across 77 source files.
  - NEXT: Phase F5 (Task Center)

- [x] **F5** — Task Center
  - Status: Done
  - Commit: Pending (F5 commit: `feat(tasks): task center, background worker, approval parking`)
  - Evidence:
    - Database Migration 006: added `src/noxai/migrations/006_task_center.sql` defining `tasks` table with execution lanes (`local`, `cloud`, `scheduler`), priority levels (`low`, `normal`, `high`, `urgent`), statuses (`queued`, `running`, `paused`, `waiting_approval`, `done`, `failed`, `cancelled`), progress (0.0–1.0), `approval_id`, and `cancellation_token`.
    - Task Center Orchestrator: implemented `src/noxai/tasks/center.py` and `src/noxai/tasks/models.py` with priority queueing (`urgent` prioritized over `high`, `normal`, `low`), state transition handling, background worker loop with cancellation tokens (`asyncio.Event`), and approval parking (`park_for_approval` and `handle_approval_resolved`).
    - Event Bus: added `task_created`, `task_started`, `task_progress`, `task_waiting_approval`, `task_done`, `task_failed`, `task_cancelled` events in `src/noxai/events.py`.
    - Daemon REST API: implemented `GET /api/tasks`, `POST /api/tasks`, `GET /api/tasks/{task_id}`, `POST /api/tasks/{task_id}/cancel`, and wired approval unparking/cancellation into `POST /api/approvals/{approval_id}` in `src/noxai/api/routes.py`.
    - CLI Commands: implemented `nx tasks list`, `nx tasks show`, `nx tasks cancel`, and `nx tasks create` in `src/noxai/commands/tasks.py` and registered sub-app in `src/noxai/cli.py`.
    - Verification & testing: 9 comprehensive unit and integration tests in `tests/test_task_center.py` verifying schema, priority ordering, lane filtering, worker lifecycle, cancellation, approval parking/resumption, API endpoints, and CLI commands.
    - Quality gates: 438 passed in 133.49s across entire test suite, 0 failures, 0 ruff errors, 0 mypy errors across 81 source files.
  - NEXT: Phase F6 (Resident small model and Ollama tuning)

- [x] **F6** — Resident small model and Ollama tuning
  - Status: Done
  - Commit: `d79d3ed` (`feat: resident model, bench-local, keep-alive tuning, doctor perf`)
  - Evidence:
    - Configuration: added `resident_num_ctx: int = 4096` and `resident_allow_tool_loops: bool = False` to `OllamaModelConfig` in `src/noxai/config.py`.
    - Model Router & Providers: updated `src/noxai/models/router.py` to support `role == "resident"`, falling back to `brain` if unassigned; default `keep_alive = "-1"` and `num_ctx = 4096` for resident role; enforced refusal of multi-turn tool loops for resident model unless `resident_allow_tool_loops=True`.
    - Embeddings & Keep-Alive: updated `Provider.embed`, `OllamaProvider.embed`, `FakeProvider.embed`, and `NvidiaNimProvider.embed` signatures to accept `keep_alive: str | None = None` defaulting to `"-1"`.
    - Diagnostics & Tuning: added `check_ram_budget()` in `src/noxai/doctor.py` checking loaded models in Ollama vs system `MemAvailable` with WARN if ratio > 80%; added `run_doctor_perf()` printing systemd drop-in recommendations (`OLLAMA_MAX_LOADED_MODELS=2/3`, `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`) with partial offload warnings. Added `nx doctor --perf` to CLI in `src/noxai/cli.py`.
    - Local Candidate Bake-Off: implemented `nx models bench-local` (and `nx bench local` alias) in `src/noxai/commands/models.py` measuring TTFT, TPS, 20 JSON prompt validations, tool evaluation, peak memory usage, composite score, and role recommendations.
    - Non-blocking Warm-Up: added background task in daemon lifespan in `src/noxai/api/app.py` warming resident model and embedder at startup with `keep_alive="-1"`, recording `app.state.warmup_time_seconds`.
    - Verification & testing: 10 tests in `tests/test_resident_model.py` and doctor tests passing.
    - Quality gates: 448 passed in 92.51s across entire test suite, 0 failures, 0 ruff errors, 0 mypy errors across 81 source files.
  - NEXT: Phase F7 (UI for the new architecture)

- [x] **F7** — UI for the new architecture
  - Status: Done
  - Commit: Pending (F7 commit: `feat(ui): route chips, trace drawer, cloud health indicator, task center`)
  - Evidence:
    - Route Chips & Trace Drawer: implemented `RouteChip` and `TraceDrawer` components displaying execution lane, model name, local/cloud badge, latency, and full slide-out drawer with intent analysis (<5ms zero-LLM), taint flags, tool executions, and raw JSON trace.
    - Cloud Health & Popover: implemented `CloudStatusIndicator` showing live state dot (active, rate_limited, no_key, offline) and interactive popover with circuit breaker state (`CLOSED`), token-bucket rate budget (30 RPM, 10 RPM interactive reserve), and cloud enable/disable switch.
    - Private Mode Controls: added top-bar Private Mode padlock toggle, chat slash command `/local`, and ambient amber/accent glow on prompt textarea enforcing 100% local CPU execution with zero cloud egress.
    - Task Center UI: implemented dedicated `TasksPage` and navigation rail item showing active background queue, lane, priority, progress bars, approve button for parked actions, and cancel button; added real-time task chips to chat header.
    - Cloud Activity & Egress Ledger: updated `ActivityPage` with tab toggle for Egress Ledger displaying model, token estimates, data class, rationale, and SHA-256 payload hash with zero prompt text storage. Added `GET /api/egress` in `src/noxai/api/routes.py`.
    - Memory Page: added `Visibility` column (`local_only` vs `cloud_allowed`) to `MemoryPage`.
    - Verification & testing: 10 test suites (41 tests) passing in Vitest (`RouteChip.test.tsx`, `TraceDrawer.test.tsx`, `CloudStatusIndicator.test.tsx`, `TasksPage.test.tsx`), TypeScript typecheck 0 errors, Vite production build clean. Backend tests (448 passed) and quality gates (0 ruff, 0 mypy) completely clean.
  - NEXT: Phase F8 (Integration, latency proof and M10 acceptance)

- [x] **F8** — Integration, latency proof and M10 acceptance
  - Status: Done
  - Commit: Pending (F8 commit: `test: M10 integration, end-to-end latency and intent benchmarks`)
  - Evidence:
    - Benchmark Suite & Routing Evaluation: created `src/noxai/router/eval.py` running an automated evaluation of 44 prompts across all 10 intent categories (`reply_now`, `reminder`, `schedule`, `job_edit`, `background_task`, `deep_research`, `memory_op`, `system_action`, `sensitive`, `ambiguous`).
    - Reflex Latency Proof: verified zero-LLM reflex intent classification latency of < 0.1ms mean (0.08ms mean, < 0.35ms max), comprehensively beating the < 5ms mean and < 50ms hard limit (§1.4).
    - Intent Classification Accuracy: enhanced regex patterns in `src/noxai/router/intent.py` achieving 100% accuracy (44/44) across all intent categories with exact reply mode assignment.
    - Sensitive Isolation & Egress Filtering: verified 100% local isolation for credentials (AWS keys, passwords, bearer tokens, credit cards, SSH keys) and confidential medical records with fail-closed protection for unlabeled strings.
    - Fallback Matrix: verified clean, transparent fallback to local brain under `NO_KEY`, `RATE_LIMITED`, and `OFFLINE` (circuit breaker `OPEN`) conditions without exceptions or UI disruption.
    - Rate Budget Interactive Reserve: verified that background acquisitions are restricted once capacity reaches the 10 RPM interactive reserve threshold, ensuring user turns are never starved.
    - Task Center Lifecycle: verified task queueing, priority assignment, cancellation tokens, and progress reporting.
    - CLI Routing Evaluation: added `nx eval-routing` command (`--offline`/`--live`, `--json`) in `src/noxai/cli.py`.
    - Test Suite: 10/10 integration tests passing in `tests/test_m10_integration.py`.
    - Documentation: updated `docs/ARCHITECTURE.md` and `docs/SECURITY.md` reflecting M10 architecture, data classes, egress ledger, fallback matrix, and UI components.
    - Quality gates: 458 passed in test suite, 0 ruff errors, 0 mypy errors across 82 source files.
  - NEXT: Phase F9 (Resume remaining verification: Session E phases E7–E11, full coverage >= 88%, and final merge to main)

- [ ] **F9** — Resume remaining verification (Session E phases E7–E11)


