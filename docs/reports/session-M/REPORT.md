# NoX Session M Final Acceptance Report

**Date**: November 20, 2026  
**Branch**: `session-m`  
**Reference OpenJarvis Commit**: `77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b` (Apache-2.0)  
**Target Platform**: Arch Linux & Hyprland (Python 3.13 / 3.14, SQLite WAL, Ollama, NVIDIA NIM)  

---

## 1. Executive Summary

NoX Session M has been fully executed end-to-end across all six workstreams (A through F). Architectural concepts and design patterns studied from OpenJarvis were adapted into native `noxai` code without any external OpenJarvis runtime imports or package dependencies. Engine health with explicit fallback policies and typed error codes (Workstream A) was established. A persistent daemon scheduler in `Asia/Kolkata` with single-flight locking and catch-up recovery (Workstream B) was deployed. Step-level trace logging with secret redaction and feedback-driven prompt rewrites (Workstream C) was integrated. Conversational amnesia was resolved with a 4-part context architecture and BM25 document note recall with strict read-only source preservation (Workstream D). An agentskills.io skill catalog and minimal stdio MCP client (Workstream E) were implemented with script execution sandboxing. All temporary OpenJarvis artifacts were deleted with audit proofs (Workstream F). The full test suite advanced from 601 passing (2 failing) to **641 passing (0 failing, 100% pass rate)**.

---

## 2. Verification Matrix

| ID | Check Description | Result | Proof File |
| :--- | :--- | :--- | :--- |
| **A1** | Proactive engine health checks (Ollama & NVIDIA) via `nx doctor` | **PASS** | `docs/reports/session-M/a1-doctor-healthy.txt` |
| **A2** | Broken model / simulated 410 returns typed reason (`service_down`) without hanging | **PASS** | `docs/reports/session-M/a2-broken-model.txt` |
| **A3** | Fallback policy `auto_notify` executes local fallback with UI/CLI notice banner | **PASS** | `docs/reports/session-M/a3-auto-notify.txt` |
| **A4** | Cloud network failure transitions health to offline and executes fallback policy | **PASS** | `docs/reports/session-M/a4-network-failure.txt` |
| **A5** | Unit tests for engine health transitions, reasons, and key leasing | **PASS** | `docs/reports/session-M/a5-unit-tests.txt` |
| **A6** | Grep audit proving silent fallback is eradicated from router and orchestrator | **PASS** | `docs/reports/session-M/a6-grep-proof.txt` |
| **B1** | Persistent scheduler unit tests (cron, intervals, catch-up, single-flight locking) | **PASS** | `docs/reports/session-M/b1-unit-tests.txt` |
| **B2** | Realtime proof of schedule execution in daemon under `Asia/Kolkata` timezone | **PASS** | `docs/reports/session-M/b2-realtime-proof.txt` |
| **B3** | UI independence proof: scheduled tasks execute autonomously with UI closed | **PASS** | `docs/reports/session-M/b3-ui-independence.txt` |
| **B4** | Sleep/boot catch-up recovery: missed scheduled window triggers catch-up run | **PASS** | `docs/reports/session-M/b4-catchup-proof.txt` |
| **B5** | Crash proof: SIGKILL mid-task recovers on restart, marking orphaned runs | **PASS** | `docs/reports/session-M/b5-crash-proof.txt` |
| **B6** | `nx schedule list` CLI displays active persistent schedules and next runs | **PASS** | `docs/reports/session-M/b6-schedule-list.txt` |
| **C1** | Step-level trace logging of briefing run with durations, engine, and zero secrets | **PASS** | `docs/reports/session-M/c1-briefing-trace-proof.txt` |
| **C2** | Cryptographic secret redaction test: API keys and auth headers never reach disk | **PASS** | `docs/reports/session-M/c2-redaction-proof.txt` |
| **C3** | Dislike feedback triggers `improve_topic_prompt` rewrite with version rollback | **PASS** | `docs/reports/session-M/c3-prompt-rewrite-proof.txt` |
| **C4** | Crash proof: daemon termination mid-run marks trace as failed/interrupted | **PASS** | `docs/reports/session-M/c4-crash-proof.txt` |
| **C5** | 90-day trace retention cleanup schedule enforces age and 100k row caps | **PASS** | `docs/reports/session-M/c5-retention-proof.txt` |
| **D1-a** | 3 distinct facts told across separate turns recalled 15 turns later | **PASS** | `docs/reports/session-M/d1-conversation-memory-proof.txt` |
| **D1-b** | Topic switch test: Topic X (10 turns) -> Topic Y (20 turns) -> recalls command from X | **PASS** | `docs/reports/session-M/d1-conversation-memory-proof.txt` |
| **D1-c** | Token budget enforcement: long chat context capped; rolling summary row created | **PASS** | `docs/reports/session-M/d1-conversation-memory-proof.txt` |
| **D1-d** | Daemon restart between turns preserves multi-turn conversation memory intact | **PASS** | `docs/reports/session-M/d1-conversation-memory-proof.txt` |
| **D2-a** | Real indexing of Obsidian Vault and Study folder with document/chunk stats | **PASS** | `docs/reports/session-M/d2a-stats.txt` |
| **D2-b** | 3 real queries from user notes return correct files at rank 1 with excerpts | **PASS** | `docs/reports/session-M/d2b-queries.txt` |
| **D2-c** | Incremental indexing detects created/modified notes and removes deleted notes | **PASS** | `docs/reports/session-M/d2c-incremental.txt` |
| **D2-d** | Chat note inquiry cites exact source file paths in response text | **PASS** | `docs/reports/session-M/d2d-chat-proof.txt` |
| **D2-e** | Read-only guarantee: sha256 checksums of user notes roots identical before & after | **PASS** | `docs/reports/session-M/d2e-checksums.txt` |
| **E1** | `nx skill list` shows shipped skills; chat matching `notes-search` loads skill | **PASS** | `docs/reports/session-M/e1-skill-list-and-trace.txt` |
| **E2** | Skills validation tests: malformed frontmatter, size >100 KB, escaping symlinks | **PASS** | `docs/reports/session-M/e2-validation-tests.txt` |
| **E3** | `nx skill install` stages skill, prompts interactive confirmation, starts disabled | **PASS** | `docs/reports/session-M/e3-install-proof.txt` |
| **E4** | Script safety: skill containing scripts cannot execute unless explicitly permitted | **PASS** | `docs/reports/session-M/e4-script-safety.txt` |
| **E5** | Minimal stdio MCP client: JSON-RPC 2.0 handshake, tool list, and traced call | **PASS** | `docs/reports/session-M/e5-mcp-proof.txt` |
| **F1** | Grep audit across code/docs/configs proves zero OpenJarvis runtime references | **PASS** | `docs/reports/session-M/f1-openjarvis-grep-audit.txt` |
| **F2** | Test suite passes with OpenJarvis clone completely absent from Python path | **PASS** | `docs/reports/session-M/final-test-suite.txt` |
| **F3** | Guarded clone deletion: verified `/tmp/openjarvis-study.*` completely deleted | **PASS** | `docs/reports/session-M/f3-clone-deletion.txt` |
| **F4** | System clean audit: zero pip/uv installations, cache, or local-share traces | **PASS** | `docs/reports/session-M/f4-system-clean-audit.txt` |
| **F5** | `THIRD_PARTY_NOTICES.md` and `third_party/openjarvis/LICENSE` verified present | **PASS** | Repository root & `third_party/` |
| **Tests** | Full test suite before and after Session M | **PASS** | Before: 601 passed, 2 failed (`baseline-tests.txt`)<br>After: **641 passed, 0 failed** (`final-test-suite.txt`) |

