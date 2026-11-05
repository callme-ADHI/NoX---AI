"""Unit tests for static plan validator (Section 4.1 & 8.1)."""

from noxai.jobs.schema import JobPlan, JobStep, StepKind
from noxai.jobs.validator import validate_plan


def test_validator_accepts_valid_plan():
    plan = JobPlan(
        job_id="job_valid",
        version=1,
        source="template",
        status="draft",
        description="Valid test plan",
        steps=[
            JobStep(
                step_index=1,
                kind=StepKind.GIT_PREFLIGHT.value,
                folder_alias="Study",
                folder_path="~/Documents/Study",
                timeout_seconds=30,
            ),
            JobStep(
                step_index=2,
                kind=StepKind.FS_SCAN_CHANGES.value,
                folder_alias="Study",
                folder_path="~/Documents/Study",
                timeout_seconds=30,
            ),
        ],
    )
    res = validate_plan(plan, allowed_roots=["~/Documents"])
    assert res.is_valid is True
    assert len(res.errors) == 0


def test_validator_rejects_out_of_root_path():
    plan = JobPlan(
        job_id="job_bad_path",
        version=1,
        source="template",
        status="draft",
        steps=[
            JobStep(
                step_index=1,
                kind=StepKind.GIT_PREFLIGHT.value,
                folder_alias="Etc",
                folder_path="/etc/passwd",
                timeout_seconds=30,
            )
        ],
    )
    res = validate_plan(plan, allowed_roots=["~/Documents"])
    assert res.is_valid is False
    assert any("outside allowed roots" in e for e in res.errors)


def test_validator_rejects_shell_metacharacters():
    plan = JobPlan(
        job_id="job_shell",
        version=1,
        source="template",
        status="draft",
        steps=[
            JobStep(
                step_index=1,
                kind=StepKind.GIT_PREFLIGHT.value,
                folder_alias="Study",
                folder_path="~/Documents/Study; rm -rf /",
                timeout_seconds=30,
            )
        ],
    )
    res = validate_plan(plan, allowed_roots=["~/Documents"])
    assert res.is_valid is False
    assert any(
        "forbidden shell metacharacter" in e or "forbidden operation" in e for e in res.errors
    )


def test_validator_rejects_force_operations():
    plan = JobPlan(
        job_id="job_force",
        version=1,
        source="template",
        status="draft",
        steps=[
            JobStep(
                step_index=1,
                kind=StepKind.GIT_PUSH.value,
                folder_alias="Study",
                folder_path="~/Documents/Study",
                params={"extra_args": ["--force"]},
                timeout_seconds=60,
            )
        ],
    )
    res = validate_plan(plan, allowed_roots=["~/Documents"])
    assert res.is_valid is False
    assert any("forbidden operation" in e for e in res.errors)
