# Automation & Briefing Audit

Audit Date: 2026-11-01
Auditor: NoX System Engineer

---

## 1. Daemon Service & Journalctl Evidence

### Service Status
Command: `systemctl --user status noxd`
Output:
```text
● noxd.service - NoX personal AI agent daemon
     Loaded: loaded (/home/boss/.config/systemd/user/noxd.service; enabled; preset: enabled)
     Active: active (running) since Sun 2026-11-01 19:27:09 IST; 24min ago
   Main PID: 1927 (noxd)
      Tasks: 13 (limit: 28464)
     Memory: 159.9M (peak: 161.8M)
        CPU: 33.098s
     CGroup: /user.slice/user-1000.slice/user@1000.service/app.slice/noxd.service
             └─1927 /home/boss/Documents/Projects/NoX/.venv/bin/python ...
```

### Journalctl Scheduler & Error Logs
Command: `journalctl --user -u noxd --since '3 days ago' | grep -i -E 'brief|sched|slot|tick|error'`
Key lines observed:
```text
Nov 01 19:24:10 X systemd[1887]: Stopping NoX personal AI agent daemon...
Nov 01 19:24:10 X systemd[1887]: Stopped NoX personal AI agent daemon.
Nov 01 19:27:09 X systemd[1916]: Started NoX personal AI agent daemon.
```
No scheduler tick outputs were logged following service startup because ticks log only at debug or error level, and any task triggered crashed immediately within the worker.

---

## 2. Real Database State

### Briefing Runs (`briefing_runs`)
Command: `SELECT * FROM briefing_runs ORDER BY created_at DESC LIMIT 5`
```text
{'id': 'brun_f9931bb67d4d', 'date': '2026-11-01', 'slot': 'morning', 'state': 'succeeded', 'generation_seconds': 11.43, 'items_count': 48, ...}
```
*Note*: This single successful morning run was executed manually via CLI (`nx briefing run --slot morning --force`). No automated runs succeeded.

### Legacy Jobs Table (`jobs`)
Command: `SELECT id, name, schedule_expr, enabled, next_run_at, last_run_at FROM jobs`
```text
{'id': 'job_daily_briefing', 'name': 'daily-briefing', 'schedule_expr': '0 8 * * *', 'enabled': 1, 'next_run_at': '2026-11-02T02:30:00+00:00', 'last_run_at': '2026-11-01T06:43:03.208182+00:00'}
```
*Note*: Legacy job table has a daily-briefing cron set to 08:00 UTC (or arbitrary expression) which collides with slot-based scheduling.

### Tasks Table (`tasks` with kind='briefing')
Command: `SELECT id, status, error, attempts, created_at, started_at, finished_at FROM tasks WHERE kind = 'briefing' ORDER BY created_at DESC LIMIT 6`
```text
1. id='task_054d05d6fb02' status='failed' error="cannot import name 'BriefingEngine' from 'noxai.briefing.engine' (/home/boss/Documents/Projects/NoX/src/noxai/briefing/engine.py)" attempts=3
2. id='task_82253994a739' status='failed' error="cannot import name 'BriefingEngine' from 'noxai.briefing.engine'" attempts=3
3. id='task_c98fcafcd3c3' status='failed' error="cannot import name 'BriefingEngine' from 'noxai.briefing.engine'" attempts=3
4. id='task_004facb54483' status='failed' error="cannot import name 'BriefingEngine' from 'noxai.briefing.engine'" attempts=3
5. id='task_9784189f5657' status='failed' error="cannot import name 'BriefingEngine' from 'noxai.briefing.engine'" attempts=3
6. id='task_c2df7f3861f8' status='failed' error="cannot import name 'BriefingEngine' from 'noxai.briefing.engine'" attempts=3
```

---

## 3. Code Trace & Root Cause Analysis

### A. Missing Import `BriefingEngine` vs `BriefingPipeline`
- **Location**: `src/noxai/briefing/scheduler.py:36`
  ```python
  from noxai.briefing.engine import BriefingEngine
  ...
  engine = BriefingEngine(config=cfg)
  ```
