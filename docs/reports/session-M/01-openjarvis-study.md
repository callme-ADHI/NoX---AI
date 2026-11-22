# Phase 0 Recon: OpenJarvis Architecture Study

**Date**: 2026-11-15  
**Studied Commit**: `77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b` (Sun Nov 15 06:09:45 2026 +0000)  
**Upstream URL**: https://github.com/open-jarvis/OpenJarvis  
**License**: Apache-2.0 (verified from `LICENSE` file)  
**Clone Directory**: `/tmp/openjarvis-study.E4FHTu/src` (Read-only study)

---

## 1. Engine (`src/openjarvis/engine/`)

### 1.1 What It Does
OpenJarvis defines an abstract base `InferenceEngine` (`_stubs.py`, 141 lines) providing a standardized interface for text generation and token streaming across backends:
- `generate(messages, model, temperature, max_tokens, **kwargs) -> dict`
- `stream(messages, model, temperature, max_tokens, **kwargs) -> AsyncIterator[str]`
- `stream_full(messages, model, ...) -> AsyncIterator[StreamChunk]`
- `list_models() -> list[str]`
- `health() -> bool`
- `can_serve(model: str) -> bool`

Concrete engine implementations include:
- `cloud.py` (80,808 lines): A massive cloud aggregator supporting OpenAI, Anthropic, Gemini, DeepSeek, Cerebras, Groq, Together, Mistral, and xAI.
- `ollama.py` (22,894 lines): Direct local Ollama HTTP client wrapping `/api/chat` with num_ctx and keep_alive.
- `nim.py` (10,227 lines): NVIDIA NIM / OpenAI-compatible endpoint wrapper.
- `_base.py` (131 lines): Shared context-overflow heuristics (`looks_like_context_length_error`), message serialization (`messages_to_dicts`), and prompt token estimation (~4 chars/token).

### 1.2 File Sizes & Dependencies
- `_stubs.py`: 141 lines; imports `openjarvis.core.types.Message`.
- `_base.py`: 131 lines; imports `openjarvis.core.types.Message`, `_stubs.InferenceEngine`.
- `cloud.py`: ~80k lines; massive vendor zoo with extensive provider-specific schemas.

### 1.3 License & Header
All engine files follow Apache License 2.0 with standard docstrings.

### 1.4 Functions & Classes Worth Porting vs Re-implementing
- **Worth Porting / Pattern**: The clean four-method `Engine` protocol (`generate`, `stream`, `list_models`, `health`) and the `looks_like_context_length_error` heuristic.
- **What NoX Needs Instead**: OpenJarvis's `health()` returns a simple `bool`. NoX requires a rich **typed** `EngineHealth` record (status `ok|degraded|down`, reason codes `auth|model_gone|rate_limited|network|timeout|unknown`, latency ms, check timestamp). Furthermore, NoX does not need the 80k-line vendor zoo in `cloud.py`; NoX only needs `NvidiaEngine` (wrapping OpenAI-compatible endpoint with key pool leases) and `OllamaEngine`.

---

## 2. Scheduler (`src/openjarvis/scheduler/`)

### 2.1 What It Does
OpenJarvis provides a background scheduled task runner in `scheduler.py` (342 lines) backed by SQLite `store.py` (168 lines):
- Supported schedule types: `cron` (5-field cron via `croniter`), `interval` (seconds), and `once` (ISO datetime).
- Storage tables: `scheduled_tasks` (`id`, `prompt`, `schedule_type`, `schedule_value`, `status`, `next_run`, `last_run`, `agent`, `tools`, `metadata`) and `task_run_logs` (`id`, `task_id`, `started_at`, `finished_at`, `success`, `result`, `error`).
- Background worker loop: Uses `threading.Thread` polling every `poll_interval` (default 60s), evaluating `task.next_run <= now_iso()`.
- MCP tools: Exposes `schedule_task`, `list_scheduled_tasks`, `cancel_scheduled_task`.

