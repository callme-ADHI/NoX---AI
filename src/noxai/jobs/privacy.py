"""Privacy sanitizer for planned jobs (Rule 7 & ADR-L2).

Replaces all filesystem paths, folder names, and remote URLs with generic placeholders
({{folder_1}}, {{remote_1}}) before transmitting job instructions to cloud models.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast


@dataclass
class SanitizedInstruction:
    sanitized_text: str
    folder_map: dict[str, str] = field(default_factory=dict)
    remote_map: dict[str, str] = field(default_factory=dict)
    alias_map: dict[str, str] = field(default_factory=dict)

    @property
    def reverse_map(self) -> dict[str, str]:
        res = {}
        for k, v in self.folder_map.items():
            res[v] = k
        for k, v in self.remote_map.items():
            res[v] = k
        return res


URL_PATTERN = re.compile(
    r"(?:https?://|git@|ssh://|git://)[^\s\"')>,]+",
    re.IGNORECASE,
)

COMMON_FOLDER_NAMES = [
    "Certificates",
    "Study",
    "Obsidian Vault",
    "NoX-Vault",
    "Projects",
    "Documents",
    "Downloads",
    "Desktop",
    "Music",
    "Pictures",
    "Videos",
]


def sanitize_instruction(instruction: str, user_home: str | None = None) -> SanitizedInstruction:
    home = user_home or os.path.expanduser("~")
    user_name = os.path.basename(home)

    folder_map: dict[str, str] = {}
    remote_map: dict[str, str] = {}
    alias_map: dict[str, str] = {}
    folder_counter = 1
    remote_counter = 1

    text = instruction

    # 1. Sanitize URLs first
    urls = list(dict.fromkeys(URL_PATTERN.findall(text)))
    for url in urls:
        ph = f"{{{{remote_{remote_counter}}}}}"
        remote_counter += 1
        remote_map[ph] = url
        text = text.replace(url, ph)

    # 2. Match paths with quoted leaf names, e.g. ~/Documents/"Obsidian Vault"
    path_with_quotes_pattern = re.compile(
        r'((?:~/[a-zA-Z0-9_./\-]*|/home/[^/\s"\']*/[a-zA-Z0-9_./\-]*|[a-zA-Z0-9_./\-]+)/)(?:"([^"]+)"|\'([^\']+)\')'
    )
    for match in path_with_quotes_pattern.finditer(instruction):
        full_match = match.group(0)
        prefix = match.group(1)
        leaf = match.group(2) or match.group(3)
        val = f"{prefix}{leaf}"
        if full_match in text:
            ph = f"{{{{folder_{folder_counter}}}}}"
            folder_counter += 1
            folder_map[ph] = val
            alias_map[leaf] = val
            text = text.replace(full_match, ph)

    # 3. Match standalone quoted folder names like "Obsidian Vault"
    quoted_pattern = re.compile(r'"([^"]+)"|\'([^\']+)\'')
    for match in quoted_pattern.finditer(instruction):
        val = match.group(1) or match.group(2)
        if val and (any(f.lower() in val.lower() for f in COMMON_FOLDER_NAMES) or "/" in val):
            full_raw = match.group(0)
            if full_raw in text:
                ph = f"{{{{folder_{folder_counter}}}}}"
                folder_counter += 1
                canonical = f"~/Documents/{val}" if "/" not in val else val
                folder_map[ph] = canonical
                alias = Path(val).name
                alias_map[alias] = canonical
                text = text.replace(full_raw, ph)

    # 4. Match explicit paths starting with ~/ or /home/... (no spaces unless quoted)
    explicit_path_pattern = re.compile(
        r"(?:~/|/home/[^/\s\"']+/|/[a-zA-Z0-9_.-]+/)[a-zA-Z0-9_./\-]+"
    )
    paths = list(dict.fromkeys(explicit_path_pattern.findall(text)))
    paths.sort(key=len, reverse=True)
    for p in paths:
        clean_p = p.strip()
        ph = f"{{{{folder_{folder_counter}}}}}"
        folder_counter += 1
        folder_map[ph] = clean_p
        alias = Path(clean_p).name.strip("\"'")
        alias_map[alias] = clean_p
        text = text.replace(p, ph)

    # 3. Match bare folder names from COMMON_FOLDER_NAMES if present
    for fname in sorted(COMMON_FOLDER_NAMES, key=len, reverse=True):
        if fname in ("Documents", "home"):
            continue
        pattern = re.compile(rf"\b{re.escape(fname)}\b", re.IGNORECASE)
        if pattern.search(text):
            ph = f"{{{{folder_{folder_counter}}}}}"
            folder_counter += 1
            canonical_path = f"~/Documents/{fname}"
            folder_map[ph] = canonical_path
            alias_map[fname] = canonical_path
            text = pattern.sub(ph, text)

    # 4. Remove any literal username occurrences
    if user_name and len(user_name) > 2 and user_name.lower() in text.lower():
        text = re.sub(re.escape(user_name), "user", text, flags=re.IGNORECASE)

    return SanitizedInstruction(
        sanitized_text=text,
        folder_map=folder_map,
        remote_map=remote_map,
        alias_map=alias_map,
    )


def resolve_plan(plan_dict: dict[str, Any], sanitized: SanitizedInstruction) -> dict[str, Any]:
    import copy

    resolved = copy.deepcopy(plan_dict)

    def _replace_val(v: Any) -> Any:
        if isinstance(v, str):
            res = v
            for ph, real in sanitized.folder_map.items():
                res = res.replace(ph, real)
            for ph, real in sanitized.remote_map.items():
                res = res.replace(ph, real)
            return res
        elif isinstance(v, list):
            return [_replace_val(item) for item in v]
        elif isinstance(v, dict):
            return {k: _replace_val(val) for k, val in v.items()}
        return v

    return cast(dict[str, Any], _replace_val(resolved))


def assert_no_paths_or_urls(data: Any, user_home: str | None = None) -> None:
    home = user_home or os.path.expanduser("~")
    user_name = os.path.basename(home)

    serialized = str(data)

    if "http://" in serialized or "https://" in serialized or "git@" in serialized:
        raise ValueError(f"Privacy breach: URL detected in payload: {serialized[:100]}")

    if "/home/" in serialized or "~/" in serialized:
        raise ValueError(f"Privacy breach: filesystem path detected in payload: {serialized[:100]}")

    for sensitive_name in ["Certificates", "Obsidian Vault", "Study", user_name]:
        if sensitive_name in serialized:
            raise ValueError(f"Privacy breach: unmasked token {sensitive_name!r} found in payload.")