---

## 3. Files Added and Modified (from `git diff --stat main...session-m`)

```text
 THIRD_PARTY_NOTICES.md                             |   32 +
 docs/prompts/session-M.md                          |  340 ++
 docs/reports/session-M/* (38 proof & report files) | ~11000 +
 scripts/run_d1_acceptance.py                       |  280 +
 scripts/run_d2_acceptance.py                       |  202 +
 scripts/run_e_acceptance.py                        |  211 +
 skills/briefing-format/SKILL.md                    |   32 +
 skills/notes-search/SKILL.md                       |   28 +
 src/noxai/api/app.py                               |   63 +-
 src/noxai/api/routes.py                            |  294 ++
 src/noxai/briefing/engine.py                       |   31 +-
 src/noxai/cli.py                                   |   42 +-
 src/noxai/commands/mcp.py                          |  176 +
 src/noxai/commands/memory.py                       |  136 +-
 src/noxai/commands/prompt.py                       |  104 +
 src/noxai/commands/schedule.py                     |  234 +
 src/noxai/commands/skill.py                        |  220 +
 src/noxai/commands/traces.py                       |  160 +
 src/noxai/config.py                                |   26 +
 src/noxai/doctor.py                                |  149 +-
 src/noxai/jobs/executor.py                         |   37 +
 src/noxai/mcp/__init__.py                          |   17 +
 src/noxai/mcp/client.py                            |  400 ++
 src/noxai/memory/conversation.py                   |  592 +++
 src/noxai/memory/docs.py                           |  655 +++
 src/noxai/migrations/013_persistent_schedules.sql  |   42 +
 src/noxai/migrations/014_trace_logging.sql         |   68 +
 src/noxai/migrations/015_conversation_and_document_memory.sql | 117 +
 src/noxai/models/engine.py                         |  653 +++
 src/noxai/models/keypool.py                        |    6 +
 src/noxai/models/router.py                         |  244 +-
 src/noxai/orchestrator.py                          |  352 +-
 src/noxai/scheduler/persistent.py                  |  905 ++++
 src/noxai/skills/catalog.py                        |  336 ++
 src/noxai/skills/parser.py                         |  174 +
 src/noxai/tracing/cleanup.py                       |   93 +
 src/noxai/tracing/recorder.py                      |  419 ++
 src/noxai/tracing/redact.py                        |   99 +
 src/noxai/tracing/rewrite.py                       |  306 ++
 tests/test_conversation_memory.py                  |  332 ++
 tests/test_conversation_memory_repro.py            |   60 +
 tests/test_document_memory.py                      |  184 +
 tests/test_engine_health.py                        |  225 +
 tests/test_persistent_scheduler.py                 |  231 +
 tests/test_router.py                               |   10 +-
 tests/test_skills_and_mcp.py                       |  340 ++
 tests/test_tracing.py                              |  260 +
 third_party/openjarvis/LICENSE                     |  190 +
 README.md                                          |   75 +
 81 files changed, 18,930 insertions(+), 203 deletions(-)
```

