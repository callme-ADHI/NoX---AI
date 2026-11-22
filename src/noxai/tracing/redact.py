# Portions adapted from OpenJarvis (https://github.com/open-jarvis/OpenJarvis, Apache-2.0),
# commit 77bff2548ad155ffd29a4d2e0d9c56c1eaaa754b.
# Modified for NoX: Strict pre-storage secret and API token redaction for traces and logs.

"""Pre-storage secret redaction for NoX execution traces (Workstream C).

Strips:
- NVIDIA API keys: nvapi-*
- GitHub tokens: ghp_*, github_pat_*
- Authorization headers and Bearer tokens
- Long hex or base64 tokens over 32 characters
"""

from __future__ import annotations

import re
from typing import Any

# Patterns to strip
NVAPI_PATTERN = re.compile(r"nvapi-[A-Za-z0-9_\-.]{8,}")
GITHUB_PAT_PATTERN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b")
AUTH_HEADER_PATTERN = re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[A-Za-z0-9_\-.~+/]{16,}=*")
BEARER_PATTERN = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9_\-.~+/]{16,}=*")

# Hex tokens strictly over 32 characters (e.g. 33+ continuous hex chars)
HEX_TOKEN_PATTERN = re.compile(r"\b[0-9a-fA-F]{33,}\b")

# Base64 tokens strictly over 32 characters ending with optional padding
BASE64_TOKEN_PATTERN = re.compile(r"\b[A-Za-z0-9+/]{33,}={0,2}\b")


def redact_secrets_text(text: str) -> str:
    """Redact sensitive API keys and tokens from a string."""
    if not text:
        return text

    # 1. NVIDIA API keys
    text = NVAPI_PATTERN.sub("nvapi-[REDACTED]", text)

    # 2. GitHub tokens
    text = GITHUB_PAT_PATTERN.sub("[REDACTED_GITHUB_TOKEN]", text)

    # 3. Authorization headers & Bearer tokens
    text = AUTH_HEADER_PATTERN.sub(r"\g<1>[REDACTED]", text)
    text = BEARER_PATTERN.sub(r"\g<1>[REDACTED]", text)

    # 4. Long hex tokens (> 32 chars)
    text = HEX_TOKEN_PATTERN.sub("[REDACTED_HEX_TOKEN]", text)

    # 5. Long base64 tokens (> 32 chars)
    def _b64_sub(m: re.Match[str]) -> str:
        s = m.group(0)
        # Avoid redacting common English words
        if "/" in s or "+" in s or "=" in s or (any(c.isupper() for c in s) and any(c.islower() for c in s) and any(c.isdigit() for c in s)):
            return "[REDACTED_BASE64_TOKEN]"
        return s

    text = BASE64_TOKEN_PATTERN.sub(_b64_sub, text)

    return text


def redact_secrets(val: Any) -> Any:
    """Recursively redact secrets from strings, dictionaries, lists, or tuples."""
    if isinstance(val, str):
        return redact_secrets_text(val)
    elif isinstance(val, dict):
        new_dict: dict[str, Any] = {}
        for k, v in val.items():
            k_str = str(k)
            # If the dictionary key itself suggests an API key or token, redact value completely
            if any(term in k_str.lower() for term in ("authorization", "api_key", "apikey", "secret", "token", "password")):
                if isinstance(v, str):
                    new_dict[k] = "[REDACTED]"
                else:
                    new_dict[k] = redact_secrets(v)
            else:
                new_dict[k] = redact_secrets(v)
        return new_dict
    elif isinstance(val, list):
        return [redact_secrets(item) for item in val]
    elif isinstance(val, tuple):
        return tuple(redact_secrets(item) for item in val)
    return val


def contains_secret(text: str) -> bool:
    """Check if text contains any obvious unredacted secret patterns."""
    if not text:
        return False
    if NVAPI_PATTERN.search(text):
        return True
    if GITHUB_PAT_PATTERN.search(text):
        return True
    if re.search(r"(?i)\bbearer\s+[A-Za-z0-9_\-.~+/]{16,}", text):
        return True
    if HEX_TOKEN_PATTERN.search(text):
        return True
    return False
