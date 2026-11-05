"""Step-by-step Planned Job Executor under TaskSupervisor (Section 4.3)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
import uuid

from noxai.config import Settings, get_config
from noxai.db import execute, fetch_all, fetch_one, get_db
from noxai.jobs.schema import JobPlan, JobStep, StepKind
from noxai.jobs.steps import (
    StepResult,
    step_fs_scan_changes,
    step_git_commit,
    step_git_ensure_repo,
    step_git_preflight,
    step_git_push,
    step_git_stage,
    step_git_verify_pushed,
    step_git_verify_remote_private,
    step_notify,
    step_scan_large_files,
    step_scan_secrets,
)

log = logging.getLogger(__name__)


async def execute_planned_job(
    job_id: str,
    run_id: str,
    cancel_event: asyncio.Event | None = None,
    config: Settings | None = None,
) -> str:
    cfg = config or get_config()
    start_time = time.monotonic()

    # 1. Load active approved plan
    async with get_db() as db:
        plan_row = await fetch_one(
            db,
            """
            SELECT version, steps_json, status, source FROM job_plans
            WHERE job_id = ? AND status = 'approved'
            ORDER BY version DESC LIMIT 1
            """,
            (job_id,),
        )
        if not plan_row:
            draft_row = await fetch_one(
                db,
                """
                SELECT version FROM job_plans WHERE job_id = ? AND status = 'draft'
                ORDER BY version DESC LIMIT 1
                """,
                (job_id,),
            )
            if draft_row:
                await execute(
                    db,
                    """
                    INSERT INTO job_runs (id, job_id, started_at, status, output_markdown, error)
                    VALUES (?, ?, datetime('now'), 'waiting_approval', 'Plan awaiting user approval', 'Plan not approved')
                    ON CONFLICT(id) DO UPDATE SET status = 'waiting_approval'
                    """,
                    (run_id, job_id),
                )
                await db.commit()
                return "waiting_approval"
            else:
                from noxai.jobs.planner import generate_plan_for_job

                await generate_plan_for_job(
                    job_id, "Backup daily documents", config=cfg, force_template=True
                )
                return "waiting_approval"

        plan_version = plan_row[0]
        plan_data = json.loads(plan_row[1])
        plan = JobPlan.model_validate(plan_data)

        # Ensure job_runs record exists and is set to running
        await execute(
            db,
            """
            INSERT INTO job_runs (id, job_id, started_at, status, heartbeat_at, plan_version)
            VALUES (?, ?, datetime('now'), 'running', datetime('now'), ?)
            ON CONFLICT(id) DO UPDATE SET status = 'running', heartbeat_at = datetime('now'), plan_version = ?
            """,
            (run_id, job_id, plan_version, plan_version),
        )

        existing_steps = await fetch_all(
            db,
            "SELECT step_index, state FROM job_run_steps WHERE run_id = ?",
            (run_id,),
        )
        existing_indices = {r[0]: r[1] for r in existing_steps}

        for s in plan.steps:
            if s.step_index not in existing_indices:
                step_id = f"step_{run_id}_{s.step_index}"
                await execute(
                    db,
                    """
                    INSERT INTO job_run_steps (id, run_id, step_index, kind, folder_alias, params_redacted, state)
                    VALUES (?, ?, ?, ?, ?, ?, 'pending')
                    """,
                    (step_id, run_id, s.step_index, s.kind, s.folder_alias, json.dumps(s.params)),
                )
        await db.commit()

    # 2. Start periodic heartbeat background task
    stop_heartbeat = asyncio.Event()

    async def _heartbeat_worker() -> None:
        while not stop_heartbeat.is_set():
            try:
                async with get_db() as h_db:
                    await execute(
                        h_db,
                        "UPDATE job_runs SET heartbeat_at = datetime('now') WHERE id = ?",
                        (run_id,),
                    )
                    await h_db.commit()
            except Exception:
                pass
            try:
                await asyncio.wait_for(stop_heartbeat.wait(), timeout=cfg.jobs.heartbeat_seconds)
            except TimeoutError:
                pass

    hb_task = asyncio.create_task(_heartbeat_worker())

    folder_failed: set[str] = set()
    folder_skipped_all: set[str] = set()
    folder_succeeded: set[str] = set()
    folder_needs_setup: set[str] = set()
    folder_files_changed: dict[str, int] = {}
    folder_commits: dict[str, str] = {}
    all_skipped_secrets: list[str] = []
    all_large_files: list[str] = []

    final_status = "succeeded"

    try:
        for s in plan.steps:
            if cancel_event and cancel_event.is_set():
                final_status = "cancelled"
                break

            if time.monotonic() - start_time > cfg.jobs.default_timeout_seconds:
                final_status = "failed"
                log.error(
                    "Job run %s exceeded hard timeout of %ds",
                    run_id,
                    cfg.jobs.default_timeout_seconds,
                )
                break

            alias = s.folder_alias

            if alias != "all" and (
                alias in folder_failed or alias in folder_skipped_all or alias in folder_needs_setup
            ):
                async with get_db() as db:
                    await execute(
                        db,
                        "UPDATE job_run_steps SET state = 'skipped', finished_at = datetime('now') WHERE run_id = ? AND step_index = ?",
                        (run_id, s.step_index),
                    )
                    await db.commit()
                continue

            async with get_db() as db:
                step_row = await fetch_one(
                    db,
                    "SELECT state FROM job_run_steps WHERE run_id = ? AND step_index = ?",
                    (run_id, s.step_index),
                )
                if step_row and step_row[0] in ("ok", "skipped"):
                    continue

                await execute(
                    db,
                    """
                    UPDATE job_run_steps
                    SET state = 'running', started_at = datetime('now')
                    WHERE run_id = ? AND step_index = ?
                    """,
                    (run_id, s.step_index),
                )
                await execute(
                    db,
                    """
                    UPDATE job_runs
                    SET current_step_index = ?, current_step_kind = ?, current_folder = ?
                    WHERE id = ?
                    """,
                    (s.step_index, s.kind, s.folder_alias, run_id),
                )
                await db.commit()

            step_res: StepResult | None = None
            attempts = 0
            max_att = s.retry.max_attempts

            while attempts < max_att:
                attempts += 1
                try:
                    step_res = await _dispatch_step(s, folder_files_changed.get(alias, 0), cfg)
                    if step_res.ok or not step_res.transient or attempts >= max_att:
                        break
                    backoff = min(
                        s.retry.max_backoff_seconds,
                        s.retry.backoff_base_seconds * (2 ** (attempts - 1))
                        + random.uniform(0.1, 1.0),
                    )
                    if os.getenv("NOX_TEST_FAST_BACKOFF") == "1":
                        backoff = 0.05
                    log.warning(
                        "Step %s on %s failed transiently; retrying in %.1fs",
                        s.kind,
                        alias,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                except Exception as exc:
                    step_res = StepResult(ok=False, state="failed", error=str(exc))
                    break

            if not step_res:
                step_res = StepResult(ok=False, state="failed", error="Unknown execution failure")

            async with get_db() as db:
                await execute(
                    db,
                    """
                    UPDATE job_run_steps
                    SET state = ?, output_excerpt_redacted = ?, error = ?, finished_at = datetime('now'), attempts = ?
                    WHERE run_id = ? AND step_index = ?
                    """,
                    (
                        step_res.state,
                        step_res.output_excerpt,
                        step_res.error,
                        attempts,
                        run_id,
                        s.step_index,
                    ),
                )
                await db.commit()

            if step_res.skipped_files:
                all_skipped_secrets.extend(step_res.skipped_files)
            if step_res.large_files:
                all_large_files.extend(step_res.large_files)
            if step_res.commit_hash:
                folder_commits[alias] = step_res.commit_hash

            if not step_res.ok:
                if "Setup required" in (step_res.error or ""):
                    folder_needs_setup.add(alias)
                else:
                    folder_failed.add(alias)
            else:
                if s.kind == StepKind.FS_SCAN_CHANGES.value:
                    folder_files_changed[alias] = step_res.files_changed
                    if step_res.files_changed == 0:
                        folder_skipped_all.add(alias)
                        folder_succeeded.add(alias)
                elif s.kind == StepKind.GIT_VERIFY_PUSHED.value:
                    folder_succeeded.add(alias)

        if cancel_event and cancel_event.is_set():
            final_status = "cancelled"
        elif folder_needs_setup:
            final_status = "needs_setup"
        elif folder_failed:
            if folder_succeeded:
                final_status = "partial"
            else:
                final_status = "failed"
        else:
            final_status = "succeeded"

    finally:
        stop_heartbeat.set()
        await hb_task

    duration_s = round(time.monotonic() - start_time, 1)

    summary_text = _build_summary(
        final_status=final_status,
        duration_s=duration_s,
        succeeded=folder_succeeded,
        failed=folder_failed,
        skipped_clean=folder_skipped_all,
        needs_setup=folder_needs_setup,
        commits=folder_commits,
        secrets=all_skipped_secrets,
        large_files=all_large_files,
    )

    async with get_db() as db:
        await execute(
            db,
            """
            UPDATE job_runs
            SET status = ?, finished_at = datetime('now'), output_markdown = ?,
                current_step_index = NULL, current_step_kind = NULL, current_folder = NULL
            WHERE id = ?
            """,
            (final_status, summary_text, run_id),
        )
        if final_status in ("succeeded", "partial"):
            await execute(
                db,
                "UPDATE jobs SET last_run_at = datetime('now'), updated_at = datetime('now') WHERE id = ?",
                (job_id,),
            )
        notif_id = f"notif_{uuid.uuid4().hex[:12]}"
        notif_level = "critical" if final_status in ("failed", "needs_setup") else "normal"
        await execute(
            db,
            """
            INSERT INTO notifications (id, level, title, message, category, payload_json)
            VALUES (?, ?, ?, ?, 'jobs', ?)
            """,
            (
                notif_id,
                notif_level,
                f"Backup {final_status.capitalize()}",
                summary_text[:300],
                json.dumps({"job_id": job_id, "run_id": run_id, "status": final_status}),
            ),
        )
        await db.commit()

    await step_notify(summary_text[:120], is_error=(final_status in ("failed", "needs_setup")))
    return final_status


async def _dispatch_step(step: JobStep, files_changed: int, cfg: Settings) -> StepResult:
    k = step.kind
    f = step.folder_path
    r = step.remote
    t = float(step.timeout_seconds)

    if k == StepKind.GIT_PREFLIGHT.value:
        return await step_git_preflight(folder=f, remote=r, timeout_sec=t)
    elif k == StepKind.GIT_ENSURE_REPO.value:
        return await step_git_ensure_repo(folder=f, remote=r, timeout_sec=t)
    elif k == StepKind.GIT_VERIFY_REMOTE_PRIVATE.value:
        return await step_git_verify_remote_private(
            folder=f,
            remote=r,
            allow_unverified=cfg.jobs.backup.allow_unverified_remote,
            timeout_sec=t,
        )
    elif k == StepKind.SCAN_LARGE_FILES.value:
        return await step_scan_large_files(folder=f, timeout_sec=t)
    elif k == StepKind.SCAN_SECRETS.value:
        return await step_scan_secrets(folder=f, timeout_sec=t)
    elif k == StepKind.FS_SCAN_CHANGES.value:
        return await step_fs_scan_changes(folder=f, timeout_sec=t)
    elif k == StepKind.GIT_STAGE.value:
        return await step_git_stage(folder=f, timeout_sec=t)
    elif k == StepKind.GIT_COMMIT.value:
        return await step_git_commit(
            folder=f, folder_alias=step.folder_alias, files_count=files_changed, timeout_sec=t
        )
    elif k == StepKind.GIT_PUSH.value:
        return await step_git_push(folder=f, timeout_sec=t)
    elif k == StepKind.GIT_VERIFY_PUSHED.value:
        return await step_git_verify_pushed(folder=f, timeout_sec=t)
    elif k == StepKind.NOTIFY.value:
        return await step_notify(summary=f"Backup completed for {step.folder_alias}")
    else:
        return StepResult(ok=False, state="failed", error=f"Unknown step kind {k}")


def _build_summary(
    final_status: str,
    duration_s: float,
    succeeded: set[str],
    failed: set[str],
    skipped_clean: set[str],
    needs_setup: set[str],
    commits: dict[str, str],
    secrets: list[str],
    large_files: list[str],
) -> str:
    lines = [f"**Backup Run Outcome**: `{final_status.upper()}` (Duration: {duration_s}s)"]
    if succeeded:
        lines.append(f"\n**Pushed Folders**: {', '.join(sorted(succeeded))}")
        for fld, c_hash in commits.items():
            lines.append(f"- `{fld}`: commit `{c_hash}`")
    if skipped_clean:
        lines.append(f"\n**Clean (Nothing to commit)**: {', '.join(sorted(skipped_clean))}")
    if failed:
        lines.append(f"\n**Failed Folders**: {', '.join(sorted(failed))}")
    if needs_setup:
        lines.append(f"\n**Setup Required (Missing Remote)**: {', '.join(sorted(needs_setup))}")
    if secrets:
        lines.append(f"\n**Secrets Excluded ({len(secrets)})**: {', '.join(secrets[:10])}")
    if large_files:
        lines.append(
            f"\n**Large Files Excluded ({len(large_files)})**: {', '.join(large_files[:10])}"
        )
    return "\n".join(lines)
