-- Migration 022: Authoritative Scheduler Convergence (C-1)
-- Gate A: BriefingScheduler is the sole authority for morning and evening briefings.
-- PersistentScheduler is the sole authority for daily backups and system maintenance.
-- JobScheduler is the sole authority for conversational reminders and ad-hoc chat jobs.

-- 1. Ensure duplicate briefing schedules are disabled in persistent schedules table
UPDATE schedules
SET enabled = 0
WHERE name IN ('briefing_morning', 'briefing_evening');

-- 2. Ensure duplicate briefing and backup jobs are disabled in legacy jobs table
UPDATE jobs
SET enabled = 0
WHERE name IN ('daily-briefing', 'BackUp');
