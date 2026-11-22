# Phase 0 Recon: NoX Codebase Audit

**Date**: 2026-11-15  
**Branch**: `session-m`  
**Author**: NoX Senior Staff Engineer

---

## 1. Repo Layout

Two-level directory tree (excluding `.git`, `node_modules`, `.venv`, `target`, `__pycache__`):

```
.
├── CHANGELOG.md
├── Makefile
├── README.md
├── config.example.toml
├── desktop/
│   ├── package.json
│   ├── src/
│   └── src-tauri/
├── docs/
│   ├── ARCHITECTURE.md
│   ├── DECISIONS.md
│   ├── INSTALL.md
│   ├── PROGRESS.md
│   ├── SECURITY.md
│   ├── SPEC.md
│   ├── prompts/
│   └── reports/
├── hyprland/
│   └── nox.conf.snippet
├── packaging/
│   └── arch/
├── programs.example.toml
├── pyproject.toml
├── scripts/
│   ├── install.sh
│   ├── nx-open.sh
│   ├── probe_chat.py
│   ├── run_proof_d.py
│   └── verify-install.sh
├── src/
│   └── noxai/
│       ├── __init__.py
│       ├── __main__.py
│       ├── agent/
│       ├── api/
│       ├── briefing/
│       ├── cli.py
│       ├── commands/
│       ├── config.py
│       ├── db.py
│       ├── devices/
│       ├── doctor.py
│       ├── events.py
│       ├── evals/
│       ├── integrations/
│       ├── jobs/
│       ├── lock.py
│       ├── logging_setup.py
│       ├── memory/
│       ├── migrations/
│       ├── models/
│       ├── notes/
│       ├── orchestrator.py
│       ├── prompts/
│       ├── research/
│       ├── router/
│       ├── scheduler/
│       ├── security/
│       ├── skills/
│       ├── taint.py
│       ├── tasks/
│       └── tools/
├── systemd/
│   └── noxd.service
├── tests/
│   ├── test_approval_flow.py
│   ├── test_briefing_phase4_5.py
│   ├── test_cli.py
│   ├── test_config.py
│   ├── test_daemon_lifecycle.py
│   ├── test_db.py
│   ├── test_gate.py
│   ├── test_jobs.py
│   ├── test_memory.py
│   ├── test_nvidia.py
│   ├── test_ollama.py
│   ├── test_orchestrator.py
│   ├── test_planned_jobs_catchup.py
│   ├── test_router.py
│   ├── test_taint.py
│   └── ... (42 test suites total)
├── ui/
│   ├── index.html
│   ├── package.json
│   ├── src/
│   ├── tailwind.config.js
│   ├── tsconfig.json
│   └── vite.config.ts
└── uv.lock
```

### Component Locations:
1. **Daemon entry point**: `src/noxai/__main__.py:main()` (registered script `noxd`) and `src/noxai/api/app.py:main()`.
2. **CLI**: `src/noxai/cli.py` (registered script `nx` using Typer & Rich).
3. **Web API server**: `src/noxai/api/app.py` (FastAPI app factory `create_app()`) and `src/noxai/api/routes.py` (REST endpoints).
4. **Tauri shell**: `desktop/src-tauri/` (Rust Tauri v2 wrapper + tray icon).
5. **Agent loop**: `src/noxai/agent/loop.py` (react tool execution loop) and `src/noxai/orchestrator.py` (`orchestrate_chat_turn`).
6. **Model/engine client code**: `src/noxai/models/` (`base.py`, `nvidia.py`, `ollama.py`, `router.py`, `cloud_health.py`).
7. **Key pool**: `src/noxai/models/key_pool.py` and `src/noxai/models/keypool.py` (leasing, RPM limits, cooldowns).
8. **Intent check**: `src/noxai/router/intent.py` (`analyze_intent`).
9. **Briefing code**: `src/noxai/briefing/` (`engine.py`, `scheduler.py`, `feedback.py`).
10. **Job/task system**: `src/noxai/jobs/` (planner, executor, steps, watchdog, catchup, deletion) and `src/noxai/tasks/` (center, supervisor, models).
11. **Memory code**: `src/noxai/memory/` (`context.py`, `store.py`, `folding.py`, `embed.py`, `vector.py`, `recall.py`).
12. **Config loader**: `src/noxai/config.py` (`get_config()`, `Settings`, TOML + Pydantic v2).
13. **Notification code**: `src/noxai/scheduler/delivery.py` (`DesktopDeliveryChannel` via `notify-send`) and `src/noxai/jobs/steps.py` (`step_notify`).
14. **Tests**: `tests/` (603 collected pytest tests).

