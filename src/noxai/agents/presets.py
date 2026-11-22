"""Presets configuration and profile activation for NoX agents."""

from __future__ import annotations

from typing import Any

from noxai.db import get_db

PRESETS: dict[str, dict[str, Any]] = {
    "morning-digest": {
        "name": "morning-digest",
        "description": "Daily morning intelligence briefings from email, calendar, and GitHub.",
        "active_agents": ["morning_digest"],
        "active_connectors": ["gmail", "calendar"],
        "schedules": [{"slot": "morning", "time": "07:00"}],
    },
    "deep-research": {
        "name": "deep-research",
        "description": "Autonomous multi-source research combining arXiv, web, and syndication.",
        "active_agents": ["deep_research", "notes_assistant"],
        "active_connectors": ["feeds"],
        "schedules": [],
    },
    "code-assistant": {
        "name": "code-assistant",
        "description": "Developer pair-programmer with workflow linter and PR review tools.",
        "active_agents": ["code_assistant", "github_ci_watch"],
        "active_connectors": ["github"],
        "schedules": [],
    },
    "scheduled-monitor": {
        "name": "scheduled-monitor",
        "description": "Host watchdog for system load, audit verification, and database health.",
        "active_agents": ["scheduled_monitor"],
        "active_connectors": [],
        "schedules": [{"slot": "hourly", "cron": "0 * * * *"}],
    },
    "chat-simple": {
        "name": "chat-simple",
        "description": "Minimal conversational assistant without background scheduled tasks.",
        "active_agents": ["simple_chat"],
        "active_connectors": [],
        "schedules": [],
    },
    "inbox-triage": {
        "name": "inbox-triage",
        "description": "High-volume email and message triage with automated phishing checks.",
        "active_agents": ["inbox_triage"],
        "active_connectors": ["gmail", "imap"],
        "schedules": [{"slot": "half-hourly", "cron": "*/30 * * * *"}],
    },
    "github-ops": {
        "name": "github-ops",
        "description": "Continuous GitHub Actions watchdog, failure triage, and PR rollup.",
        "active_agents": ["github_ci_watch", "code_assistant"],
        "active_connectors": ["github"],
        "schedules": [],
    },
}


def list_presets() -> list[dict[str, Any]]:
    """Return all available system presets."""
    return list(PRESETS.values())


def get_preset(name: str) -> dict[str, Any] | None:
    """Get a preset configuration by name."""
    return PRESETS.get(name)


async def apply_preset(name: str) -> dict[str, Any]:
    """Apply a preset by enabling targeted agents and disabling unselected agents."""
    preset = get_preset(name)
    if not preset:
        raise ValueError(f"Preset '{name}' not found. Available: {list(PRESETS.keys())}")

    active_agents = set(preset["active_agents"])

    async with get_db() as db:
        # Enable target agents and disable others
        await db.execute("UPDATE agents SET enabled = 0")
        for agent_name in active_agents:
            await db.execute(
                "UPDATE agents SET enabled = 1 WHERE name = ?", (agent_name,)
            )

        # Store active preset in security_state
        await db.execute(
            """
            INSERT INTO security_state (key, value, updated_at)
            VALUES ('active_preset', ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (name,),
        )
        await db.commit()

    return preset


async def get_current_preset() -> str | None:
    """Get currently active preset name."""
    async with get_db() as db:
        async with db.execute(
            "SELECT value FROM security_state WHERE key = 'active_preset'"
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None
