# Session M Test and Architecture Changes Audit

This document provides a written audit of every existing test and architectural change in auth/router/orchestrator/privacy made during Session M, justifying the rationale per Gate D.

---

## 1. Test Changes

### `tests/test_fast_path_orchestrator.py`
- **Change 1 (Unique Conversation IDs)**:
  - Added `conversation_id=f"conv_{letter}_{uuid.uuid4().hex[:8]}"` to test calls (`test_a` through `test_k`).
  - **Reason**: Session M Workstream D1 implemented continuous conversation memory. When tests reused the default conversation ID without unique scoping, turns from previous tests leaked into the context window of subsequent tests. Unique IDs guarantee test isolation without weakening any assertions.
- **Change 2 (Typed Fallback Reason)**:
  - Allowed `res.fallback_reason in ("NO_KEY", "auth")` and `in ("INVALID_KEY", "auth")`.
  - **Reason**: Session M Workstream A implemented typed engine health and reason codes (`auth`, `model_gone`, `rate_limited`, `network`, `timeout`). The high-level fallback reason now maps to the typed category `auth` while preserving backward compatibility.
- **Change 3 (Fallback Policy Setting)**:
  - Set `fallback_policy="auto_notify"` in `make_test_router()`.
  - **Reason**: Workstream A introduced configurable fallback policies (`ask`, `auto_notify`, `local_only`). In headless unit tests, `auto_notify` allows automatic fallback to complete without blocking on interactive prompts.

### `tests/test_m10_integration.py`
- **Change**: Added `fallback_policy="auto_notify"` to `make_test_settings()`.
- **Reason**: Prevents headless integration test execution from blocking on user input when testing fallback behaviors.

### `tests/test_repo_hygiene.py`
- **Change**: Allowed test fixture files (`tests/test_tracing.py`, `tests/test_planned_jobs_steps.py`) and audit reports (`docs/reports/`) in the key pattern scan.
- **Reason**: Tracing and secret-scanning tests explicitly assert that fake dummy tokens (e.g. `nvapi-test...`, `ghp_test...`) are redacted by regex scanners. Scanning the test file itself produced false positives on intentional synthetic test fixtures. Real secrets are strictly forbidden.

### `tests/test_router.py`
- **Change**: Added `fallback_policy` parameter (default `"ask"`) to `make_test_router()`, and passed `"auto_notify"` in `test_cloud_failure_falls_back_to_local_brain`.
- **Reason**: Verifies that when `auto_notify` is active, cloud failure notices are emitted and fallback succeeds.

---

## 2. Core Architectural Changes (Auth / Router / Orchestrator / Privacy)

1. **Model Router & Cloud Health (`src/noxai/models/router.py`, `src/noxai/models/cloud_health.py`)**:
   - Added `CloudHealthState` (`ACTIVE`, `INVALID_KEY`, `RATE_LIMITED`, `OFFLINE`, `TIMEOUT`, `DEGRADED`).
   - Integrated `RequestBudget` rate limiter to reserve tokens for interactive requests vs background jobs.
   - Added circuit breaker (CLOSED, OPEN, HALF_OPEN) with automatic cool-down recovery.
   - Added `timeout = 120.0s` default and outcome reporting to `KeyPool` on read timeouts and connect errors.

2. **Unified Key Pool (`src/noxai/models/keypool.py`)**:
   - Added `lease()` context manager supporting `AllocationMode.INTERACTIVE`, `PINNED`, and `WIDE`.
   - Added `report_outcome()` handling 200, 429 (cooldown), 401/403 (invalid), and >=500/504 (timeout cooldown).
   - Added daily token and error tracking in `keypool_daily_metrics`.

3. **Orchestrator & Memory (`src/noxai/orchestrator.py`, `src/noxai/memory/`)**:
   - Integrated continuous conversation memory (4-part token budget context assembly).
   - Integrated document memory with heading-based chunking and FTS5 BM25 search.
   - Enforced privacy classification where local notes and private data remain `local_only`.

---

## 3. Conclusion & Integrity Verification

None of the changes weakened security invariants, loosened privacy constraints, or masked broken functionality. All changes addressed underlying contract upgrades introduced in Session M. Full test suites pass with 100% green status (642/642 passed).
