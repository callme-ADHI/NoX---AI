# Third-Party Notices and Licenses

This file contains licensing and attribution notices for open-source software referenced, adapted, or studied in the NoX codebase.

---

## 1. OpenJarvis

- **Project Name**: OpenJarvis
- **Project URL**: https://github.com/open-jarvis/OpenJarvis
- **Commit Studied**: `77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b` (Sun Nov 15 06:09:45 2026 +0000)
- **License**: Apache License, Version 2.0 (see `third_party/openjarvis/LICENSE`)

### Concepts Referenced & Adapted
In NoX Session M, OpenJarvis was studied as an architecture reference and design benchmark. OpenJarvis is **never** an installed dependency, runtime import, or external requirement of NoX. All features are implemented in native NoX style within package `noxai`.

| NoX Target / Feature Area | OpenJarvis Origin / Reference | Nature of Adaptation |
| :--- | :--- | :--- |
| **Engine Interface & Health** (`noxai.models.engine`) | `src/openjarvis/engine/_stubs.py`, `engine/_base.py` | Unified `Engine` abstraction (`generate`, `stream`, `list_models`, `health`) adapted for NoX's dual `NvidiaEngine` (key pool leased) and `OllamaEngine` with typed `EngineHealth` reason codes. |
| **Persistent Scheduler** (`noxai.scheduler.persistent`) | `src/openjarvis/scheduler/scheduler.py`, `scheduler/store.py` | Single-flight scheduler loop and SQLite state machine (`schedules`, `schedule_runs`) adapted for NoX's async SQLite WAL, timezone handling (`Asia/Kolkata`), and catch-up recovery. |
| **Trace Logging** (`noxai.tracing.recorder`) | `src/openjarvis/traces/store.py`, `traces/collector.py` | Step-level tracing data model adapted with strict secret redaction (`nvapi-*`, tokens) and 90-day retention cleanup. |
| **Conversation Memory** (`noxai.memory.context`) | `src/openjarvis/agents/monitor_operative.py` | Continuous conversation context budgeting (4-part assembly: system/pinned, rolling summary, recent verbatim window, retrieved past turns). |
| **Document Memory & BM25 Search** (`noxai.memory.docs`) | `src/openjarvis/tools/storage/ingest.py`, `storage/bm25.py` | FTS5 BM25 search over user note repositories with heading/paragraph chunking and read-only source guarantees. |
| **Skills Catalog & MCP Stdio** (`noxai.skills.parser`, `noxai.mcp.client`) | `src/openjarvis/skills/parser.py`, `mcp/client.py` | `SKILL.md` YAML frontmatter parser and minimal JSON-RPC stdio client adapted without external OpenJarvis runtime deps. |

### Code Adaptation Header Rule
Every NoX source file that includes code directly copied or closely adapted from OpenJarvis includes the required Apache-2.0 attribution header:
```python
# Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
# commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
# Modified for NoX: <specific description of modification>
```