---

## 2. Language, Python Version & Dependencies

- **Language**: Python 3 (running Python 3.14.10 on Arch Linux; `requires-python = ">=3.11"`).
- **Frontend / Desktop**: TypeScript + React 18 + Vite + Tailwind CSS (`ui/`), Rust + Tauri 2 (`desktop/src-tauri/`).
- **Dependency Manager**: `uv` (managed with `pyproject.toml` and `uv.lock`, Hatchling build backend).
- **Direct Python Dependencies**:
  - `fastapi>=0.115.0`
  - `uvicorn[standard]>=0.32.0`
  - `websockets>=13.1`
  - `pydantic>=2.10.0`
  - `pydantic-settings>=2.6.0`
  - `httpx>=0.28.0`
  - `typer>=0.15.0`
  - `rich>=13.9.0`
  - `aiosqlite>=0.20.0`
  - `croniter>=5.0.0`
  - `dateparser>=1.2.0`
  - `ddgs>=7.2.0`
  - `trafilatura>=2.0.0`
  - `feedparser>=6.0.11`
  - `psutil>=6.1.0`
  - `ollama>=0.4.0`
  - `openai>=1.54.0`
  - `numpy>=2.2.0`
  - `defusedxml>=0.7.1`
  - `python-frontmatter>=1.3.0`

---

## 3. Configuration & Secrets

- **Config File**: `~/.config/nox/config.toml` (parsed with Python stdlib `tomllib`, validated via Pydantic v2 `_StrictModel` rejecting unknown keys).
- **Secrets File**: `~/.config/nox/secrets.env` (must have strict permissions `0600`; parsed as simple `KEY=VALUE` pairs without shell expansion).
- **NVIDIA API Keys Location**: Stored strictly in `~/.config/nox/secrets.env` under `NVIDIA_API_KEY` (or indexed keys `NVIDIA_API_KEY_1`, `NVIDIA_API_KEY_2`, etc. for key pool). Never committed to git or printed to logs.

---

## 4. Storage & SQLite Schema

NoX persists data in:
- SQLite Database: `~/.local/share/nox/nox.db` (WAL mode, foreign keys ON).
- Single Instance Lock: `~/.local/share/nox/noxd.lock`.
- Stored Briefings: `~/.local/share/nox/briefings/` (Markdown and JSON artifacts).

### Existing SQLite Tables:
1. `approvals`
2. `audit_log`
3. `briefing_feedback`
4. `briefing_items`
5. `briefing_runs`
6. `briefing_staging`
7. `briefing_topic_runs`
8. `briefing_topics`
9. `conv_state`
10. `conv_summaries`
11. `conversations`
12. `egress_ledger`
13. `episodes`
14. `facts`
15. `facts_history`
16. `job_plans`
17. `job_run_steps`
18. `job_runs`
19. `job_versions`
20. `jobs`
21. `keypool_daily_metrics`
22. `messages`
23. `msg_chunks`
24. `notifications`
25. `opportunity_registry`
26. `outputs`
27. `preferences`
28. `prompt_versions`
29. `runs`
30. `safety_whitelist`
31. `schema_migrations`
32. `seed_state`
33. `seen_items`
34. `settings`
35. `skill_versions`
36. `skills_index`
37. `sources`
38. `task_nodes`
39. `tasks`
40. `fts_episodes*`, `fts_facts*`, `fts_msg_chunks*` (FTS5 virtual tables)
41. `vec_episodes`, `vec_facts` (vector blob storage)

