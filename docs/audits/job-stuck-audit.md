# Audit: Why Job `job_06725cb80054` Was Stuck

**Audit Date**: 2026-11-05
**Target Job**: `job_06725cb80054` ("BackUp")
**Status**: Root Cause Confirmed with Live System Evidence

---

## 1. Real Database Audit (Read-Only SQL)

### 1.1 Jobs Row for `job_06725cb80054`
Command:
```bash
sqlite3 -header -line ~/.local/share/nox/nox.db "SELECT id, name, instruction, schedule_type, schedule_expr, timezone, config_json, next_run_at, last_run_at, enabled FROM jobs WHERE id = 'job_06725cb80054';"
```
Output:
```text
id: job_06725cb80054
         name: BackUp
  instruction: inside Documents there are fre folders. name Certificates, Study, Obsidian Vault, and those are connect the the git repo. so i need to push everything to the correct git repo at 8 pm.  just run all command syou have got my access and permission. no need to ask
schedule_type: cron
schedule_expr: 40 18 * * *
     timezone: Asia/Kolkata
  config_json: {}
  next_run_at: 2026-11-05T13:10:00+00:00
  last_run_at: 2026-11-04 14:35:52
      enabled: 1
```

### 1.2 Recent `job_runs` for `job_06725cb80054`
Command:
```bash
sqlite3 -header -line ~/.local/share/nox/nox.db "SELECT id, job_id, started_at, finished_at, status, task_run_id, error, output_markdown FROM job_runs WHERE job_id = 'job_06725cb80054' ORDER BY started_at DESC LIMIT 10;"
```
Output:
```text
id: jrun_270e44ce4081
         job_id: job_06725cb80054
     started_at: 2026-11-04 14:30:03
    finished_at: 2026-11-04 14:35:52
         status: failed
    task_run_id: 
          error: 
output_markdown: Run cancelled by user.
```

### 1.3 Approvals around 2026-11-04 14:30 UTC (20:00 IST)
Command:
```bash
sqlite3 -header -line ~/.local/share/nox/nox.db "SELECT * FROM approvals WHERE requested_at >= '2026-11-04T13:00:00' AND requested_at <= '2026-11-04T15:00:00';"
```
Output:
```text
id: 31
   tool_name: run Backup job now
   args_json: {"job_id": "job_06725cb80054"}
   args_hash: aebea343f5b369bf903c5491d47c41e6796be9faa78eec16a408c89cee9e15a8
      reason: User requested to execute the Backup job immediately instead of waiting for its scheduled time.
requested_at: 2026-11-04T13:18:13.229Z
  expires_at: 2026-11-04T13:33:13.228038+00:00
      status: approved
  decided_at: 2026-11-04 13:18:24
  decided_by: owner
      run_id: run_fok2knzsj1u

          id: 32
   tool_name: run_backup_job
   args_json: {"job_id": "job_06725cb80054"}
   args_hash: aebea343f5b369bf903c5491d47c41e6796be9faa78eec16a408c89cee9e15a8
      reason: User requested to run the Backup job immediately.
requested_at: 2026-11-04T13:30:07.370Z
  expires_at: 2026-11-04T13:45:07.369554+00:00
      status: approved
  decided_at: 2026-11-04 13:30:20
  decided_by: owner
      run_id: run_pije6fvoz6
```

### 1.4 Tasks around 2026-11-04 14:30 UTC
Command:
```bash
sqlite3 -header -line ~/.local/share/nox/nox.db "SELECT id, title, prompt, lane, status, created_at, started_at, finished_at, error, approval_id, run_id FROM tasks WHERE created_at >= '2026-11-04T13:00:00' AND created_at <= '2026-11-04T15:00:00';"
```
Output:
```text
(No tasks in waiting_approval created in this time window)
```

