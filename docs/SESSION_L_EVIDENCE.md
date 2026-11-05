# Session L Evidence Log

This document records verbatim command executions, inputs, and outputs with rigorous labels:
- `VERIFIED-LIVE`: Real system, live daemon, real git/filesystem operations.
- `VERIFIED-TEST`: Real code execution path through automated test runner.
- `MOCK-ONLY`: Isolated unit test with mock/stub.
- `NOT-DONE`: Not yet verified or blocked.

---

## Phase 0: Stuck Job Audit Evidence

### Query Stuck Job Row
`VERIFIED-LIVE`:
```bash
sqlite3 -header -line ~/.local/share/nox/nox.db "SELECT id, name, instruction, schedule_type, schedule_expr, timezone, config_json, next_run_at, last_run_at, enabled FROM jobs WHERE id = 'job_06725cb80054';"
```
Output:
```text
           id: job_06725cb80054
         name: BackUp
  instruction: inside Documents there are fre folders. name Certificates, Study,
               Obsidian Vault, and those are connect the the git repo. so i need
               to push everything to the correct git repo at 8 pm.  just run all
               command syou have got my access and permission. no need to ask
schedule_type: cron
schedule_expr: 40 18 * * *
     timezone: Asia/Kolkata
  config_json: {}
  next_run_at: 2026-11-05T13:10:00+00:00
  last_run_at: 2026-11-04 14:35:52
      enabled: 1
```

### Query Stuck Job Run
`VERIFIED-LIVE`:
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
out_preview: Run cancelled by user.
```

### Trace Linked Agent Run
`VERIFIED-LIVE`:
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
     trace_json: {"route": 0.0, "memory_recall": 413.22, "prompt_build": 1.65,
                 "provider_connect": 0.0, "time_to_first_token": 0.0,
                 "generation": 0.0, "tool_time": {}, "post_run_extraction": 0.0,
                 "total": 274216.47, "prompt_tokens_est": 1100, "model_used":
                 "qwen2.5:14b", "intent": "reply_now"}
```

### Daemon Cancellation Log
`VERIFIED-LIVE`:
```bash
journalctl --user -u noxd --since "2026-11-04 20:05:50" --until "2026-11-04 20:06:00" --no-pager
```
Output:
```text
Nov 04 20:05:52 X noxd[1861]: 20:05:52 INFO [noxai.agent.loop] Agent run fad6cb6af45d48a4a056e58726c4fec3 was cancelled.
```
