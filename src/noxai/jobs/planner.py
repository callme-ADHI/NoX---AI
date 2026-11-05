"""Plan creation engine: Cloud Planner via KeyPool with deterministic fallback (Phase 1.1)."""

from __future__ import annotations

import json
import logging

from noxai.config import Settings, get_config
from noxai.db import execute, fetch_one, get_db
from noxai.jobs.privacy import assert_no_paths_or_urls, resolve_plan, sanitize_instruction
from noxai.jobs.schema import JobPlan
from noxai.jobs.template import create_backup_template_plan, get_default_backup_folders
from noxai.jobs.validator import validate_plan
from noxai.models.keypool import AllocationMode, KeyPool, get_default_keypool

log = logging.getLogger(__name__)

PLANNER_SYSTEM_PROMPT = """You are the NoX Job Planner.
Your role is to analyze a sanitized job instruction (where all paths are placeholders like {{folder_1}}, {{remote_1}})
and produce an ordered, deterministic JSON plan composed ONLY of whitelisted step kinds.

Whitelisted step kinds:
- fs.scan_changes: Inspect folder for tracked or untracked file modifications.
- scan.secrets: Exclude sensitive credentials (.env, pem, id_rsa, tokens) from staging.
- scan.large_files: Exclude files >50MB from staging.
- git.preflight: Non-interactive auth check with git ls-remote.
- git.ensure_repo: Ensure folder is initialized as git repo with origin remote.
- git.verify_remote_private: Verify repository is private (unauthenticated GET returns 404).
- git.stage: Stage changes with git add -A.
- git.commit: Deterministic commit message with timestamp.
- git.push: Push fast-forward to remote origin.
- git.verify_pushed: Verify remote HEAD matches local commit hash.
- notify: Deliver summary notification.

Output STRICT JSON only matching this format:
{
  "description": "Short description of the plan",
  "steps": [
    {
      "step_index": 1,
      "kind": "git.preflight",
      "folder_alias": "alias",
      "folder_path": "{{folder_1}}",
      "remote": "{{remote_1}}",
      "timeout_seconds": 30
    }
  ]
}
No shell commands, no bash, no extra keys."""


async def generate_plan_for_job(
    job_id: str,
    instruction: str,
    config: Settings | None = None,
    force_template: bool = False,
) -> JobPlan:
    """Produce an approved or draft plan for a job.
    Uses cloud LLM via KeyPool with sanitized placeholders; falls back to deterministic template.
    """
    cfg = config or get_config()
    sanitized = sanitize_instruction(instruction)
    assert_no_paths_or_urls(sanitized.sanitized_text)

    # Determine next plan version
    async with get_db() as db:
        row = await fetch_one(
            db,
            "SELECT MAX(version) FROM job_plans WHERE job_id = ?",
            (job_id,),
        )
        current_max = row[0] if row and row[0] is not None else 0
        version = current_max + 1

    plan: JobPlan | None = None
    source = "template"

    if not force_template and cfg.models.nvidia.enabled:
        pool = get_default_keypool(cfg)
        if not pool.is_empty() and pool.healthy_keys_count > 0:
            try:
                plan = await _call_cloud_planner(
                    sanitized_instruction=sanitized.sanitized_text,
                    job_id=job_id,
                    version=version,
                    pool=pool,
                    cfg=cfg,
                )
                if plan:
                    source = "cloud"
            except Exception as exc:
                log.warning(
                    "Cloud planner failed for job %s: %s; falling back to template.", job_id, exc
                )

    if not plan:
        # Fallback to deterministic template planner
        folders = (
            list(sanitized.folder_map.values())
            if sanitized.folder_map
            else get_default_backup_folders()
        )
        plan = create_backup_template_plan(
            job_id=job_id,
            folders=folders,
            remotes=None,
            version=version,
        )
        source = "template"

    # Fill real paths back into plan for local persistence and UI preview
    plan_dict = plan.model_dump()
    resolved_dict = resolve_plan(plan_dict, sanitized)
    resolved_plan = JobPlan.model_validate(resolved_dict)
    resolved_plan.source = source
    resolved_plan.version = version

    # Validate resolved plan
    val = validate_plan(resolved_plan, allowed_roots=cfg.jobs.allowed_roots)
    if not val.is_valid:
        log.warning(
            "Resolved plan failed validation (%s); regenerating canonical template.",
            val.error_summary,
        )
        resolved_plan = create_backup_template_plan(job_id=job_id, version=version)
        resolved_plan.source = "template"

    # Persist in job_plans table
    async with get_db() as db:
        # Mark previous plans superseded
        await execute(
            db,
            "UPDATE job_plans SET status = 'superseded' WHERE job_id = ? AND status != 'superseded'",
            (job_id,),
        )
        await execute(
            db,
            """
            INSERT INTO job_plans (job_id, version, steps_json, source, status, created_at)
            VALUES (?, ?, ?, ?, 'draft', datetime('now'))
            """,
            (job_id, version, resolved_plan.model_dump_json(), source),
        )
        await db.commit()

    return resolved_plan


async def _call_cloud_planner(
    sanitized_instruction: str,
    job_id: str,
    version: int,
    pool: KeyPool,
    cfg: Settings,
) -> JobPlan | None:
    """Call cloud model with sanitized instruction and validate response."""
    messages = [
        {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Create a step-by-step plan for this job: {sanitized_instruction}",
        },
    ]

    async with pool.lease(job_id=job_id, priority=1, mode=AllocationMode.MULTI_JOB) as lease:
        req_body = {
            "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
            "messages": messages,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        # Double-check outbound payload
        assert_no_paths_or_urls(req_body)

        resp = await lease.client.post(
            "/chat/completions",
            json=req_body,
            headers={"Authorization": f"Bearer {lease.api_key}"},
            timeout=30.0,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Cloud API HTTP {resp.status_code}: {resp.text[:100]}")

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        plan_json = json.loads(content)

        # Basic shape check
        plan_json["job_id"] = job_id
        plan_json["version"] = version
        plan_json["source"] = "cloud"
        plan_json["status"] = "draft"
        return JobPlan.model_validate(plan_json)


async def approve_job_plan(job_id: str, version: int | None = None) -> JobPlan:
    """Approve a draft plan, granting execution authorization for its scoped steps and roots."""
    async with get_db() as db:
        if version is not None:
            row = await fetch_one(
                db,
                "SELECT steps_json, source, status, created_at FROM job_plans WHERE job_id = ? AND version = ?",
                (job_id, version),
            )
        else:
            row = await fetch_one(
                db,
                "SELECT steps_json, source, status, created_at, version FROM job_plans WHERE job_id = ? ORDER BY version DESC LIMIT 1",
                (job_id,),
            )
        if not row:
            raise ValueError(f"No plan found for job '{job_id}'")

        v = version or row[4]
        await execute(
            db,
            "UPDATE job_plans SET status = 'approved' WHERE job_id = ? AND version = ?",
            (job_id, v),
        )
        await db.commit()

        plan_data = json.loads(row[0])
        plan_data["status"] = "approved"
        return JobPlan.model_validate(plan_data)