### 2.2 File Sizes & Dependencies
- `scheduler.py`: 342 lines; imports `croniter`, `SchedulerStore`.
- `store.py`: 168 lines; stdlib `sqlite3`, `json`.
- `tools.py`: 280 lines; MCP tool adapters.

### 2.3 What NoX Needs Instead
OpenJarvis's scheduler is thread-based, does not support timezone-aware scheduling (it stores raw UTC ISO strings), lacks single-flight heartbeat recovery (if a process crashes mid-run, runs stay in limbo), and does not support boot catch-up policies (`run_once`, `skip`, `run_all`).
NoX requires:
- Daemon-owned async loop within FastAPI lifespan.
- Full timezone awareness (default `Asia/Kolkata`).
- Persisted `schedules` and `schedule_runs` with state machine: `running`, `success`, `failed`, `cancelled`, `missed`, `interrupted`.
- Clock jump / suspend-resume detection (>2 min drift).
- Single-flight locking with timeout and startup orphan recovery.

---

## 3. Memory & Document Ingestion (`memory/` & `tools/storage/`)

### 3.1 What It Does
OpenJarvis separates user profile facts from document search:
- `memory/store.py` (457 lines): Local JSONL fact store with cross-process file locking (`fcntl.flock` on Linux, `msvcrt.locking` on Windows). Tracks trust tiers (`auto`, `trusted`, `untrusted`).
- `tools/storage/sqlite.py` (530 lines): SQLite store for document chunks and vector embeddings.
- `tools/storage/chunking.py` (320 lines): Text chunker splitting Markdown by headings first (`#`, `##`, `###`), then by token/character length with overlap.
- `tools/storage/bm25.py` (280 lines): SQLite FTS5-based BM25 full-text keyword retrieval.
- `tools/storage/hybrid.py` (350 lines): Reciprocal Rank Fusion (RRF) combining BM25 and vector similarity.
- `tools/storage/context.py` (420 lines): Formats recalled document chunks with filename, line range, and inline citation markers.

### 3.2 File Sizes & Dependencies
- `chunking.py`: 320 lines; lightweight regex and Markdown header traversal.
- `bm25.py`: 280 lines; stdlib `sqlite3` FTS5.
- `sqlite.py`: 530 lines; SQLite schema for documents and chunks.

### 3.3 What NoX Needs Instead
OpenJarvis stores memory in separate JSONL files and a standalone `knowledge.db` SQLite database.
NoX already uses a centralized WAL SQLite database (`~/.local/share/nox/nox.db`).
NoX needs:
- Single database schema integration: `documents`, `chunks`, and `fts_chunks` FTS5 tables in `nox.db`.
- Strictly read-only indexing for `~/Documents/Obsidian Vault` and `~/Documents/Study`.
- Mtime + SHA256 change tracking for incremental updates.
- Zero extra heavy dependencies (avoiding external ColBERT/FAISS).

---

## 4. Agents & Memory Compression (`src/openjarvis/agents/`)

### 4.1 What It Does
- `monitor_operative.py` (759 lines): Long-horizon continuous agent featuring:
  - Observation compression (`summarize`, `truncate`, `none`).
  - Memory extraction (`structured_json`, `scratchpad`, `causality_graph`).
  - Context budget management and turn folding.
- `deep_research.py` (383 lines): Multi-hop research loop with strict source verification and inline Markdown citations (`[Source: path/to/file#L10-L20]`).

### 4.2 Re-implementing the Idea in NoX
OpenJarvis's `monitor_operative.py` is tightly coupled to its `ToolUsingAgent` hierarchy.
For NoX:
- **Conversation Memory (Workstream D1)**: Adopt the rolling summary pattern and the 4-part context budget assembly:
  1. System prompt + pinned facts.
  2. Rolling summary of folded historical turns.
  3. Verbatim sliding window of the last 8 turns.
  4. Retrieved past snippets relevant to current turn.
- Store every turn in `messages` table with token estimates and turn indices.

---

## 5. Trace Logging (`src/openjarvis/traces/`)