---

## 5. Engines & Silent Fallback Analysis

### Calling Mechanisms:
- **NvidiaProvider** (`src/noxai/models/nvidia.py`): Uses `openai.AsyncOpenAI` pointing to `base_url="https://integrate.api.nvidia.com/v1"` with API key leased from `KeyPool`.
- **OllamaProvider** (`src/noxai/models/ollama.py`): Uses `ollama.AsyncClient` pointing to `base_url="http://127.0.0.1:11434"`.

### Silent Fallback Code Location:
- **File**: `src/noxai/models/router.py`
- **Method**: `ModelRouter.chat()`
- **Line Range**: lines 334–385:
```python
334: except (ProviderUnavailable, RateLimitError, ProviderError, Exception) as exc:
335:     err_str = str(exc)
336:     status_code = getattr(exc, "status_code", None)
...
354:     local_role = "brain" if tools else "resident"
355:     log.warning(
356:         "Cloud role '%s' failed (%s). Falling back to local %s role.",
357:         role, exc, local_role,
358:     )
361:     fallback_msg = f"Cloud role '{role}' failed ({exc}). Falling back to local model."
...
365:     fallback_provider, fallback_model = self.resolve_role(local_role)
378:     async for chunk in fallback_provider.chat(...)
```
And lines 271–308:
```python
272: cloud_health = getattr(provider, "cloud_health", None)
273: if cloud_health is not None and not cloud_health.is_active():
...
281:     local_role = "brain" if tools else "resident"
282:     local_provider, local_model = self.resolve_role(local_role)
297:     async for chunk in local_provider.chat(...)
```

**Why it degraded silently**:
When NVIDIA returned HTTP 410 (model decommissioned), `ModelRouter.chat()` caught `Exception`, logged a warning, and immediately rerouted the entire chat generation stream to Ollama without user consent or policy check. Since the local model runs at ~2 tokens/sec on CPU, the user experienced a 20+ second freeze with no explicit fallback notification or choice.

---

## 6. Scheduling & UI Coupling

### Current Triggering:
- **Briefings**: Managed by `BriefingScheduler` (`src/noxai/briefing/scheduler.py`) started during `noxd` daemon lifespan in `src/noxai/api/app.py:297` (`app.state.briefing_scheduler = briefing_sched`). Slots are `04:00` and `18:00 Asia/Kolkata`.
- **Jobs / Backup**: Managed by `JobScheduler` (`src/noxai/jobs/scheduler.py`) started in `src/noxai/api/app.py:289`. Reads cron jobs from the `jobs` table (e.g. `job_06725cb80054` for BackUp at `0 20 * * *`).

### Why runs get cancelled or fail:
1. **API route instantiating new unmanaged schedulers**: In `src/noxai/api/routes.py:1340-1346`, if `request.app.state.briefing_scheduler` was None or accessed via direct route fallback, a new unstarted `BriefingScheduler()` was created on the fly.
2. **WebSocket & UI Lifecycle**: In `src/noxai/api/routes.py:481-535`, chat runs were tied to an in-memory `active_runs` dictionary in `routes.py`. When the UI disconnected or refreshed, tasks could receive `asyncio.CancelledError`.
3. **Missing Heartbeats**: When a job was running and the daemon restarted or crashed, `jobs` table rows remained in `running` status indefinitely because there was no heartbeat recovery mechanism to mark interrupted jobs.

---

## 7. Conversation Memory & Root Cause of Loss

### How chat history is stored:
Messages are stored in the SQLite `messages` table (`id`, `conversation_id`, `role`, `content`, `taint_flags`, `created_at`).