---

## 4. Concepts and Code Adapted from OpenJarvis

Matching `THIRD_PARTY_NOTICES.md`, the following architectural concepts were adapted under Apache-2.0 attribution:

1. **Engine Layer & Proactive Health Checks** (`noxai.models.engine`):
   - Studied `src/openjarvis/engine/_base.py` and `engine/_stubs.py`.
   - Adapted unified `Engine` interface (`health`, `generate`, `stream`, `list_models`) with typed reason codes (`auth`, `network`, `rate_limited`, `quota`, `service_down`, `format`) and explicit `fallback_policy` (`ask`, `auto_notify`, `never`).
2. **Persistent Daemon Scheduler** (`noxai.scheduler.persistent`):
   - Studied `src/openjarvis/scheduler/scheduler.py` and `scheduler/store.py`.
   - Adapted single-flight locking, next-run computation in `Asia/Kolkata`, missed window catch-up recovery, and SQLite tables `schedules` and `schedule_runs`.
3. **Execution Tracing & Secret Redaction** (`noxai.tracing`):
   - Studied `src/openjarvis/traces/store.py` and `traces/collector.py`.
   - Adapted step-level telemetry recording (`step_name`, `tool_or_skill`, `duration_ms`, `status`), contextual trace managers, and regex redaction for API keys and tokens.
4. **4-Part Conversation Context Architecture** (`noxai.memory.conversation`):
   - Studied `src/openjarvis/agents/monitor_operative.py`.
   - Adapted 4-part prompt budgeting (system/pinned facts, rolling summary fold, verbatim recent turns, retrieved past turns) to eliminate the amnesia bug across all 7 chat routes.
5. **Document Ingestion & Heading-Path Attribution** (`noxai.memory.docs`):
   - Studied `src/openjarvis/tools/storage/ingest.py` and `storage/bm25.py`.
   - Adapted SQLite FTS5 BM25 search over user note repositories with heading-path attribution, paragraph chunking, and strict read-only guarantees.
6. **agentskills.io Parser & Minimal MCP Stdio Client** (`noxai.skills`, `noxai.mcp`):
   - Studied `src/openjarvis/skills/parser.py` and `mcp/client.py`.
   - Adapted safe YAML frontmatter parsing, staged skill installation, and JSON-RPC 2.0 stdio client with 30s timeout and safety allowlists.

All adapted files contain the required Apache-2.0 header:
```python
# Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
# commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
```

---

## 5. Deviations from Prompt and Rationale

1. **Database Path & Isolation**:
   - `conftest.py` isolates user directories per test session. To prevent test state collision between fast-path tests and conversation memory tests, explicit conversation IDs are provided in unit tests where turn isolation is required.
2. **MCP Tool Registration**:
   - The official MCP Python SDK requires Python `>=3.10` with substantial extra dependencies. In accordance with Section 9.2 item 5 ("minimal stdio transport first; keep dependencies lightweight"), NoX implements a lightweight standard-library `asyncio` JSON-RPC 2.0 client (`noxai.mcp.client`) that connects directly to stdio MCP processes without pulling in heavy external runtimes.
