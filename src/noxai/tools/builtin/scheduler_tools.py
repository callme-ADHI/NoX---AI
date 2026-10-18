"""Meta-tools for managing jobs, reminders, sources, preferences, skills, and undo
per Section 6.7.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from pydantic import BaseModel, Field

from noxai.db import execute, fetch_all, fetch_one
from noxai.scheduler.time_utils import (
    DEFAULT_TIMEZONE,
    compute_next_run,
    format_human_time,
    parse_natural_time,
)
from noxai.skills.loader import (
    DEFAULT_SKILLS_DIR,
    parse_frontmatter,
)
from noxai.tools.registry import ToolContext, ToolResult, tool

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schedule resolution helper
# ---------------------------------------------------------------------------


def resolve_schedule_expression(schedule_input: str) -> tuple[str, str, str]:
    """Convert cron or natural language schedule into (schedule_type, expr, description)."""
    text = schedule_input.strip()

    # 1. Direct cron expression (5 fields)
    parts = text.split()
    if len(parts) == 5 and all(re.match(r"^[\d\*\-\,\/]+$", p) for p in parts):
        return "cron", text, f"Cron schedule '{text}'"

    # 2. Heuristic conversion for common phrases
    t_lower = text.lower()

    # Time of day (e.g. "6 pm", "18:00", "7:30 am")
    time_match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t_lower)
    hour = 9
    minute = 0
    if time_match:
        h = int(time_match.group(1))
        m = int(time_match.group(2) or 0)
        ampm = time_match.group(3)
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        hour = h
        minute = m

    # Days of week
    dow = "*"
    if "weekday" in t_lower:
        dow = "1-5"
    elif "monday" in t_lower:
        dow = "1"
    elif "tuesday" in t_lower:
        dow = "2"
    elif "wednesday" in t_lower:
        dow = "3"
    elif "thursday" in t_lower:
        dow = "4"
    elif "friday" in t_lower:
        dow = "5"
    elif "saturday" in t_lower:
        dow = "6"
    elif "sunday" in t_lower:
        dow = "0"
    elif "weekend" in t_lower:
        dow = "6,0"

    cron_expr = f"{minute} {hour} * * {dow}"
    human = (
        f"Daily at {hour:02d}:{minute:02d}"
        if dow == "*"
        else f"At {hour:02d}:{minute:02d} on {dow}"
    )
    return "cron", cron_expr, human


# ---------------------------------------------------------------------------
# Job Meta-Tools
# ---------------------------------------------------------------------------


class JobListParams(BaseModel):
    enabled_only: bool = Field(default=False, description="Filter only active jobs")


@tool(
    name="job_list",
    description="List all scheduled jobs, their schedules, and next run times.",
    params=JobListParams,
    tier="read",
)
async def job_list(params: JobListParams, ctx: ToolContext) -> ToolResult:
    """List all scheduled jobs, their schedules, and next run times."""
    if ctx.db is None:
        return ToolResult(ok=False, output="Database not available.", summary="no db")

    sql = (
        "SELECT id, name, description, schedule_type, schedule_expr, timezone, enabled, "
        "next_run_at FROM jobs"
    )
    if params.enabled_only:
        sql += " WHERE enabled = 1"
    sql += " ORDER BY name ASC"

    rows = await fetch_all(ctx.db, sql)
    jobs_data = [
        {
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "schedule": f"{r['schedule_type']}: {r['schedule_expr']}",
            "timezone": r["timezone"],
            "enabled": bool(r["enabled"]),
            "next_run_at": r["next_run_at"],
        }
        for r in rows
    ]
    return ToolResult(
        ok=True,
        output=json.dumps(jobs_data, indent=2),
        summary=f"Found {len(jobs_data)} jobs",
    )


class JobGetParams(BaseModel):
    name: str = Field(..., description="Job name or ID")


@tool(
    name="job_get",
    description="Retrieve full details and recent run history for a job.",
    params=JobGetParams,
    tier="read",
)
async def job_get(params: JobGetParams, ctx: ToolContext) -> ToolResult:
    """Retrieve full details and recent run history for a job."""
    if ctx.db is None:
        return ToolResult(ok=False, output="Database not available.", summary="no db")

    row = await fetch_one(
        ctx.db,
        "SELECT * FROM jobs WHERE name = ? OR id = ?",
        (params.name, params.name),
    )
    if row is None:
        return ToolResult(ok=False, output=f"Job '{params.name}' not found.", summary="not found")

    job_dict = dict(row)
    runs = await fetch_all(
        ctx.db,
        "SELECT id, started_at, finished_at, status, error FROM job_runs "
        "WHERE job_id = ? ORDER BY started_at DESC LIMIT 5",
        (job_dict["id"],),
    )
    job_dict["recent_runs"] = [dict(r) for r in runs]
    return ToolResult(
        ok=True,
        output=json.dumps(job_dict, indent=2),
        summary=f"Details for job {row['name']}",
    )


class JobCreateParams(BaseModel):
    name: str = Field(..., description="Unique job identifier")
    description: str = Field(default="", description="Human-readable job summary")
    schedule: str = Field(
        ...,
        description="Cron expression or natural schedule (e.g. '30 7 * * *', 'every day at 6pm')",
    )
    instruction: str = Field(..., description="Prompt instructions for the agent to execute")
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Job settings (allow_cloud, delivery, sources, etc.)",
    )


@tool(
    name="job_create",
    description="Create a new scheduled job or recurring briefing.",
    params=JobCreateParams,
    tier="write",
    is_meta_tool=True,
)
async def job_create(params: JobCreateParams, ctx: ToolContext) -> ToolResult:
    """Create a new recurring or one-time scheduled agent job."""
    if ctx.db is None:
        return ToolResult(ok=False, output="Database not available.", summary="no db")

    stype, expr, human_sched = resolve_schedule_expression(params.schedule)
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    tz_name = DEFAULT_TIMEZONE
    next_dt = compute_next_run(stype, expr, tz_name)
    next_iso = next_dt.isoformat() if next_dt else None
    cfg_json = json.dumps(params.config)

    try:
        await execute(
            ctx.db,
            """
            INSERT INTO jobs (
                id, name, description, schedule_type, schedule_expr, timezone,
                instruction, config_json, enabled, created_by, version, next_run_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 'agent', 1, ?)
            """,
            (
                job_id,
                params.name,
                params.description,
                stype,
                expr,
                tz_name,
                params.instruction,
                cfg_json,
                next_iso,
            ),
        )

        snapshot = {
            "id": job_id,
            "name": params.name,
            "description": params.description,
            "schedule_type": stype,
            "schedule_expr": expr,
            "timezone": tz_name,
            "instruction": params.instruction,
            "config_json": cfg_json,
            "enabled": 1,
            "version": 1,
        }
        await execute(
            ctx.db,
            """
            INSERT INTO job_versions (job_id, version, snapshot_json, changed_by, change_note)
            VALUES (?, 1, ?, 'agent', 'Created job')
            """,
            (job_id, json.dumps(snapshot)),
        )
        await ctx.db.commit()

        msg = f"Created job '{params.name}' ({human_sched}). Next run: {next_iso}"
        return ToolResult(ok=True, output=msg, summary=f"created job {params.name}")
    except Exception as exc:
        return ToolResult(ok=False, output=f"Failed creating job: {exc}", summary="create error")


class JobUpdateParams(BaseModel):
    name: str = Field(..., description="Job name or ID to update")
    schedule: str | None = Field(default=None, description="New schedule expression or phrase")
    instruction: str | None = Field(default=None, description="New prompt instructions")
    description: str | None = Field(default=None, description="New description")
    enabled: bool | None = Field(default=None, description="Enable or disable job")
    config: dict[str, Any] | None = Field(default=None, description="Updated config values")


@tool(
    name="job_update",
    description="Update an existing scheduled job (e.g. reschedule or edit instruction).",
    params=JobUpdateParams,
    tier="write",
    is_meta_tool=True,
)
async def job_update(params: JobUpdateParams, ctx: ToolContext) -> ToolResult:
    """Update properties of an existing job, recording a version snapshot for undo."""
    if ctx.db is None:
        return ToolResult(ok=False, output="Database not available.", summary="no db")

    row = await fetch_one(
        ctx.db,
        "SELECT * FROM jobs WHERE name = ? OR id = ?",
        (params.name, params.name),
    )
    if row is None:
        return ToolResult(ok=False, output=f"Job '{params.name}' not found.", summary="not found")

    job_id = str(row["id"])
    cur_version = int(row["version"] or 1)
    new_version = cur_version + 1

    updates: list[str] = []
    args: list[Any] = []
    changes: list[str] = []

    stype = str(row["schedule_type"])
    sexpr = str(row["schedule_expr"])
    tz_name = str(row["timezone"] or DEFAULT_TIMEZONE)

    if params.schedule is not None:
        stype, sexpr, human_sched = resolve_schedule_expression(params.schedule)
        next_dt = compute_next_run(stype, sexpr, tz_name)
        updates.extend(["schedule_type = ?", "schedule_expr = ?", "next_run_at = ?"])
        args.extend([stype, sexpr, next_dt.isoformat() if next_dt else None])
        changes.append(f"schedule changed to {human_sched}")

    if params.instruction is not None:
        updates.append("instruction = ?")
        args.append(params.instruction)
        changes.append("instruction updated")

    if params.description is not None:
        updates.append("description = ?")
        args.append(params.description)
        changes.append("description updated")

    if params.enabled is not None:
        updates.append("enabled = ?")
        args.append(1 if params.enabled else 0)
        changes.append("enabled" if params.enabled else "disabled")

    if params.config is not None:
        cur_cfg = json.loads(row["config_json"]) if row["config_json"] else {}
        cur_cfg.update(params.config)
        updates.append("config_json = ?")
        args.append(json.dumps(cur_cfg))
        changes.append("config updated")

    if not updates:
        return ToolResult(ok=True, output="No changes specified.", summary="no change")

    updates.extend(["version = ?", "updated_at = datetime('now')"])
    args.extend([new_version, job_id])

    sql = f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?"  # noqa: S608
    await execute(ctx.db, sql, tuple(args))

    updated_row = await fetch_one(ctx.db, "SELECT * FROM jobs WHERE id = ?", (job_id,))
    snapshot = dict(updated_row) if updated_row else {}

    change_note = "; ".join(changes)
    await execute(
        ctx.db,
        """
        INSERT INTO job_versions (job_id, version, snapshot_json, changed_by, change_note)
        VALUES (?, ?, ?, 'agent', ?)
        """,
        (job_id, new_version, json.dumps(snapshot), change_note),
    )
    await ctx.db.commit()

    msg = f"Updated job '{row['name']}' to version {new_version}: {change_note}"
    return ToolResult(ok=True, output=msg, summary=f"updated job {row['name']}")


class JobToggleParams(BaseModel):
    name: str = Field(..., description="Job name or ID")


@tool(
    name="job_enable",
    description="Enable a scheduled job.",
    params=JobToggleParams,
    tier="write",
    is_meta_tool=True,
)
async def job_enable(params: JobToggleParams, ctx: ToolContext) -> ToolResult:
    res = await job_update(JobUpdateParams(name=params.name, enabled=True), ctx)
    return cast(ToolResult, res)


@tool(
    name="job_disable",
    description="Disable a scheduled job.",
    params=JobToggleParams,
    tier="write",
    is_meta_tool=True,
)
async def job_disable(params: JobToggleParams, ctx: ToolContext) -> ToolResult:
    res = await job_update(JobUpdateParams(name=params.name, enabled=False), ctx)
    return cast(ToolResult, res)


@tool(
    name="job_delete",
    description="Delete a scheduled job.",
    params=JobToggleParams,
    tier="write",
    is_meta_tool=True,
)
async def job_delete(params: JobToggleParams, ctx: ToolContext) -> ToolResult:
    if ctx.db is None:
        return ToolResult(ok=False, output="Database not available.", summary="no db")

    row = await fetch_one(
        ctx.db,
        "SELECT id, name FROM jobs WHERE name = ? OR id = ?",
        (params.name, params.name),
    )
    if row is None:
        return ToolResult(ok=False, output=f"Job '{params.name}' not found.", summary="not found")

    await execute(ctx.db, "DELETE FROM jobs WHERE id = ?", (str(row["id"]),))
    await ctx.db.commit()
    return ToolResult(
        ok=True, output=f"Deleted job '{row['name']}'.", summary=f"deleted {row['name']}"
    )


@tool(
    name="job_run_now",
    description="Run a scheduled job immediately on demand.",
    params=JobToggleParams,
    tier="write",
    is_meta_tool=True,
)
async def job_run_now(params: JobToggleParams, ctx: ToolContext) -> ToolResult:
    if ctx.db is None:
        return ToolResult(ok=False, output="Database not available.", summary="no db")
    if ctx.router is None:
        return ToolResult(ok=False, output="Model router not available.", summary="no router")

    from noxai.scheduler.scheduler import JobScheduler
    from noxai.tools.registry import default_registry

    reg = getattr(ctx, "registry", None) or default_registry
    scheduler = JobScheduler(
        router=ctx.router,
        registry=reg,
        config=ctx.config,
    )
    try:
        res = await scheduler.run_job_now(params.name, ctx.db)
        return ToolResult(
            ok=(res["status"] == "ok"),
            output=res["output"],
            summary=f"executed job {params.name} ({res['status']})",
        )
    except Exception as exc:
        return ToolResult(ok=False, output=f"Error executing job: {exc}", summary="execution error")


# ---------------------------------------------------------------------------
# Reminder Meta-Tools
# ---------------------------------------------------------------------------


class ReminderCreateParams(BaseModel):
    message: str = Field(..., description="Reminder text to display")
    when_text: str = Field(
        ..., description="Time to trigger (e.g. 'in 20 minutes', 'tomorrow at 9am', '5pm')"
    )
    delivery: list[str] = Field(
        default_factory=lambda: ["ui", "desktop"], description="Delivery channels"
    )


@tool(
    name="reminder_create",
    description="Create a reminder for a future time with natural language expression.",
    params=ReminderCreateParams,
    tier="write",
    is_meta_tool=True,
)
async def reminder_create(params: ReminderCreateParams, ctx: ToolContext) -> ToolResult:
    if ctx.db is None:
        return ToolResult(ok=False, output="Database not available.", summary="no db")

    target_dt = parse_natural_time(params.when_text, tz_name=DEFAULT_TIMEZONE)
    if target_dt is None:
        return ToolResult(
            ok=False,
            output=f"Could not parse time '{params.when_text}'. Please specify a clearer time.",
            summary="parse time error",
        )

    human_str = format_human_time(target_dt, tz_name=DEFAULT_TIMEZONE)
    job_id = f"rem_{uuid.uuid4().hex[:10]}"
    job_name = f"reminder-{job_id}"
    iso_time = target_dt.isoformat()

    cfg = {
        "kind": "reminder",
        "message": params.message,
        "delivery": params.delivery,
        "catchup": "once",
    }

    await execute(
        ctx.db,
        """
        INSERT INTO jobs (
            id, name, description, schedule_type, schedule_expr, timezone,
            instruction, config_json, enabled, created_by, version, next_run_at
        )
        VALUES (?, ?, ?, 'once', ?, ?, ?, ?, 1, 'agent', 1, ?)
        """,
        (
            job_id,
            job_name,
            f"Reminder: {params.message[:50]}",
            iso_time,
            DEFAULT_TIMEZONE,
            params.message,
            json.dumps(cfg),
            iso_time,
        ),
    )
    await ctx.db.commit()

    output = f"Reminder set for {human_str}: '{params.message}'"
    return ToolResult(ok=True, output=output, summary=f"reminder set for {human_str}")


class ReminderSnoozeParams(BaseModel):
    reminder_id: str = Field(..., description="Reminder ID or job name")
    minutes: int = Field(default=10, description="Minutes to snooze (+10m, +60m)")


@tool(
    name="reminder_snooze",
    description="Snooze an existing reminder by a specified number of minutes.",
    params=ReminderSnoozeParams,
    tier="write",
    is_meta_tool=True,
)
async def reminder_snooze(params: ReminderSnoozeParams, ctx: ToolContext) -> ToolResult:
    if ctx.db is None:
        return ToolResult(ok=False, output="Database not available.", summary="no db")

    row = await fetch_one(
        ctx.db,
        "SELECT * FROM jobs WHERE id = ? OR name = ?",
        (params.reminder_id, params.reminder_id),
    )
    if row is None:
        return ToolResult(
            ok=False, output=f"Reminder '{params.reminder_id}' not found.", summary="not found"
        )

    new_time = datetime.now(UTC) + timedelta(minutes=params.minutes)
    iso_time = new_time.isoformat()
    human_str = format_human_time(new_time, tz_name=DEFAULT_TIMEZONE)

    await execute(
        ctx.db,
        "UPDATE jobs SET enabled = 1, next_run_at = ?, schedule_expr = ? WHERE id = ?",
        (iso_time, iso_time, str(row["id"])),
    )
    await ctx.db.commit()

    return ToolResult(
        ok=True,
        output=f"Snoozed reminder '{row['name']}' until {human_str}",
        summary=f"snoozed until {human_str}",
    )


class ReminderCancelParams(BaseModel):
    reminder_id: str = Field(..., description="Reminder ID or job name")


@tool(
    name="reminder_cancel",
    description="Cancel an active reminder.",
    params=ReminderCancelParams,
    tier="write",
    is_meta_tool=True,
)
async def reminder_cancel(params: ReminderCancelParams, ctx: ToolContext) -> ToolResult:
    res = await job_disable(JobToggleParams(name=params.reminder_id), ctx)
    return cast(ToolResult, res)


# ---------------------------------------------------------------------------
# Source Meta-Tools
# ---------------------------------------------------------------------------


class SourceListParams(BaseModel):
    tag: str | None = Field(default=None, description="Optional tag filter")


@tool(
    name="source_list",
    description="List configured news and opportunity RSS and web sources.",
    params=SourceListParams,
    tier="read",
)
async def source_list(params: SourceListParams, ctx: ToolContext) -> ToolResult:
    if ctx.db is None:
        return ToolResult(ok=False, output="Database not available.", summary="no db")

    rows = await fetch_all(
        ctx.db, "SELECT id, name, url, kind, tags, enabled FROM sources ORDER BY id ASC"
    )
    sources = []
    for r in rows:
        tags = json.loads(r["tags"]) if r["tags"] else []
        if params.tag and params.tag not in tags:
            continue
        sources.append(
            {
                "id": r["id"],
                "name": r["name"],
                "url": r["url"],
                "kind": r["kind"],
                "tags": tags,
                "enabled": bool(r["enabled"]),
            }
        )

    return ToolResult(
        ok=True, output=json.dumps(sources, indent=2), summary=f"Found {len(sources)} sources"
    )


class SourceAddParams(BaseModel):
    name: str = Field(..., description="Source name")
    url: str = Field(..., description="Feed or webpage URL")
    kind: str = Field(default="rss", description="'rss' or 'web'")
    tags: list[str] = Field(default_factory=list, description="Tags like ['news', 'cyber', 'tech']")


@tool(
    name="source_add",
    description="Add a new RSS or web source for news and opportunities.",
    params=SourceAddParams,
    tier="write",
    is_meta_tool=True,
)
async def source_add(params: SourceAddParams, ctx: ToolContext) -> ToolResult:
    if ctx.db is None:
        return ToolResult(ok=False, output="Database not available.", summary="no db")

    try:
        await execute(
            ctx.db,
            """
            INSERT INTO sources (name, url, kind, tags, enabled)
            VALUES (?, ?, ?, ?, 1)
            """,
            (params.name, params.url, params.kind, json.dumps(params.tags)),
        )
        await ctx.db.commit()
        return ToolResult(
            ok=True, output=f"Added source '{params.name}' ({params.url})", summary="source added"
        )
    except Exception as exc:
        return ToolResult(
            ok=False, output=f"Failed adding source: {exc}", summary="add source error"
        )


class SourceRemoveParams(BaseModel):
    name_or_url: str = Field(..., description="Source name or URL")


@tool(
    name="source_remove",
    description="Remove a feed source.",
    params=SourceRemoveParams,
    tier="write",
    is_meta_tool=True,
)
async def source_remove(params: SourceRemoveParams, ctx: ToolContext) -> ToolResult:
    if ctx.db is None:
        return ToolResult(ok=False, output="Database not available.", summary="no db")

    await execute(
        ctx.db,
        "DELETE FROM sources WHERE name = ? OR url = ?",
        (params.name_or_url, params.name_or_url),
    )
    await ctx.db.commit()
    return ToolResult(
        ok=True, output=f"Removed source '{params.name_or_url}'", summary="source removed"
    )


# ---------------------------------------------------------------------------
# Preference Meta-Tools
# ---------------------------------------------------------------------------


class PreferenceSetParams(BaseModel):
    key: str = Field(..., description="Preference key (e.g. 'briefing.topics', 'timezone')")
    value: str = Field(..., description="Preference value")


@tool(
    name="preference_set",
    description="Set or update a persistent user preference.",
    params=PreferenceSetParams,
    tier="write",
    is_meta_tool=True,
)
async def preference_set(params: PreferenceSetParams, ctx: ToolContext) -> ToolResult:
    if ctx.db is None:
        return ToolResult(ok=False, output="Database not available.", summary="no db")

    await execute(
        ctx.db,
        """
        INSERT INTO preferences (key, value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now')
        """,
        (params.key, params.value),
    )
    await ctx.db.commit()
    return ToolResult(
        ok=True,
        output=f"Set preference '{params.key}' = '{params.value}'",
        summary=f"set {params.key}",
    )


class PreferenceGetParams(BaseModel):
    key: str = Field(..., description="Preference key")


@tool(
    name="preference_get",
    description="Get a user preference value.",
    params=PreferenceGetParams,
    tier="read",
)
async def preference_get(params: PreferenceGetParams, ctx: ToolContext) -> ToolResult:
    if ctx.db is None:
        return ToolResult(ok=False, output="Database not available.", summary="no db")

    row = await fetch_one(ctx.db, "SELECT value FROM preferences WHERE key = ?", (params.key,))
    if row is None:
        return ToolResult(
            ok=False, output=f"Preference '{params.key}' not found.", summary="not found"
        )
    return ToolResult(ok=True, output=str(row["value"]), summary=f"preference {params.key}")


class PreferenceListParams(BaseModel):
    pass


@tool(
    name="preference_list",
    description="List all user preferences.",
    params=PreferenceListParams,
    tier="read",
)
async def preference_list(params: PreferenceListParams, ctx: ToolContext) -> ToolResult:
    if ctx.db is None:
        return ToolResult(ok=False, output="Database not available.", summary="no db")

    rows = await fetch_all(
        ctx.db, "SELECT key, value, updated_at FROM preferences ORDER BY key ASC"
    )
    prefs = {r["key"]: r["value"] for r in rows}
    return ToolResult(
        ok=True, output=json.dumps(prefs, indent=2), summary=f"Found {len(prefs)} preferences"
    )


# ---------------------------------------------------------------------------
# Skill Meta-Tools
# ---------------------------------------------------------------------------


class SkillListParams(BaseModel):
    pass


@tool(
    name="skill_list",
    description="List available skills with their descriptions.",
    params=SkillListParams,
    tier="read",
)
async def skill_list(params: SkillListParams, ctx: ToolContext) -> ToolResult:
    if ctx.db is None:
        return ToolResult(ok=False, output="Database not available.", summary="no db")

    rows = await fetch_all(
        ctx.db,
        "SELECT name, description, filename, updated_at FROM skills_index ORDER BY name ASC",
    )
    skills = [dict(r) for r in rows]
    return ToolResult(
        ok=True, output=json.dumps(skills, indent=2), summary=f"Found {len(skills)} skills"
    )


class SkillReadParams(BaseModel):
    name: str = Field(..., description="Skill name")


@tool(
    name="skill_read",
    description="Read the markdown body and guidelines of a skill.",
    params=SkillReadParams,
    tier="read",
)
async def skill_read(params: SkillReadParams, ctx: ToolContext) -> ToolResult:
    skills_dir = DEFAULT_SKILLS_DIR
    target = skills_dir / f"{params.name}.md"
    if not target.exists():
        if ctx.db:
            row = await fetch_one(
                ctx.db, "SELECT filename FROM skills_index WHERE name = ?", (params.name,)
            )
            if row:
                target = skills_dir / str(row["filename"])

    if not target.exists():
        return ToolResult(
            ok=False, output=f"Skill '{params.name}' not found on disk.", summary="skill not found"
        )

    content = target.read_text(encoding="utf-8")
    _meta, body = parse_frontmatter(content)
    return ToolResult(ok=True, output=body, summary=f"read skill {params.name}")


class SkillWriteParams(BaseModel):
    name: str = Field(..., description="Skill name (slug format e.g. 'cve-audit')")
    description: str = Field(..., description="Short summary of what this skill guides")
    body: str = Field(..., description="Markdown guidelines and instructions")


@tool(
    name="skill_write",
    description="Create or update a skill markdown file, versioning previous content.",
    params=SkillWriteParams,
    tier="write",
    is_meta_tool=True,
)
async def skill_write(params: SkillWriteParams, ctx: ToolContext) -> ToolResult:
    skills_dir = DEFAULT_SKILLS_DIR
    skills_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{params.name}.md"
    target = skills_dir / filename

    if target.exists() and ctx.db:
        old_content = target.read_text(encoding="utf-8")
        await execute(
            ctx.db,
            """
            INSERT INTO skill_versions (skill_name, body, changed_by, change_note)
            VALUES (?, ?, 'agent', 'Updated skill')
            """,
            (params.name, old_content),
        )

    full_text = (
        f"---\nname: {params.name}\ndescription: {params.description}\n---\n\n{params.body}\n"
    )
    target.write_text(full_text, encoding="utf-8")

    if ctx.db:
        await execute(
            ctx.db,
            """
            INSERT INTO skills_index (name, description, filename, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(name) DO UPDATE SET
                description = excluded.description,
                filename = excluded.filename,
                updated_at = datetime('now')
            """,
            (params.name, params.description, filename),
        )
        await ctx.db.commit()

    return ToolResult(
        ok=True,
        output=f"Saved skill '{params.name}' to {filename}",
        summary=f"saved skill {params.name}",
    )


# ---------------------------------------------------------------------------
# Change History & Undo Meta-Tools
# ---------------------------------------------------------------------------


class ChangeHistoryParams(BaseModel):
    entity: str = Field(..., description="'job' or 'skill'")
    name: str = Field(..., description="Name of the job or skill")


@tool(
    name="change_history",
    description="View historical versions of a job or skill.",
    params=ChangeHistoryParams,
    tier="read",
)
async def change_history(params: ChangeHistoryParams, ctx: ToolContext) -> ToolResult:
    if ctx.db is None:
        return ToolResult(ok=False, output="Database not available.", summary="no db")

    if params.entity == "job":
        row = await fetch_one(
            ctx.db,
            "SELECT id FROM jobs WHERE name = ? OR id = ?",
            (params.name, params.name),
        )
        if row is None:
            return ToolResult(
                ok=False, output=f"Job '{params.name}' not found.", summary="not found"
            )

        v_rows = await fetch_all(
            ctx.db,
            "SELECT version, changed_by, change_note, created_at FROM job_versions "
            "WHERE job_id = ? ORDER BY version DESC",
            (str(row["id"]),),
        )
        history = [dict(v) for v in v_rows]
        return ToolResult(
            ok=True,
            output=json.dumps(history, indent=2),
            summary=f"{len(history)} versions for {params.name}",
        )

    elif params.entity == "skill":
        v_rows = await fetch_all(
            ctx.db,
            "SELECT id, skill_name, changed_by, change_note, created_at FROM skill_versions "
            "WHERE skill_name = ? ORDER BY id DESC",
            (params.name,),
        )
        history = [dict(v) for v in v_rows]
        return ToolResult(
            ok=True,
            output=json.dumps(history, indent=2),
            summary=f"{len(history)} versions for {params.name}",
        )

    return ToolResult(ok=False, output="Entity must be 'job' or 'skill'.", summary="invalid entity")


class UndoLastChangeParams(BaseModel):
    entity: str = Field(default="job", description="'job' or 'skill'")
    name: str = Field(..., description="Name of the job or skill to undo")


@tool(
    name="undo_last_change",
    description="Undo the last change made to a job or skill (restore previous version).",
    params=UndoLastChangeParams,
    tier="write",
    is_meta_tool=True,
)
async def undo_last_change(params: UndoLastChangeParams, ctx: ToolContext) -> ToolResult:
    if ctx.db is None:
        return ToolResult(ok=False, output="Database not available.", summary="no db")

    if params.entity == "job":
        row = await fetch_one(
            ctx.db,
            "SELECT id, version FROM jobs WHERE name = ? OR id = ?",
            (params.name, params.name),
        )
        if row is None:
            return ToolResult(
                ok=False, output=f"Job '{params.name}' not found.", summary="not found"
            )

        cur_v = int(row["version"] or 1)
        prev_row = await fetch_one(
            ctx.db,
            "SELECT snapshot_json, version FROM job_versions "
            "WHERE job_id = ? AND version < ? ORDER BY version DESC LIMIT 1",
            (str(row["id"]), cur_v),
        )
        if prev_row is None:
            return ToolResult(
                ok=False,
                output=f"No previous version found for job '{params.name}'.",
                summary="no prior version",
            )

        snap = json.loads(prev_row["snapshot_json"])
        restored_v = int(prev_row["version"])

        await execute(
            ctx.db,
            """
            UPDATE jobs SET
                schedule_type = ?, schedule_expr = ?, timezone = ?,
                instruction = ?, config_json = ?, enabled = ?,
                version = ?, updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                snap["schedule_type"],
                snap["schedule_expr"],
                snap["timezone"],
                snap["instruction"],
                snap["config_json"],
                snap["enabled"],
                restored_v,
                str(row["id"]),
            ),
        )
        await ctx.db.commit()
        return ToolResult(
            ok=True,
            output=f"Undid change: restored job '{params.name}' to version {restored_v}.",
            summary=f"restored {params.name} to v{restored_v}",
        )

    elif params.entity == "skill":
        prev_row = await fetch_one(
            ctx.db,
            "SELECT id, body FROM skill_versions WHERE skill_name = ? ORDER BY id DESC LIMIT 1",
            (params.name,),
        )
        if prev_row is None:
            return ToolResult(
                ok=False,
                output=f"No previous version found for skill '{params.name}'.",
                summary="no prior version",
            )

        skills_dir = DEFAULT_SKILLS_DIR
        target = skills_dir / f"{params.name}.md"
        target.write_text(str(prev_row["body"]), encoding="utf-8")

        await execute(ctx.db, "DELETE FROM skill_versions WHERE id = ?", (prev_row["id"],))
        await ctx.db.commit()

        return ToolResult(
            ok=True,
            output=f"Undid change: restored skill '{params.name}' from version history.",
            summary=f"restored {params.name}",
        )

    return ToolResult(ok=False, output="Entity must be 'job' or 'skill'.", summary="invalid entity")