### Exact cause of lost thread turns:
1. **CLI Defaults (`src/noxai/cli.py:388-396`)**:
   `nx chat` has `cont: bool = typer.Option(False, "--continue", "-c")`. Unless `--continue` or `--conv` is explicitly passed, `conversation_id` is passed as `None`.
   In `src/noxai/orchestrator.py:291`:
   `actual_conv_id = conversation_id or f"conv_{uuid.uuid4().hex[:12]}"`
   This generates a completely new ephemeral conversation on every turn by default.
2. **Context Assembly Truncation (`src/noxai/memory/context.py:596-606`)**:
   In `build_context()`, `raw_history_messages` is traversed in reverse:
   ```python
   for m in reversed(raw_history_messages):
       m_tokens = approximate_tokens(m.get_text())
       if allocated_tokens + m_tokens <= effective_budget:
           included_history.insert(0, m)
           allocated_tokens += m_tokens
       else:
           break
   ```
   If older turns exceed the budget, they are dropped with `break`. Older turns are only represented if a `ConversationSummary` row exists in `conv_summaries`. However, rolling summarization was performed in an un-awaited background task (`_bg_cms()` in `orchestrator.py:881-890`) that rarely finished or failed silently.

---

## 8. Briefing Feedback & Self-Rewriting Prompt

- **Feedback Storage**: Stored in `briefing_feedback` table:
  `id`, `briefing_id`, `topic_id`, `item_id`, `rating` ('like' | 'dislike'), `reason_text`, `tags_json`, `created_at`, `applied_in_prompt_version`.
- **Prompt Storage**: Stored in `prompt_versions` table:
  `id`, `scope` (`topic_{topic_id}`), `version`, `text`, `changelog`, `created_by`, `parent_version`, `status` ('active' | 'candidate'), `created_at`.
- **Disconnected Loop**: Although `src/noxai/briefing/feedback.py` implements `check_and_rewrite_topic_prompt()`, `src/noxai/briefing/engine.py` never queried `prompt_versions` during Stage B topic extraction, rendering prompt rewrites inactive.

---

## 9. Status of Sessions J, K, L

| Feature | Status | Proof Command & Evidence |
| :--- | :--- | :--- |
| **Standalone daemon as systemd user service** | **DONE** | `systemctl --user is-active noxd.service` -> `active` (PID 1907). |
| **Tauri tray app** | **DONE** | `desktop/src-tauri` configured with tray icon and builds with Tauri v2. |
| **PKGBUILD** | **DONE** | `packaging/arch/PKGBUILD` present with package dependencies and build steps. |
| **One 'Run briefing' button** | **DONE** | `ui/src/pages/BriefingsPage.tsx` lines 773-787 (`data-testid="run-briefing-button"`). |
| **Automatic 04:00 & 18:00 runs with catch-up** | **DONE** | `src/noxai/briefing/scheduler.py` has `_slots = [time(4, 0), time(18, 0)]` and boot catch-up. |
| **Cloud connection ON by default** | **DONE** | `python -c "from noxai.config import get_config; print(get_config().privacy.chat_to_cloud)"` -> `True`. |
| **20:00 IST GitHub backup job with boot catch-up** | **DONE** | `job_06725cb80054` ("BackUp", cron `0 20 * * *`, Asia/Kolkata, enabled=1, ran today). |
| **Job pre-planning with cloud key** | **DONE** | `_call_cloud_planner` in `src/noxai/jobs/planner.py` with fallback to template planner. |
| **Completion & error notifications** | **DONE** | `DesktopDeliveryChannel` in `src/noxai/scheduler/delivery.py` and `step_notify` in `src/noxai/jobs/steps.py`. |
| **Delete buttons for jobs, tasks, briefings** | **DONE** | `DeleteConfirmModal` used across `BriefingsPage.tsx`, `JobsPage.tsx`, and `TasksPage.tsx`. |

---

## 10. Test Suite Baseline

