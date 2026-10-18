"""Robust JSON heuristic repair utilities."""

from __future__ import annotations

import ast
import json
import logging
import re
from typing import Any

log = logging.getLogger(__name__)


def repair_json(raw: str) -> dict[str, Any]:
    """Attempt to parse and repair malformed JSON arguments from model output.

    Handles:
    - Markdown code fences (```json ... ```)
    - Trailing commas in objects/arrays
    - Unbalanced brackets and braces
    - Single-quoted dictionary strings
    """
    text = raw.strip()
    if not text:
        return {}

    # 1. Strip markdown code fences
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # 2. Fast path: standard json.loads
    try:
        val = json.loads(text)
        if isinstance(val, dict):
            return val
        if isinstance(val, list):
            return {"items": val}
        return {"value": val}
    except json.JSONDecodeError:
        pass

    # 3. Clean trailing commas before } or ]
    cleaned = re.sub(r",\s*([}\]])", r"\1", text)

    # 4. Balance unclosed brackets and braces
    open_braces = cleaned.count("{") - cleaned.count("}")
    open_brackets = cleaned.count("[") - cleaned.count("]")
    if open_brackets > 0:
        cleaned = cleaned + ("]" * open_brackets)
    if open_braces > 0:
        cleaned = cleaned + ("}" * open_braces)

    try:
        val = json.loads(cleaned)
        if isinstance(val, dict):
            return val
        return {"value": val}
    except json.JSONDecodeError:
        pass

    try:
        val = ast.literal_eval(text)
        if isinstance(val, dict):
            return {str(k): v for k, v in val.items()}
    except (ValueError, SyntaxError, MemoryError) as exc:
        log.debug("ast.literal_eval could not parse dict: %s", exc)

    raise ValueError(f"Unable to parse arguments as valid JSON: {raw[:120]}")
