# NoX Architecture

## Overview

NoX is structured as a daemon-plus-thin-clients architecture. One always-on
background process (`noxd`) owns all logic, state, and model access. The web
UI and CLI are clients of its HTTP/WebSocket API.

```mermaid
graph TB
    subgraph "Login sequence"
        FL[FaceLock] --> ND[noxd daemon]
        ND --> UI[Chromium --app UI]
    end

    subgraph "noxd — FastAPI + uvicorn :7777"
        API[REST API\n/api/*]
        WS[WebSocket /ws\nEvent bus]
        AUTH[Session token\n+ cookie auth]
        API --> AUTH
        WS --> AUTH
    end

    subgraph "Agent pipeline"
        LOOP[Agent loop\nagent/loop.py]
        PLAN[Planner\nagent/planner.py]
        EXEC[DAG Executor\nagent/executor.py]
        WORK[Workers\nagent/worker.py]
        SYNTH[Synthesizer\nagent/synthesizer.py]
        PLAN --> EXEC --> WORK --> SYNTH
        LOOP --> PLAN
    end

    subgraph "Model router"
        ROUTER[Router\nmodels/router.py]
        OLLAMA[OllamaProvider\nqwen2.5:14b / nomic-embed-text]
        NVIDIA[NvidiaProvider\nllama-3.3-nemotron\npublic data only]
        TAINT[Taint checker\nPrivacyViolation if PRIVATE→cloud]
        ROUTER --> TAINT
        TAINT --> OLLAMA
        TAINT --> NVIDIA
    end

    subgraph "Tools"
        REGISTRY[Tool registry\ntools/registry.py]
        SAFETY[Safety layer\ntools/safety.py]
        BUILTINS[Built-in tools\nweb / system / memory / scheduler / meta]
        AUDIT[Audit log\nSQLite]
        REGISTRY --> SAFETY --> BUILTINS
        SAFETY --> AUDIT
    end

    subgraph "Memory"
        STORE[Memory store\nmemory/store.py]
        EMBED[Embeddings\nnomic-embed-text]
        RECALL[Recall\nhybrid BM25+cosine]
        EXTRACT[Extractor\nfacts + episodes]
        CONS[Consolidation\nnightly job]
        STORE --> EMBED
        RECALL --> STORE
        EXTRACT --> STORE
        CONS --> STORE
    end

    subgraph "Scheduler"
        SCHED[Scheduler loop\n15s tick]
        JOBS[Jobs table\nSQLite]
        SEEDS[Seed data\ndaily-briefing\nopportunities\nmemory-consolidation]
        DELIVERY[Delivery channels\nui / desktop notify]
        SCHED --> JOBS
        SEEDS --> JOBS
        SCHED --> DELIVERY
    end

    subgraph "Storage — SQLite WAL"
        DB[(nox.db\n~/.local/share/nox/)]
    end

    subgraph "Config"
        CFG[config.toml\n~/.config/nox/]
        SEC[secrets.env\n0600 permissions]
        CFG --> API
        SEC --> ROUTER
    end

    API --> LOOP
    LOOP --> ROUTER
    LOOP --> REGISTRY
    LOOP --> STORE
    EXEC --> ROUTER
    SCHED --> LOOP
    STORE --> DB
    JOBS --> DB
    AUDIT --> DB
    WS --> LOOP
```

## Data Flow — Chat Request

```
User → POST /api/chat
         ↓
       Auth check (cookie + CSRF)
         ↓
       Gate check (if mode=command)
         ↓
       Complexity gate → simple? → Agent loop
                       → complex? → Planner → DAG Executor
                                                ↓
                                         Workers (parallel, up to 2)
                                                ↓
                                         Synthesizer
         ↓
       WebSocket events pushed to UI (token, tool_call, plan_created, …)
         ↓
       Memory extraction (background, local model only)
```

## Privacy Routing

Every piece of content carries `TaintFlag` bits (`PRIVATE`, `UNTRUSTED`).
The router enforces:

- `PRIVATE` content → **local Ollama only**. Never sent to NVIDIA. Hard-coded
  in `models/router.py`, tested with a fake cloud provider.
- `UNTRUSTED` content → wrapped in `<untrusted_content>` tags before the model
  sees it. Escalates approval requirements for write/dangerous tool calls.

## File Layout

```
nox/
├── src/noxai/         Python package
│   ├── config.py      Load + validate config.toml
│   ├── db.py          SQLite helpers + migration runner
│   ├── taint.py       TaintFlag + Content wrapper
│   ├── logging_setup  Structured JSON logs
│   ├── cli.py         nx command
│   ├── models/        Provider, router (M1)
│   ├── agent/         Loop, planner, executor, workers (M3+M5)
│   ├── tools/         Registry, safety, builtins (M2)
│   ├── memory/        Store, embed, recall, extract (M4)
│   ├── scheduler/     Loop, jobs, seeds, delivery (M6)
│   ├── api/           FastAPI app, routes, WebSocket (M7)
│   └── prompts/       system.md and prompt templates
├── ui/                React + TypeScript + Vite + Tailwind (M8)
├── tests/             pytest test suite
├── docs/              Architecture, decisions, security, tool guide
├── systemd/           noxd.service user unit
├── hyprland/          nox.conf.snippet
└── scripts/           install.sh, nx-open.sh
```

## Key Design Principles

1. **Behavior is data** — jobs, schedules, sources, preferences are DB rows.
2. **Safety is code** — tiers, deny-lists, privacy routing cannot be prompted away.
3. **Daemon + thin clients** — one process owns all state.
4. **Local-first** — Ollama on the same machine; cloud only for public data.
5. **Everything observable** — every step emits a WebSocket event.
6. **Untrusted content is data** — web/RSS text is wrapped and taint-flagged.
7. **Fail soft** — model down? Degrade and keep running.