- **Total Collected**: 603 tests across 42 test files.
- **Pass Count**: 601 passed.
- **Fail Count**: 2 failed (pre-existing before Session M):
  1. `test_repo_hygiene.py::test_no_unmasked_real_keys_tracked` (mock key string `nvapi-1234...` in test fixture).
  2. `test_daemon_lifecycle.py::test_daemon_restart_invalidates_old_cookies` (session token cached in-process).
- **Execution Time**: 309.58s (~5 minutes).
- **Output File**: `docs/reports/session-M/baseline-tests.txt`.
- **Coverage**: Measured via `.coverage` SQLite database and pytest coverage plugin.

---

## Integration Points for Session M

| Workstream | Target Files | Key Functions / Classes to Modify or Add | Purpose |
| :--- | :--- | :--- | :--- |
| **Workstream A** (Engine & Health) | `src/noxai/models/base.py`<br>`src/noxai/models/engine.py` [NEW]<br>`src/noxai/models/nvidia.py`<br>`src/noxai/models/ollama.py`<br>`src/noxai/models/router.py`<br>`src/noxai/doctor.py`<br>`src/noxai/config.py` | `Engine` interface, `NvidiaEngine`, `OllamaEngine`, `EngineHealth`, `ModelRouter.chat()`, `run_doctor()`, fallback policy (`ask`, `auto_notify`, `never`). | Replace silent fallback with typed health checks, 10-minute model validator, explicit fallback policy, and desktop alerts. |
| **Workstream B** (Persistent Scheduler) | `src/noxai/migrations/010_schedules.sql` [NEW]<br>`src/noxai/scheduler/persistent.py` [NEW]<br>`src/noxai/api/app.py`<br>`src/noxai/api/routes.py`<br>`src/noxai/cli.py` | `schedules` & `schedule_runs` tables, `PersistentScheduler`, daemon startup loop, `nx schedule` CLI, decoupling UI. | Daemon-owned persistent scheduler with single flight, heartbeats, and catch-up policies (`run_once`). |
| **Workstream C** (Trace Logging) | `src/noxai/migrations/011_traces.sql` [NEW]<br>`src/noxai/tracing/recorder.py` [NEW]<br>`src/noxai/briefing/feedback.py`<br>`src/noxai/cli.py` | `traces` table, `redact_secrets()`, `TraceRecorder`, `improve_topic_prompt()`, `nx traces`, `nx prompt rollback`. | Redacted trace logging, 90-day retention, feedback-driven prompt self-rewriting loop. |
| **Workstream D1** (Chat Memory) | `src/noxai/memory/context.py`<br>`src/noxai/memory/folding.py`<br>`src/noxai/orchestrator.py`<br>`src/noxai/cli.py` | `build_context()`, `RollingSummaryFolder`, token budgeting (4 parts), `nx chat` session continuity. | Fix forgotten-chat defect, enforce 4-part context budget, resilient rolling summary. |
| **Workstream D2** (Document Memory) | `src/noxai/migrations/012_document_memory.sql` [NEW]<br>`src/noxai/memory/docs.py` [NEW]<br>`src/noxai/cli.py` | `documents`, `chunks`, `fts_chunks` FTS5 tables, `DocumentIndexer`, `search_documents()`, `nx memory` CLI. | Fast BM25 document search over Obsidian Vault and Study notes with read-only guarantee. |
| **Workstream E** (Skills & MCP) | `src/noxai/skills/catalog.py` [NEW]<br>`src/noxai/skills/parser.py` [NEW]<br>`src/noxai/mcp/client.py` [NEW]<br>`src/noxai/tools/registry.py` | `AgentSkill`, `parse_skill_md()`, `StdioMCPClient`, `ToolRegistry.register_skill()`. | Parse `SKILL.md` format, progressive prompt loading, script execution safety gates, minimal stdio MCP. |
| **Workstream F** (Cleanup) | `/tmp/openjarvis-study.*`<br>`THIRD_PARTY_NOTICES.md` | Verification scripts, cleanup of `/tmp/openjarvis-study.*`. | Clean removal of clone, verify zero unauthorized imports/dependencies, final report. |