- **Location**: `src/noxai/api/routes.py:1165`
  ```python
  from noxai.briefing.engine import run_briefing_pipeline
  ```
- **Reality**: `src/noxai/briefing/engine.py` defines `BriefingPipeline` with method `execute_run(slot, date_str, force)`. Neither `BriefingEngine` nor `run_briefing_pipeline` exists!
- **Consequence**: Every scheduled run and every button click creates a task that crashes instantly with `ImportError`.

### B. Broken Button Flow in UI
- **Location**: `ui/src/pages/BriefingsPage.tsx:112-121`
  ```typescript
  const handleRunSlot = async (slot: 'morning' | 'evening') => {
    setRunning(true);
    try {
      await api.runBriefingSlot(slot, true);
      setTimeout(loadBriefings, 4000);
    } catch (err) {
      console.error('Failed to trigger briefing:', err);
    } finally {
      setRunning(false);
    }
  };
  ```
- **Root Cause**:
  1. `api.runBriefingSlot` returns immediately (`200 OK` with `{ok: true, task_id: ...}`).
  2. The UI enters `finally` and sets `setRunning(false)` immediately.
  3. The UI runs a blind timeout 4 seconds later calling `loadBriefings()`.
  4. The background task crashed in 15ms due to the `BriefingEngine` `ImportError`.
  5. The UI shows no error, re-renders idle state, and nothing appears to happen.
  6. The UI provides two buttons (Morning / Evening) rather than a single unified "Run briefing" button.
  7. There is no background task attachment, no polling of `/api/briefings/status`, and no persistence across page navigation.

### C. Automation & Slot Logic Deficiencies
- **Location**: `src/noxai/briefing/scheduler.py:163-169`
  ```python
  for slot, slot_time in SLOT_TIMES:
      if now_time.hour == slot_time.hour and now_time.minute == slot_time.minute:
          task = await self.trigger_slot(...)
  ```
- **Flaws**:
  1. Exact-minute matching: If the machine is suspended or the daemon is restarting at `04:00` or `18:00`, the single-minute window is missed forever.
  2. Boot catch-up bug (`catchup_on_boot`):
     ```python
     if now_time >= slot_time:
     ```
     When booted at 19:30, it sees `now_time >= 04:00` AND `now_time >= 18:00`, triggering both morning and evening slots simultaneously!
  3. Window definition: Lacks defined windows (`[04:00, 18:00)` for morning of date D, and `[18:00, 04:00)` for evening of date D).
  4. Missed recording: Does not mark missed slots as `state='missed'` when a window completely expires without execution.
  5. Dual schedulers: Legacy `JobScheduler` has a `daily-briefing` job while `BriefingScheduler` also attempts to manage briefings.

---

## 4. Live Reproduction of Broken Button
Command:
```bash
python3 -c "
from pathlib import Path
import urllib.request, json
token = (Path.home() / '.local/share/nox/session_token').read_text().strip()
req = urllib.request.Request(
    'http://127.0.0.1:7777/api/briefings/run',
    data=json.dumps({'slot': 'morning', 'force': True}).encode('utf-8'),
    headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json', 'X-Nox-Client': 'web'},
    method='POST'
)
with urllib.request.urlopen(req) as resp:
    print(resp.status, resp.read().decode())
"
```
Output:
```text
200 {"ok":true,"task_id":"task_054d05d6fb02","slot":"morning","status":"queued"}
```
Database check 2 seconds later:
```text
{'id': 'task_054d05d6fb02', 'status': 'failed', 'error': "cannot import name 'BriefingEngine' from 'noxai.briefing.engine' (/home/boss/Documents/Projects/NoX/src/noxai/briefing/engine.py)", 'attempts': 3}
```
Root cause verified live.

---

## 5. Machine Suspend & Boot Scenarios
- When machine is in suspend during `04:00` or `18:00`: Scheduler loop was asleep; upon wake-up, `now_time.hour == slot_time.hour` was false; slot was completely skipped.
- No tick drift calculation existed (`gap > 2 minutes`), preventing immediate re-evaluation upon suspend wake.
