"""Planned jobs engine for NoX (Session L).

Provides plan-first execution, deterministic backup templates, validation,
cloud planning with privacy sanitization, heartbeat watchdogs, catchup logic,
and soft-delete / purge controls.
"""

from .catchup import compute_due_slot, is_job_due
from .deletion import (
    clear_finished_tasks,
    delete_briefing,
    delete_job,
    delete_task,
    purge_expired,
    restore_briefing,
    restore_job,
    restore_task,
)
from .executor import execute_planned_job
from .planner import approve_job_plan, generate_plan_for_job
from .privacy import assert_no_paths_or_urls, resolve_plan, sanitize_instruction
from .schema import JobPlan, JobStep, StepKind, StepRetryPolicy
from .template import calculate_folder_summary, create_backup_template_plan
from .validator import PlanValidationError, validate_plan
from .watchdog import repair_orphaned_runs

__all__ = [
    "JobPlan",
    "JobStep",
    "PlanValidationError",
    "StepKind",
    "StepRetryPolicy",
    "approve_job_plan",
    "assert_no_paths_or_urls",
    "calculate_folder_summary",
    "clear_finished_tasks",
    "compute_due_slot",
    "create_backup_template_plan",
    "delete_briefing",
    "delete_job",
    "delete_task",
    "execute_planned_job",
    "generate_plan_for_job",
    "is_job_due",
    "purge_expired",
    "repair_orphaned_runs",
    "resolve_plan",
    "restore_briefing",
    "restore_job",
    "restore_task",
    "sanitize_instruction",
    "validate_plan",
]