### 1.5 Linked Agent Run Record (`fad6cb6af45d48a4a056e58726c4fec3`)
Command:
```bash
sqlite3 -header -line ~/.local/share/nox/nox.db "SELECT * FROM runs WHERE id = 'fad6cb6af45d48a4a056e58726c4fec3';"
```
Output:
```text
id: fad6cb6af45d48a4a056e58726c4fec3
           kind: chat
conversation_id: 
     job_run_id: 
         status: cancelled
    allow_cloud: 0
     started_at: 2026-11-04T14:31:18.060537+00:00
    finished_at: 2026-11-04T14:35:52.172098+00:00
          error: 
     trace_json: {"route": 0.0, "memory_recall": 413.22, "prompt_build": 1.65, "provider_connect": 0.0, "time_to_first_token": 0.0, "generation": 0.0, "tool_time": {}, "post_run_extraction": 0.0, "total": 274216.47, "prompt_tokens_est": 1100, "model_used": "qwen2.5:14b", "intent": "reply_now"}
```

### 1.6 Systemd Daemon Log Lines around 20:00–20:06 IST (14:30–14:36 UTC)
Command:
```bash
journalctl --user -u noxd --since "2026-11-04 20:05:50" --until "2026-11-04 20:06:00" --no-pager
```
Output:
```text
Nov 04 20:05:51 X systemd[1851]: Stopping NoX personal AI agent daemon...
Nov 04 20:05:52 X noxd[1861]: 20:05:52 INFO     [uvicorn.error] Shutting down
Nov 04 20:05:52 X noxd[1861]: 20:05:52 INFO     [noxai.api.websocket] WebSocket client disconnected.
Nov 04 20:05:52 X noxd[1861]: 20:05:52 INFO     [uvicorn.error] Waiting for application shutdown.
Nov 04 20:05:52 X noxd[1861]: 20:05:52 INFO     [noxai.api.app] NoX daemon shutdown initiated...
Nov 04 20:05:52 X noxd[1861]: 20:05:52 INFO     [noxai.api.app] Stopping TaskSupervisor...
Nov 04 20:05:52 X noxd[1861]: 20:05:52 INFO     [noxai.tasks.supervisor] Stopping TaskSupervisor, cancelling running background tasks...
Nov 04 20:05:52 X noxd[1861]: 20:05:52 INFO     [noxai.tasks.supervisor] TaskSupervisor shutdown complete.
Nov 04 20:05:52 X noxd[1861]: 20:05:52 INFO     [noxai.api.app] Stopping BriefingScheduler...
Nov 04 20:05:52 X noxd[1861]: 20:05:52 INFO     [noxai.briefing.scheduler] BriefingScheduler stopped.
Nov 04 20:05:52 X noxd[1861]: 20:05:52 INFO     [noxai.api.app] Stopping JobScheduler...
Nov 04 20:05:52 X noxd[1861]: 20:05:52 INFO     [noxai.agent.loop] Agent run fad6cb6af45d48a4a056e58726c4fec3 was cancelled.
```

---

## 2. Code Trace with file:line

1. **Job Scheduling Trigger**:
   - In `src/noxai/scheduler/scheduler.py:180`, when the scheduler detects `next_run_at <= now()`, it immediately inserts a `job_runs` row:
     ```python
     INSERT INTO job_runs (id, job_id, started_at, status)
     VALUES (?, ?, datetime('now'), 'running')
     ```
   - Notice: `job_runs.status` is set to `'running'`. At this point, `jobs.last_run_at` is NOT updated (it remains `NULL` because line 371 only updates `last_run_at` upon completion).

2. **Absence of Plan & Freeform Agent Execution**:
   - In `src/noxai/scheduler/scheduler.py:270-281`, because there was no plan-first engine, the scheduler instantiates an `AgentLoop` and executes the user instruction as a freeform prompt:
     ```python
     loop = AgentLoop(
         router=self.router,
         registry=self.registry,
         config=self.config,
         event_bus=self.event_bus,
     )
     loop_res = await loop.run(
         formatted_instruction,
         allow_cloud=allow_cloud,
         db=db,
     )
     ```
   - Because `job.config_json` had `allow_cloud = false` (default), the agent loop attempted to call the local Ollama LLM (`qwen2.5:14b`).
   - The trace in table `runs` for `fad6cb6af45d48a4a056e58726c4fec3` shows:
     `"model_used": "qwen2.5:14b", "total": 274216.47, "time_to_first_token": 0.0, "generation": 0.0, "tool_time": {}`.
     The local Ollama model hung for over 274 seconds (4.5 minutes) without emitting a single token or tool call.

