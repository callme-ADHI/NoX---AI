"""Complexity gate for determining whether to run simple AgentLoop or deep Planner DAG.

Per Section 6.4:
1. Fast heuristics: explicit trigger keywords, word count > ~60, or multi-ask structure.
2. Direct override: `/deep` command prefix.
3. Fast local-model classification fallback if heuristics are inconclusive.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from noxai.agent.json_repair import repair_json
from noxai.models.base import Message

if TYPE_CHECKING:
    from noxai.models.router import ModelRouter

log = logging.getLogger(__name__)

# Trigger keywords and phrases that signal complex multi-agent research
COMPLEX_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(?:detailed|in[- ]depth|research|compare|report|full breakdown|deep dive)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\beverything about\b", re.IGNORECASE),
    re.compile(r"\bcomprehensive (?:analysis|breakdown|overview|summary)\b", re.IGNORECASE),
]

# Simple greetings or direct commands that should immediately stay simple
SIMPLE_SHORT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"^(?:hi|hello|hey|ping|echo\b.*|what time is it\??|date|uptime|whoami|pwd)$",
        re.IGNORECASE,
    ),
]


@dataclass
class ComplexityResult:
    """Outcome of complexity evaluation."""

    is_complex: bool
    cleaned_message: str
    reason: str
    forced: bool = False


async def evaluate_complexity(
    user_message: str,
    router: ModelRouter | None = None,
) -> ComplexityResult:
    """Determine whether a request should be handled by the simple loop or deep planner."""
    raw = user_message.strip()

    # 1. Force override: /deep prefix or flag
    if raw.startswith("/deep"):
        cleaned = raw[len("/deep") :].strip()
        # If user typed just "/deep", preserve something to work with
        if not cleaned:
            cleaned = raw
        return ComplexityResult(
            is_complex=True,
            cleaned_message=cleaned,
            reason="Forced via /deep command",
            forced=True,
        )

    # 2. Fast simple heuristic
    for pat in SIMPLE_SHORT_PATTERNS:
        if pat.match(raw):
            return ComplexityResult(
                is_complex=False,
                cleaned_message=raw,
                reason="Direct short command/greeting",
            )

    # 3. Fast complex heuristics
    # (a) Trigger keywords
    for pat in COMPLEX_PATTERNS:
        match = pat.search(raw)
        if match:
            return ComplexityResult(
                is_complex=True,
                cleaned_message=raw,
                reason=f"Heuristic keyword matched: '{match.group(0)}'",
            )

    # (b) Word count > 60
    words = raw.split()
    if len(words) > 60:
        return ComplexityResult(
            is_complex=True,
            cleaned_message=raw,
            reason=f"Word count threshold exceeded ({len(words)} > 60 words)",
        )

    # (c) Multiple distinct questions / asks (>= 2 questions or structured asks)
    if raw.count("?") >= 2:
        return ComplexityResult(
            is_complex=True,
            cleaned_message=raw,
            reason="Multiple distinct questions detected",
        )

    if re.search(r"(?:^|\n)\s*(?:1[.)]|-|\*)\s+.*\n\s*(?:2[.)]|-|\*)\s+", raw):
        return ComplexityResult(
            is_complex=True,
            cleaned_message=raw,
            reason="Multi-point structured query detected",
        )

    # 4. If short and direct, default to simple
    if len(words) < 15:
        return ComplexityResult(
            is_complex=False,
            cleaned_message=raw,
            reason="Short, direct request",
        )

    # 5. Fast local model fallback check if router is provided
    if router is not None:
        try:
            gate_prompt = (
                "Decide if the user request is simple (answered in 1 turn or single tool)\n"
                "or complex (requires multi-topic research, multi-source comparison,\n"
                "or structured subtask decomposition).\n\n"
                f"User request: {raw}\n\n"
                'Return ONLY a JSON object: {"complex": true|false, "reason": "short explanation"}'
            )
            messages = [
                Message(
                    role="system",
                    content="You are a fast routing classifier. Always return valid JSON only.",
                ),
                Message(role="user", content=gate_prompt),
            ]
            raw_llm = ""
            async for chunk in router.chat(role="brain", messages=messages, stream=False):
                if chunk.text:
                    raw_llm += chunk.text

            clean_json = raw_llm.strip()
            if "```json" in clean_json:
                clean_json = clean_json.split("```json", 1)[1].split("```", 1)[0].strip()
            elif "```" in clean_json:
                clean_json = clean_json.split("```", 1)[1].split("```", 1)[0].strip()

            parsed = (
                json.loads(clean_json)
                if clean_json.startswith("{")
                else repair_json(clean_json)
            )
            if isinstance(parsed, dict) and "complex" in parsed:
                return ComplexityResult(
                    is_complex=bool(parsed["complex"]),
                    cleaned_message=raw,
                    reason=f"Model classified: {parsed.get('reason', 'no reason provided')}",
                )
        except Exception as exc:
            log.warning("Fast LLM gate classification failed, falling back to simple: %s", exc)

    # Default to simple
    return ComplexityResult(
        is_complex=False,
        cleaned_message=raw,
        reason="Defaulted to simple execution",
    )
