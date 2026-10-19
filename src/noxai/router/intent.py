"""Zero-LLM intent analyzer and Reflex Router (<50ms execution) (§1.4, §G3).

Classifies each user turn into one of 10 primary intents:
- reply_now: Direct questions, conversational greetings, explanations.
- reminder: Setting reminders, timers, alarms.
- schedule: Recurring schedules, daily/weekly briefings, cron patterns.
- job_edit: Modifying, pausing, resuming, or deleting scheduled jobs.
- background_task: Long-running computations, batch operations.
- deep_research: In-depth multi-source web research and synthesis.
- memory_op: Saving, updating, forgetting, or querying user facts.
- system_action: Launching apps, checking system metrics, shell actions.
- sensitive: Auth, passwords, secrets, financial, medical data.
- ambiguous: Unclear, underspecified, or empty prompts.

Assigns a reply mode:
- instant: Stream foreground answer immediately.
- background: Dispatch to Task Center.
- both: Stream immediate acknowledgment AND dispatch to background.
"""

from __future__ import annotations

import enum
import re
import time
from dataclasses import dataclass, field
from typing import Any

from noxai.security.sensitivity import detect_sensitivity


class IntentType(enum.StrEnum):
    """Primary intent categories for user requests."""

    REPLY_NOW = "reply_now"
    REMINDER = "reminder"
    SCHEDULE = "schedule"
    JOB_EDIT = "job_edit"
    BACKGROUND_TASK = "background_task"
    DEEP_RESEARCH = "deep_research"
    MEMORY_OP = "memory_op"
    SYSTEM_ACTION = "system_action"
    SENSITIVE = "sensitive"
    AMBIGUOUS = "ambiguous"


class ReplyMode(enum.StrEnum):
    """Dispatch strategy for responding to the intent."""

    INSTANT = "instant"        # Answer immediately in foreground stream
    BACKGROUND = "background"  # Queue to Task Center silently or purely async
    BOTH = "both"              # Stream immediate confirmation AND queue background task


@dataclass
class IntentResult:
    """Outcome of intent analysis."""

    intent: IntentType
    reply_mode: ReplyMode
    confidence: float
    reason: str
    suggested_lane: str  # "cloud" | "local"
    entities: dict[str, Any] = field(default_factory=dict)
    analysis_time_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "reply_mode": self.reply_mode.value,
            "confidence": round(self.confidence, 2),
            "reason": self.reason,
            "suggested_lane": self.suggested_lane,
            "entities": self.entities,
            "analysis_time_ms": round(self.analysis_time_ms, 2),
        }


# ---------------------------------------------------------------------------
# Pre-compiled Regex Patterns for <50ms Zero-LLM Intent Analysis
# ---------------------------------------------------------------------------

_REMINDER_PATTERNS = [
    re.compile(
        r"\b(?:remind\s+me(?:\s+to|\s+in|\s+at|\s+about)?|set\s+(?:a\s+)?reminder|"
        r"create\s+(?:a\s+)?reminder|nudge\s+me|set\s+(?:a\s+)?timer|set\s+(?:an?\s+)?alarm)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:in|after)\s+\d+\s*(?:s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours)\b",
        re.IGNORECASE,
    ),
]

_SCHEDULE_PATTERNS = [
    re.compile(
        r"\b(?:schedule\s+(?:a\s+)?(?:daily|weekly|hourly|monthly|recurring|task|job|run)|"
        r"every\s+(?:day|morning|evening|night|afternoon|week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"\d+\s*(?:hours?|minutes?|days?|secs?))|daily\s+briefing\s+at|morning\s+briefing\s+at)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bcron\s+['\"][^'\"]+['\"]\b", re.IGNORECASE),
]

_JOB_EDIT_PATTERNS = [
    re.compile(
        r"\b(?:cancel|delete|remove|pause|disable|resume|enable|edit|update)\s+"
        r"(?:the\s+|my\s+)?(?:[\w\-]+\s+)*(?:job|scheduled\s+task|reminder|briefing|schedule)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:change|move|reschedule)\s+(?:my\s+)?(?:morning\s+briefing|reminder|job|schedule)\s+(?:to|at)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:list|show|view|status\s+of)\s+(?:all\s+)?(?:my\s+)?(?:(?:scheduled\s+)?(?:jobs|schedules|tasks)|reminders)\b",
        re.IGNORECASE,
    ),
]

