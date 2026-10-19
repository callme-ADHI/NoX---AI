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

- [ ] **F4** — Reflex router, Front Desk, intent analysis
- [ ] **F5** — Task Center
- [ ] **F6** — Resident small model and Ollama tuning
- [ ] **F7** — UI for the new architecture
- [ ] **F8** — Integration, latency proof and M10 acceptance
- [ ] **F9** — Resume remaining verification (Session E phases E7–E11)
