"""Static Validator for Planned Jobs (Phase 1.1)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from noxai.jobs.schema import WHITELISTED_STEP_KINDS, JobPlan

FORBIDDEN_OPERATIONS = {
    "--force",
    "-f",
    "force",
    "rebase",
    "reset --hard",
    "clean -f",
    "rm -rf",
    "drop table",
    "delete from",
    "format",
    "mkfs",
}

MAX_STEPS_ALLOWED = 50


class PlanValidationError(Exception):
    """Raised when a job plan fails validation."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str] = field(default_factory=list)

    @property
    def error_summary(self) -> str:
        return "; ".join(self.errors)


def validate_plan(
    plan: JobPlan | dict[str, Any],
    allowed_roots: list[str] | None = None,
) -> ValidationResult:
    """Validate that a job plan strictly complies with execution constraints."""
    errors: list[str] = []

    if isinstance(plan, dict):
        try:
            plan = JobPlan.model_validate(plan)
        except Exception as exc:
            return ValidationResult(is_valid=False, errors=[f"Plan structure invalid: {exc}"])

    if len(plan.steps) == 0:
        errors.append("Plan contains 0 steps")
    elif len(plan.steps) > MAX_STEPS_ALLOWED:
        errors.append(
            f"Plan exceeds maximum allowed steps ({len(plan.steps)} > {MAX_STEPS_ALLOWED})"
        )

    roots = [os.path.expanduser(r).rstrip("/") for r in (allowed_roots or ["~/Documents"])]

    # Validate index ordering
    indices = [s.step_index for s in plan.steps]
    if indices != list(range(1, len(plan.steps) + 1)):
        errors.append(f"Step indices must be sequential 1..N, got {indices}")

    for step in plan.steps:
        idx = step.step_index
        # 1. Whitelisted kind
        if step.kind not in WHITELISTED_STEP_KINDS:
            errors.append(f"Step {idx}: forbidden or unknown kind '{step.kind}'")

        # 2. Path security
        fpath = step.folder_path
        if not fpath.startswith("{{folder_"):
            expanded = os.path.expanduser(fpath).rstrip("/")
            # Check inside allowed root
            if not any(expanded == r or expanded.startswith(r + "/") for r in roots):
                errors.append(f"Step {idx}: path '{fpath}' is outside allowed roots ({roots})")

        # 3. No shell strings or forbidden commands in params or remote
        serialized = f"{step.folder_path} {step.remote or ''} {step.params}".lower()
        for forbidden in FORBIDDEN_OPERATIONS:
            if forbidden in serialized:
                errors.append(f"Step {idx}: forbidden operation/argument '{forbidden}' detected")

        # Check for shell metacharacters
        for char in [";", "&&", "||", "`", "$("]:
            if char in serialized:
                errors.append(f"Step {idx}: forbidden shell metacharacter '{char}' in parameters")

        # 4. Timeout and retry policy
        if step.timeout_seconds < 5 or step.timeout_seconds > 1800:
            errors.append(f"Step {idx}: invalid timeout {step.timeout_seconds}s (must be 5-1800s)")

        if step.retry.max_attempts < 1 or step.retry.max_attempts > 10:
            errors.append(f"Step {idx}: invalid retry attempts {step.retry.max_attempts}")

    return ValidationResult(is_valid=len(errors) == 0, errors=errors)