_MEMORY_OP_PATTERNS = [
    re.compile(
        r"\b(?:remember\s+that|save\s+(?:this\s+)?fact|don't\s+forget\s+that|note\s+that|"
        r"pin\s+(?:this\s+)?fact|forget\s+that|forget\s+my|delete\s+fact|"
        r"my\s+(?:name|email|cat|dog|pet|wife|husband|car|address|favorite|preference)\s+is)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:what\s+do\s+you\s+remember\s+about|recall\s+facts?|"
        r"what\s+is\s+my\s+(?:name|email|cat|dog|preference))\b",
        re.IGNORECASE,
    ),
]

_SYSTEM_ACTION_PATTERNS = [
    re.compile(
        r"\b(?:open|launch|start)\s+(?:firefox|terminal|alacritty|kitty|code|obsidian|spotify|"
        r"discord|slack|calculator|browser|chromium|google-chrome)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:system\s+info|disk\s+space|battery\s+(?:status|level)|cpu\s+usage|ram\s+usage|"
        r"volume\s+(?:up|down|mute)|mute\s+audio|reboot|shutdown|restart\s+system)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:run\s+(?:command|script|shell|bash)|execute\s+(?:command|script|shell)|install\s+package)\b",
        re.IGNORECASE,
    ),
]

_DEEP_RESEARCH_PATTERNS = [
    re.compile(
        r"\b(?:deep\s+research|in-depth\s+research|comprehensive\s+(?:report|analysis|survey|comparison)|"
        r"research\s+(?:and\s+compare|all\s+differences|the\s+history|the\s+state\s+of|recent\s+advances)|"
        r"literature\s+review)\b",
        re.IGNORECASE,
    ),
]

_BACKGROUND_TASK_PATTERNS = [
    re.compile(
        r"\b(?:in\s+(?:the\s+)?background|background\s+task|asynchronously|"
        r"run\s+(?:this\s+)?in\s+background|batch\s+process|download\s+and\s+build|scrape\s+and\s+save)\b",
        re.IGNORECASE,
    ),
]

_TIME_ENTITY_PATTERN = re.compile(
    r"\b(?:in|after|for)\s+(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)\b",
    re.IGNORECASE,
)


