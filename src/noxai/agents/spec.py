"""Agent specifications, schemas, and built-in agent definitions for NoX."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from noxai.db import get_db


@dataclass
class AgentSpec:
    """Specification defining an autonomous NoX agent."""

    id: str
    name: str
    description: str = ""
    mode: str = "on_demand"  # "on_demand", "scheduled", "continuous"
    prompt_template: str = ""
    cron_expression: str | None = None
    tools: list[str] = field(default_factory=list)
    budget_tokens: int = 50000
    budget_cost_usd: float = 1.0
    enabled: bool = True
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Built-in Agent Definitions (8 Core Personal AI Roles)
BUILTIN_AGENTS: dict[str, AgentSpec] = {
    "deep_research": AgentSpec(
        id="agent_deep_research",
        name="deep_research",
        description=(
            "Autonomous multi-step investigation combining web, papers, and local notes "
            "with verified citations."
        ),
        mode="on_demand",
        prompt_template=(
            "You are NoX Deep Research Agent. Investigate the user's inquiry thoroughly across "
            "sources. Formulate research hypotheses, search relevant literature and syndicated "
            "feeds, cross-reference local notes, and synthesize a structured evidence report with "
            "Markdown citations."
        ),
        tools=["web_search", "notes_search", "feeds_search", "paper_search"],
        budget_tokens=80000,
        budget_cost_usd=1.5,
    ),
    "morning_digest": AgentSpec(
        id="agent_morning_digest",
        name="morning_digest",
        description=(
            "Compiles morning intelligence: unread emails, upcoming meetings, CI statuses, "
            "and security feeds."
        ),
        mode="scheduled",
        cron_expression="0 7 * * *",
        prompt_template=(
            "You are NoX Morning Briefing Agent. Synthesize an actionable daily intelligence "
            "brief. Triage overnight emails, upcoming calendar events, watched GitHub Actions "
            "runs, and flag priority action items."
        ),
        tools=["mail_triage", "calendar_upcoming", "gh_runs", "feeds_digest"],
        budget_tokens=40000,
        budget_cost_usd=0.5,
    ),
    "inbox_triage": AgentSpec(
        id="agent_inbox_triage",
        name="inbox_triage",
        description=(
            "Monitors email and chat channels, detects urgency/phishing, extracts deadlines, "
            "and drafts replies."
        ),
        mode="scheduled",
        cron_expression="*/30 * * * *",
        prompt_template=(
            "You are NoX Inbox Triage Agent. Classify unread messages into categories, inspect "
            "for phishing or brand spoofing threats, extract action deadlines, and prepare "
            "drafted replies."
        ),
        tools=["mail_list", "mail_classify", "phishing_detect", "mail_draft"],
        budget_tokens=30000,
        budget_cost_usd=0.4,
    ),
    "github_ci_watch": AgentSpec(
        id="agent_github_ci_watch",
        name="github_ci_watch",
        description=(
            "Watches GitHub Actions pipelines, triages failure logs, extracts terminal error "
            "frames, and recommends fixes."
        ),
        mode="continuous",
        prompt_template=(
            "You are NoX GitHub CI Watchdog. Monitor watched repositories for workflow runs. "
            "When a run fails, extract the failed step logs, determine root cause, and produce "
            "an actionable triage report."
        ),
        tools=["gh_runs", "gh_triage", "gh_watch"],
        budget_tokens=60000,
        budget_cost_usd=1.0,
    ),
    "scheduled_monitor": AgentSpec(
        id="agent_scheduled_monitor",
        name="scheduled_monitor",
        description="Periodic system health, database integrity, and security state watchdog.",
        mode="scheduled",
        cron_expression="0 * * * *",
        prompt_template=(
            "You are NoX System Doctor Agent. Inspect daemon health, database WAL checkpoint "
            "status, active connectors, and tamper-evident audit log integrity. Report any "
            "degraded state."
        ),
        tools=["health_check", "audit_verify", "db_status"],
        budget_tokens=20000,
        budget_cost_usd=0.2,
    ),
    "code_assistant": AgentSpec(
        id="agent_code_assistant",
        name="code_assistant",
        description="Assists with codebase refactoring, workflow linting, and PR reviews.",
        mode="on_demand",
        prompt_template=(
            "You are NoX Code Assistant. Assist the developer with codebase analysis, static "
            "linting, workflow security review, and bug investigation following local-first "
            "conventions."
        ),
        tools=["code_search", "workflow_lint", "pr_summary"],
        budget_tokens=50000,
        budget_cost_usd=1.0,
    ),
    "notes_assistant": AgentSpec(
        id="agent_notes_assistant",
        name="notes_assistant",
        description=(
            "Personal knowledge vault curator, semantic chunk linker, and concept synthesizer."
        ),
        mode="on_demand",
        prompt_template=(
            "You are NoX Notes Assistant. Help curate the personal Markdown vault. Extract "
            "key concepts, suggest cross-links, and answer queries grounded strictly in local "
            "knowledge."
        ),
        tools=["vault_search", "vault_link", "vault_write"],
        budget_tokens=30000,
        budget_cost_usd=0.3,
    ),
    "simple_chat": AgentSpec(
        id="agent_simple_chat",
        name="simple_chat",
        description="Direct conversational assistant maintaining privacy and local boundaries.",
        mode="on_demand",
        prompt_template=(
            "You are NoX, personal AI assistant for Arch Linux and Hyprland. Provide concise, "
            "direct, and technically accurate responses while protecting sensitive user "
            "information."
        ),
        tools=["system_status", "remind", "calc"],
        budget_tokens=20000,
        budget_cost_usd=0.2,
    ),
}


def get_builtin_agents() -> dict[str, AgentSpec]:
    """Return dictionary of all 8 built-in agent specifications."""
    return BUILTIN_AGENTS.copy()


async def sync_builtin_agents_to_db() -> None:
    """Ensure all built-in agents are seeded in the database."""
    now = datetime.now(UTC).isoformat()
    async with get_db() as db:
        for spec in BUILTIN_AGENTS.values():
            await db.execute(
                """
                INSERT INTO agents (
                    id, name, mode, cron_expression, enabled, config_json,
                    prompt_template, budget_tokens, budget_cost_usd, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'idle', ?)
                ON CONFLICT(name) DO UPDATE SET
                    mode = excluded.mode,
                    cron_expression = excluded.cron_expression,
                    budget_tokens = excluded.budget_tokens,
                    budget_cost_usd = excluded.budget_cost_usd,
                    updated_at = excluded.updated_at
                """,
                (
                    spec.id,
                    spec.name,
                    spec.mode,
                    spec.cron_expression,
                    1 if spec.enabled else 0,
                    json.dumps(spec.config),
                    spec.prompt_template,
                    spec.budget_tokens,
                    spec.budget_cost_usd,
                    now,
                ),
            )
        await db.commit()