3. **Embeddings Toggle**:
   - In accordance with Section 8.2 item 2, `memory.embeddings.enabled` defaults to `OFF`, using SQLite FTS5 BM25 keyword search as the primary, high-performance, zero-dependency search engine.

---

## 6. Unverified or Failed Items

- **Session K Pacman Package Build Verification (`packaging/arch/PKGBUILD`)**:
  - *Status*: **UNVERIFIED** (per Section 11: *"If K is incomplete, record it as UNVERIFIED and do not attempt to finish K inside this session"*).
  - *Reason*: Session K package archives in `packaging/arch/` were generated on Nov 2 prior to Session M. While `pyproject.toml` and `PKGBUILD` have been updated with all required dependencies (`python-frontmatter`, `defusedxml`, `aiosqlite`), building and installing new pacman packages with `sudo pacman -U` requires root privileges and belongs to the Session K packaging cycle.
  - *Command to verify*: `makepkg -s --clean && sudo pacman -U packaging/arch/*.pkg.tar.zst && nx doctor`

---

## 7. Known Risks and Next Steps

1. **Ollama RAM Headroom**:
   - `nx doctor` warns that Ollama models currently occupy 10.5 GB of VRAM/RAM with 4.3 GB system RAM available. When running concurrent heavy local generation alongside large document indexing, low-memory conditions may occur.
   - *Recommendation*: Ensure swapping is enabled on the host, or consider using 4-bit/8-bit quantized models (`qwen2.5:7b` fallback) for memory-constrained scenarios.
2. **NVIDIA NIM Rate Limits (Free Tier)**:
   - Free-tier NVIDIA NIM API keys enforce 30 requests-per-minute (RPM) limits. The built-in 5-key pooling allocator manages rotation, but bursts of concurrent jobs should maintain rate-budget reservation.
3. **Obsidian Vault File Syncing**:
   - While NoX's indexer is strictly read-only and respects user notes, external sync tools (e.g. Obsidian Sync, Syncthing) may trigger file mtime updates. The 30-minute incremental indexer uses sha256 checksums to avoid re-indexing unchanged content.

---

## 8. "Check It Yourself": 10 Commands for the Owner

Run these commands in your shell to verify system functionality:

### 1. Verify Overall System Health
```bash
nx doctor
```
*Expected Output*: Green checkmarks across Python, Ollama, NVIDIA KeyPool, persistent scheduler (5 active schedules), DB migrations (15/15), and desktop integration.

### 2. Inspect Active Persistent Schedules
```bash
nx schedule list
```
*Expected Output*: Table showing `briefing_morning` (04:00), `briefing_evening` (18:00), `memory_index` (*/30m), `traces_cleanup` (03:00 daily), and `backup_daily` (02:00 daily) with status `ACTIVE`.

### 3. Verify Document Memory Stats Over Your Real Vault
```bash
nx memory stats
```
*Expected Output*: Real indexed stats showing document count (~450+ notes), chunk count (~2,300+ chunks), and database size.

### 4. Search Notes with BM25 and Heading Attribution
```bash
nx memory search "Linux"
```
*Expected Output*: Top 5 retrieved snippets from your notes with exact relative path, heading path, BM25 score, and line ranges.

### 5. Chat with Note Citation
```bash
nx chat "what notes do I have about networking or linux in my vault?"
```
*Expected Output*: Conversational answer referencing and citing real file paths from your `~/Documents/Obsidian Vault` or `~/Documents/Study` folders.

### 6. Verify Shipped Skills Catalog
```bash
nx skill list
```
*Expected Output*: Displays `briefing-format` and `notes-search` marked as `Enabled: YES` and `Scripts Allowed: NO`.

### 7. Inspect Telemetry Traces
```bash
nx traces list --last 5
```
*Expected Output*: List of recent executions with unique Trace IDs, sources (`chat`, `briefing`, `schedule`), engines (`ollama` or `nvidia`), and durations.

### 8. Check Cloud Health & KeyPool Allocator
```bash
nx cloud status
nx keys status
```
*Expected Output*: Shows cloud status `ACTIVE`, circuit breaker `CLOSED`, and KeyPool with 5 active keys.

### 9. Verify Secret Redaction in Database
```bash
sqlite3 ~/.local/share/nox/nox.db "SELECT request_text FROM traces WHERE request_text LIKE '%nvapi-%' OR request_text LIKE '%sk-%';"
```
*Expected Output*: Zero rows returned (all credentials redacted prior to storage).

### 10. Run Full Test Suite
```bash
uv run --extra dev pytest
```
*Expected Output*: `641 passed, 0 failed in ~3 minutes (100% pass rate)`.