def _extract_time_seconds(text: str) -> int | None:
    """Extract delay duration in seconds if present."""
    match = _TIME_ENTITY_PATTERN.search(text)
    if not match:
        return None
    val = int(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith("s"):
        return val
    if unit.startswith("m"):
        return val * 60
    if unit.startswith("h"):
        return val * 3600
    if unit.startswith("d"):
        return val * 86400
    return None


def analyze_intent(text: str) -> IntentResult:
    """Perform deterministic, zero-LLM intent analysis in <50ms.

    Args:
        text: Raw user message text.

    Returns:
        IntentResult containing classified intent, reply_mode, lane, and entities.
    """
    t0 = time.perf_counter()
    clean = text.strip()

    # 1. Ambiguous: empty or extremely short without substance
    if not clean or len(clean) == 1:
        dt = (time.perf_counter() - t0) * 1000.0
        return IntentResult(
            intent=IntentType.AMBIGUOUS,
            reply_mode=ReplyMode.INSTANT,
            confidence=0.9,
            reason="Empty or single-character input",
            suggested_lane="cloud",
            analysis_time_ms=dt,
        )

    # 2. Sensitive data check (secrets, credentials, personal medical/financial)
    sens_result = detect_sensitivity(clean)
    if sens_result.is_sensitive:
        dt = (time.perf_counter() - t0) * 1000.0
        reason_str = (
            f"Detected sensitive patterns: "
            f"{', '.join(sens_result.secrets_found or sens_result.sensitive_categories)}"
        )
        return IntentResult(
            intent=IntentType.SENSITIVE,
            reply_mode=ReplyMode.INSTANT,
            confidence=1.0,
            reason=reason_str,
            suggested_lane="local",
            entities={"secrets_count": len(sens_result.secrets_found)},
            analysis_time_ms=dt,
        )

    # 3. Reminder pattern
    if any(p.search(clean) for p in _REMINDER_PATTERNS):
        delay_sec = _extract_time_seconds(clean)
        dt = (time.perf_counter() - t0) * 1000.0
        return IntentResult(
            intent=IntentType.REMINDER,
            reply_mode=ReplyMode.BOTH,
            confidence=0.95,
            reason="Matches reminder request patterns",
            suggested_lane="local",
            entities={"delay_seconds": delay_sec} if delay_sec else {},
            analysis_time_ms=dt,
        )

    # 4. Job edit / cancel / pause pattern
    if any(p.search(clean) for p in _JOB_EDIT_PATTERNS):
        dt = (time.perf_counter() - t0) * 1000.0
        # If user is only asking to list/show jobs, it's an instant reply
        is_list = bool(re.search(r"\b(?:list|show|view)\b", clean, re.IGNORECASE))
        return IntentResult(
            intent=IntentType.JOB_EDIT,
            reply_mode=ReplyMode.INSTANT if is_list else ReplyMode.BOTH,
            confidence=0.95,
            reason="Matches job management / edit pattern",
            suggested_lane="local",
            analysis_time_ms=dt,
        )

    # 5. Schedule pattern
    if any(p.search(clean) for p in _SCHEDULE_PATTERNS):
        dt = (time.perf_counter() - t0) * 1000.0
        return IntentResult(
            intent=IntentType.SCHEDULE,
            reply_mode=ReplyMode.BOTH,
            confidence=0.95,
            reason="Matches recurring schedule pattern",
            suggested_lane="local",
            analysis_time_ms=dt,
        )

    # 6. Memory operations (facts and preferences)
    if any(p.search(clean) for p in _MEMORY_OP_PATTERNS):
        dt = (time.perf_counter() - t0) * 1000.0
        return IntentResult(
            intent=IntentType.MEMORY_OP,
            reply_mode=ReplyMode.INSTANT,
            confidence=0.9,
            reason="Matches personal fact/memory pattern",
            suggested_lane="local",
            analysis_time_ms=dt,
        )

    # 7. System action (desktop apps, battery, volume, shell)
    if any(p.search(clean) for p in _SYSTEM_ACTION_PATTERNS):
        dt = (time.perf_counter() - t0) * 1000.0
        is_read_only = bool(
            re.search(r"\b(?:system\s+info|disk\s+space|battery|cpu|ram)\b", clean, re.IGNORECASE)
        )
        return IntentResult(
            intent=IntentType.SYSTEM_ACTION,
            reply_mode=ReplyMode.INSTANT if is_read_only else ReplyMode.BOTH,
            confidence=0.9,
            reason="Matches system control or launch pattern",
            suggested_lane="local",
            analysis_time_ms=dt,
        )

    # 8. Deep research
    if any(p.search(clean) for p in _DEEP_RESEARCH_PATTERNS):
        dt = (time.perf_counter() - t0) * 1000.0
        return IntentResult(
            intent=IntentType.DEEP_RESEARCH,
            reply_mode=ReplyMode.BOTH,
            confidence=0.9,
            reason="Matches deep research / literature review pattern",
            suggested_lane="cloud",
            analysis_time_ms=dt,
        )

    # 9. Background task
    if any(p.search(clean) for p in _BACKGROUND_TASK_PATTERNS):
        dt = (time.perf_counter() - t0) * 1000.0
        return IntentResult(
            intent=IntentType.BACKGROUND_TASK,
            reply_mode=ReplyMode.BOTH,
            confidence=0.9,
            reason="Explicit request for background processing",
            suggested_lane="local",
            analysis_time_ms=dt,
        )

    # 10. Check for ambiguous single-word tokens
    if len(clean.split()) == 1 and clean.lower() in ("what", "why", "how", "huh", "help", "test"):
        dt = (time.perf_counter() - t0) * 1000.0
        return IntentResult(
            intent=IntentType.AMBIGUOUS,
            reply_mode=ReplyMode.INSTANT,
            confidence=0.8,
            reason="Single ambiguous token without context",
            suggested_lane="cloud",
            analysis_time_ms=dt,
        )

    # 11. Default to REPLY_NOW for standard conversational turns
    dt = (time.perf_counter() - t0) * 1000.0
    return IntentResult(
        intent=IntentType.REPLY_NOW,
        reply_mode=ReplyMode.INSTANT,
        confidence=0.85,
        reason="Direct conversational question or greeting",
        suggested_lane="cloud",
        analysis_time_ms=dt,
    )