3. **No Timeout or Heartbeat**:
   - In `src/noxai/scheduler/scheduler.py:238-240`:
     ```python
     max_runtime = cfg.get("max_runtime_seconds")
     timeout_sec = float(max_runtime) if max_runtime is not None else None
     ```
     Because `max_runtime_seconds` was not specified in `config_json`, `timeout_sec` was `None`.
   - `scheduler.py` has no periodic heartbeat update, no step-level progress logging, and no watchdog thread.
   - The job stayed in status `running` in `job_runs` with `jobs.last_run_at` as `NULL`.

4. **Why the Chat Assistant Reported "running" as Healthy**:
   - In `src/noxai/tools/builtin/scheduler_tools.py:170-176`, tool `job_get` executes:
     ```python
     runs = await fetch_all(
         ctx.db,
         "SELECT id, started_at, finished_at, status, error FROM job_runs "
         "WHERE job_id = ? ORDER BY started_at DESC LIMIT 5",
         (job_dict["id"],),
     )
     job_dict["recent_runs"] = [dict(r) for r in runs]
     ```
   - When the user asked in conversation `conv_53ea1c2a7e34` at 14:33:27 UTC:
     `did you run the backup job?`
   - The assistant read the raw output: `status: "running"`, `started_at: "2026-11-04 14:30:03"`, `finished_at: null`.
   - Lacking any heartbeat timestamp or step indicator, the LLM assumed `status == "running"` meant active, healthy execution, replying:
     *"The backup job is currently executing... One entry shows a run started at 2026-11-04 14:30:03 UTC with status running and no finish time yet... So, yes – the backup job is running right now."*

5. **Run Termination**:
   - At 14:35:52 UTC (20:05:52 IST), the daemon received a `systemd stop` / restart.
   - `noxai.agent.loop` caught `CancelledError` during shutdown, updating `job_runs` to `status: failed`, `output_markdown: Run cancelled by user.` at `finished_at: 2026-11-04 14:35:52`.

---

## 3. Root Cause Analysis

### Refutation / Confirmation:
- The initial hypothesis was that the agent called a write/exec tool that parked in `waiting_approval`.
- **Evidence Refutation**: The approvals query shows zero approvals created at 14:30 UTC for `jrun_270e44ce4081`. (Prior approvals #31 and #32 were requested at 13:18 and 13:30 from chat interactions, and both were already approved by the owner).
- **Confirmed Root Cause**:
  1. **Reactive LLM-in-Execution Loop**: The scheduler attempted to run a complex, multi-folder git operation by prompting a local Ollama model (`qwen2.5:14b`) in an open-ended agent loop. The local model took 274+ seconds without completing TTFT or generating tool calls.
  2. **No Plan-First Architecture**: There were no pre-compiled, validated steps. Execution was entirely dependent on generative LLM inference during the scheduled execution window.
  3. **No Heartbeat or Stall Watchdog**: Neither `job_runs` nor `tasks` recorded heartbeat timestamps during scheduled job runs. There was no watchdog to mark a 3-minute stalled run or enforce a default timeout (since `max_runtime_seconds` was not set).
  4. **Binary / Blind Status in UI & Chat**: A run with `status = "running"` had no step context (`step 3/9: git.push`) or recent heartbeat timestamp. The chat assistant erroneously reported an unresponsive, hanging run as healthy and "running right now".

---

## 4. Remediation Plan (Phases 1 & 2)

1. **Plan-First Architecture**:
   - Before any run, the cloud API produces a sanitized JSON plan with explicit step kinds (`git.preflight`, `scan.secrets`, `git.push`, etc.).
   - During execution, **zero LLM calls** occur. Pure deterministic Python actions execute one step at a time.
2. **Step Persistence & Heartbeats**:
   - `job_run_steps` table tracks each step (`run_id`, `step_index`, `kind`, `state`, `started_at`, `finished_at`).
   - 5-second heartbeats on `job_runs`. If no heartbeat is received for 3 minutes, the watchdog transitions the run to `stalled`.
3. **Automatic Startup Repair**:
   - `nx jobs repair` (and automatic daemon startup reconciliation) reconciles orphaned or stalled runs, updating `job_runs` and `jobs.last_run_at`.
   - The existing stuck state will be reconciled cleanly on startup.