### 5.1 What It Does
`traces/store.py` (391 lines) records execution traces into SQLite:
- Tables: `traces` and `trace_steps` with an FTS5 search index (`traces_fts`) synchronized via insert/update/delete triggers.
- Fields: `trace_id`, `query`, `agent`, `model`, `engine`, `result`, `outcome`, `feedback`, `started_at`, `ended_at`, `total_tokens`, `total_latency_seconds`, `metadata`, `messages`.
- `analyzer.py` (11,582 lines): Evaluates trajectory success and computes latency metrics.

### 5.2 What NoX Needs Instead
OpenJarvis does NOT redact API keys or secrets before writing traces to disk.
NoX requires:
- Strict pre-storage secret redaction (`nvapi-*`, tokens over 32 chars, Bearer headers, GitHub PATs).
- 90-day retention cleanup policy capped at 100,000 rows.
- Feedback-driven prompt rewrite loop linking disliked traces to `prompt_versions` via `improve_topic_prompt()`.

---

## 6. Skills & MCP Catalog (`skills/` & `mcp/`)

### 6.1 What It Does
- `skills/parser.py` (232 lines): Two-pass YAML frontmatter parser for `SKILL.md` (conforming to `agentskills.io` standard). Validates `name`, `description`, `license`, `compatibility`, `allowed-tools`, and maps vendor fields.
- `skills/loader.py` (340 lines): Discovers skills from standard paths (`~/.openjarvis/skills`, `./skills`), reads instructions, and attaches executable scripts as tools.
- `mcp/client.py` (155 lines): Minimal JSON-RPC 2.0 client implementing MCP `initialize`, `notifications/initialized`, and `tools/list` over stdio transport.

### 6.2 What NoX Needs Instead
OpenJarvis's skill manager includes a heavy overlay engine and external registry dependencies.
NoX needs:
- A clean, standalone `parse_skill_md()` parser in `noxai.skills.parser`.
- Progressive prompt loading: skill description injected in system prompt; full instructions loaded only when activated.
- Safety gate: scripts in `scripts/` require executable verification and confirmation.
- Minimal `StdioMCPClient` in `noxai.mcp.client`.

---

## 7. Port Decision Table

| Feature / Pattern | Source in OpenJarvis | Port Decision | Est. Lines | Risk |
| :--- | :--- | :--- | :--- | :--- |
| **Engine Interface** | `src/openjarvis/engine/_stubs.py` | Re-implement as clean NoX pattern with typed `EngineHealth` | 150 | Low |
| **Context Length Heuristic** | `src/openjarvis/engine/_base.py` | Port code directly (`looks_like_context_length_error`) | 30 | Very Low |
| **Persistent Scheduler Model** | `src/openjarvis/scheduler/store.py` | Adapt SQLite schema (`schedules`, `schedule_runs`) for NoX WAL | 250 | Low |
| **Scheduler Single-Flight & Recovery** | `src/openjarvis/scheduler/scheduler.py` | Re-implement async loop with heartbeats and clock-drift catchup | 300 | Medium |
| **Trace Storage Schema** | `src/openjarvis/traces/store.py` | Adapt SQLite trace tables with pre-storage secret redaction | 220 | Low |
| **Prompt Rewrite Feedback Loop** | No equivalent in OJ (concept only) | Implement from first principles on `prompt_versions` | 180 | Low |
| **4-Part Memory Context Budget** | `src/openjarvis/agents/monitor_operative.py` | Adapt into `noxai.memory.context` with turn indices | 200 | Medium |
| **Markdown Heading Chunking** | `src/openjarvis/tools/storage/chunking.py` | Adapt heading + paragraph chunker (800 chars / 100 overlap) | 140 | Low |
| **FTS5 BM25 Document Search** | `src/openjarvis/tools/storage/bm25.py` | Adapt into `noxai.memory.docs` over user note roots | 220 | Low |
| **SKILL.md Frontmatter Parser** | `src/openjarvis/skills/parser.py` | Adapt two-pass parser into `noxai.skills.parser` | 150 | Low |
| **Stdio MCP Client** | `src/openjarvis/mcp/client.py`, `transport.py` | Port minimal stdio JSON-RPC client | 160 | Low |
