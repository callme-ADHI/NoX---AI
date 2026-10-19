"""Prompt assembly and context window management for NoX agent runs."""

from __future__ import annotations

import datetime
import logging
import zoneinfo
from pathlib import Path

import aiosqlite

from noxai.config import Settings
from noxai.models.base import Message, ToolSpec
from noxai.models.router import ModelRouter

log = logging.getLogger(__name__)

# Default prompt path
SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "system.md"


def approximate_tokens(text: str) -> int:
    """Approximate token count using character length / 3.5 with 25% safety margin."""
    if not text:
        return 0
    # (len(text) / 3.5) * 1.25 = len(text) / 2.8
    return max(1, int(len(text) / 2.8))


def approximate_messages_tokens(messages: list[Message]) -> int:
    """Estimate total tokens across a list of messages including framing overhead."""
    total = 0
    for msg in messages:
        total += approximate_tokens(msg.get_model_text()) + 4  # 4 tokens message framing
        for tc in msg.tool_calls:
            total += approximate_tokens(str(tc.arguments)) + approximate_tokens(tc.name) + 4
    return total


async def assemble_system_prompt(
    config: Settings,
    tools: list[ToolSpec],
    db: aiosqlite.Connection | None = None,
    *,
    allow_cloud: bool = False,
    recalled_memories: list[str] | None = None,
    relevant_skills: list[dict[str, str]] | None = None,
    stable_prefix: bool = False,
) -> str:
    """Assemble the system prompt.

    When stable_prefix is False (legacy Section 6.3.1 order):
    1. Core identity and rules (prompts/system.md).
    2. Current date, time, weekday, and timezone.
    3. Owner preferences (from preferences table).
    4. Relevant skills (at most 3, trimmed).
    5. Recalled memories (omitted entirely if allow_cloud=True).
    6. Tool list with short descriptions.

    When stable_prefix is True (Phase F1 KV cache optimization):
    1. Core identity and rules (stable).
    2. Tool list with short descriptions (stable).
    3. Owner preferences (stable across turns).
    4. Relevant skills (stable).
    5. Current date, time, weekday, and timezone (volatile).
    6. Recalled memories (volatile).
    """
    # 1. Core identity & rules
    if SYSTEM_PROMPT_PATH.exists():
        system_rules = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
    else:
        system_rules = (
            "You are NoX, the owner's personal AI agent running on his Arch Linux computer."
        )

    # 2. Date, time, weekday, timezone
    tz_name = config.timezone.name
    tz: datetime.tzinfo
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = datetime.UTC
        tz_name = "UTC"

    now = datetime.datetime.now(tz)
    date_str = now.strftime("%Y-%m-%d")
    weekday_str = now.strftime("%A")
    time_str = now.strftime("%H:%M:%S")
    date_time_sec = f"Current date & time: {weekday_str}, {date_str} {time_str} {tz_name}"

    # 3. Owner preferences
    preferences: dict[str, str] = {}
    if db is not None:
        try:
            async with db.execute("SELECT key, value FROM preferences ORDER BY key") as cursor:
                async for row in cursor:
                    preferences[str(row[0])] = str(row[1])
        except Exception as exc:
            log.warning("Could not load preferences from database: %s", exc)

    pref_sec = ""
    if preferences:
        pref_lines = ["Owner preferences:"]
        for k, v in preferences.items():
            pref_lines.append(f"- {k}: {v}")
        pref_sec = "\n".join(pref_lines)

    # 4. Relevant skills (at most 3; omitted for cloud-eligible runs)
    skills = [] if allow_cloud else (relevant_skills or [])
    if not skills and not allow_cloud and db is not None:
        try:
            async with db.execute("SELECT name, description FROM skills_index LIMIT 3") as cursor:
                async for row in cursor:
                    skills.append({"name": str(row[0]), "description": str(row[1])})
        except Exception as exc:
            log.warning("Could not load skills from database: %s", exc)

    skills_sec = ""
    if skills:
        skill_lines = ["Active skills:"]
        for s in skills[:3]:
            skill_lines.append(f"- {s.get('name')}: {s.get('description', '')}")
        skills_sec = "\n".join(skill_lines)

    # 5. Recalled memories (Omitted entirely for cloud-eligible runs!)
    memories_sec = ""
    if not allow_cloud:
        memories = recalled_memories or []
        if memories:
            mem_lines = ["Recalled memories:"]
            for m in memories:
                mem_lines.append(f"- {m}")
            memories_sec = "\n".join(mem_lines)

    # 6. Tool list with short descriptions
    tools_sec = ""
    if tools:
        tool_lines = ["Available tools:"]
        for t in tools:
            tool_lines.append(f"- {t.name}: {t.description}")
        tools_sec = "\n".join(tool_lines)

    if stable_prefix:
        ordered = [
            system_rules,
            tools_sec,
            pref_sec,
            skills_sec,
            date_time_sec,
            memories_sec,
        ]
    else:
        ordered = [
            system_rules,
            date_time_sec,
            pref_sec,
            skills_sec,
            memories_sec,
            tools_sec,
        ]

    return "\n\n".join([sec for sec in ordered if sec])


async def trim_context(
    messages: list[Message],
    max_tokens: int = 8192,
    router: ModelRouter | None = None,
) -> list[Message]:
    """Trim conversation context so total estimated tokens stay <= 70% of max_tokens.

    Preserves the system message (first message) and the latest N messages.
    If older history pushes token usage above 70%, older turns are summarized.
    """
    if not messages:
        return []

    target_tokens = int(max_tokens * 0.70)
    current_tokens = approximate_messages_tokens(messages)

    if current_tokens <= target_tokens:
        return list(messages)

    system_msg = messages[0] if messages[0].role == "system" else None
    non_system = messages[1:] if system_msg else messages[:]

    # Always preserve the most recent 4 messages if available
    min_recent = min(4, len(non_system))
    recent_messages = non_system[-min_recent:]
    older_messages = non_system[:-min_recent]

    if not older_messages:
        # Cannot trim further without losing the most recent turn
        res = [system_msg, *recent_messages] if system_msg else recent_messages
        return res

    # Generate summary of older messages
    older_texts: list[str] = []
    for m in older_messages:
        older_texts.append(f"{m.role.upper()}: {m.get_text()[:200]}")
    combined_older = "\n".join(older_texts)

    summary_text: str
    if router and len(combined_older) > 300:
        try:
            # Use local brain to quickly summarize older context
            prompt = [
                Message(
                    role="system",
                    content=(
                        "Summarize the key facts and context of earlier dialogue "
                        "in 2-3 concise sentences."
                    ),
                ),
                Message(role="user", content=combined_older),
            ]
            response_chunks = []
            async for chunk in router.chat(role="brain", messages=prompt):
                if chunk.text:
                    response_chunks.append(chunk.text)
            summary_text = "".join(response_chunks).strip()
        except Exception as exc:
            log.warning("Model context summarization failed, falling back to heuristic: %s", exc)
            summary_text = combined_older[:400] + "..."
    else:
        summary_text = combined_older[:400] + "..." if len(combined_older) > 400 else combined_older

    summary_message = Message(
        role="system",
        content=f"Summary of earlier conversation:\n{summary_text}",
    )

    result: list[Message] = []
    if system_msg:
        result.append(system_msg)
    result.append(summary_message)
    result.extend(recent_messages)

    return result
