"""Planned jobs engine for NoX (Session L).

Provides plan-first execution, deterministic backup templates, validation,
cloud planning with privacy sanitization, heartbeat watchdogs, catchup logic,
and soft-delete / purge controls.
"""

from .schema import StepKind, JobStep, JobPlan, StepRetryPolicy
from .validator import validate_plan, PlanValidationError
from .privacy import sanitize_instruction, resolve_plan, assert_no_paths_or_urls
from .template import create_backup_template_plan, calculate_folder_summary
from .planner import generate_plan_for_job, approve_job_plan
from .executor import execute_planned_job
from .watchdog import repair_orphaned_runs
from .catchup import compute_due_slot, is_job_due
from .deletion import (
    delete_job,
    delete_task,
    clear_finished_tasks,
    delete_briefing,
    restore_job,
    restore_task,
    restore_briefing,
    purge_expired,
)

__all__ = [
    "StepKind",
    "JobStep",
    "JobPlan",
    "StepRetryPolicy",
    "validate_plan",
    "PlanValidationError",
    "sanitize_instruction",
    "resolve_plan",
    "assert_no_paths_or_urls",
    "create_backup_template_plan",
    "calculate_folder_summary",
    "generate_plan_for_job",
    "approve_job_plan",
    "execute_planned_job",
    "repair_orphaned_runs",
    "compute_due_slot",
    "is_job_due",
    "delete_job",
    "delete_task",
    "clear_finished_tasks",
    "delete_briefing",
    "restore_job",
    "restore_task",
    "restore_briefing",
    "purge_expired",
]
