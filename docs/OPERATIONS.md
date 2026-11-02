# NoX Operational Runbook & Maintenance Guide

This runbook covers administration, monitoring, disaster recovery, and maintenance for NoX on Arch Linux.

---

## 1. Service Management (`noxd.service`)

The NoX daemon runs as a persistent systemd user service.

```bash
# Check service status and recent journal logs
systemctl --user status noxd.service

# View live daemon logs
journalctl --user -u noxd.service -f

# Restart daemon
systemctl --user restart noxd.service

# Stop daemon
systemctl --user stop noxd.service
```

### Crash Recovery
The systemd user unit specifies `Restart=on-failure` and `RestartSec=3`. If the daemon is terminated abruptly (`kill -9 <pid>`), systemd revives the service within 3 seconds. The scheduler and supervisor automatically resume interrupted tasks.

---

## 2. Database Backup & Disaster Recovery

NoX databases are stored at `~/.local/share/nox/nox.db` in SQLite WAL mode.

### Automatic Backups
Backups are created before any destructive operation, schema migration, or maintenance run:
- **Location**: `~/.local/share/nox/backups/`
- **Naming**: `nox_YYYYMMDD_HHMMSS_<hash>.db`
- **Retention**: Last 10 backups retained automatically.

### Manual Backup Commands
```bash
# Create a fresh timestamped backup
nx backup create

# List existing backups
nx backup list

# Restore from a backup
nx backup restore ~/.local/share/nox/backups/nox_20261102_144630_83576c.db
```

---

## 3. Briefing Scheduler Auditing & Repair

The briefing scheduler runs automatically at **04:00 (Morning)** and **18:00 (Evening)** in `Asia/Kolkata`.

### Inspecting Reliability
```bash
# View last 6 briefing slots, item counts, durations, and drop reasons
nx briefing audit

# Check system health and reliability metrics
nx doctor
```

### Repairing Interrupted Runs
If a machine was powered off mid-run or crashed:
```bash
# Identifies orphaned runs in 'running' state, marks them interrupted, and re-queues them
nx briefing repair
```

---

## 4. Cloud Inference & KeyPool Operations

NoX uses a 5-key KeyPool allocator for NVIDIA NIM cloud inference with circuit breaking.

### Cloud Status & Toggling
```bash
# Inspect current cloud status, health, and active keys
nx cloud status

# Inspect KeyPool lease status, quota consumption, and rate budgets
nx keys status

# Disable cloud bursting (forces 100% local inference)
nx cloud off

# Re-enable cloud bursting
nx cloud on
```

### Secrets File Permissions
The API secrets file must be protected with `0600` permissions:
```bash
chmod 600 ~/.config/nox/secrets.env
```
