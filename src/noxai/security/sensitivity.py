"""Secret detection, masking, and sensitivity analysis for NoX Privacy Policy v2.

Provides regex-based secret scanning for credentials, API keys, private keys,
and PII, along with heuristic sensitivity detection for user prompts (§1.2, §1.3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class SecretMatch:
    """Represents a detected secret or credential in a text string."""

    pattern_name: str
    matched_text: str
    start: int
    end: int

    @property
    def masked_preview(self) -> str:
        """Return a masked preview (first 4 and last 2 chars visible if long enough)."""
        if len(self.matched_text) <= 8:
            return "[REDACTED]"
        return f"{self.matched_text[:4]}...{self.matched_text[-2:]}"


# ---------------------------------------------------------------------------
# Secret Token Regex Patterns
# ---------------------------------------------------------------------------

SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "openai_key": re.compile(r"\b(?:sk-(?:proj-)?[a-zA-Z0-9_\-]{20,})\b"),
    "anthropic_key": re.compile(r"\bsk-ant-[a-zA-Z0-9_\-]{32,}\b"),
    "nvidia_key": re.compile(r"\bnvapi-[a-zA-Z0-9_\-]{20,}\b"),
    "github_token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36,}\b"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "bearer_token": re.compile(r"(?i)\bbearer\s+([a-zA-Z0-9_\-\.]{20,})\b"),
    "generic_api_key": re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|secret[_-]?token)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-]{16,})['\"]?"
    ),
    "private_key": re.compile(
        r"-----BEGIN (?:RSA |DSA |EC |OPENSSH |PGP |ENCRYPTED |)PRIVATE KEY[^-]*-----",
        re.DOTALL,
    ),
    "password_assignment": re.compile(
        r"(?i)\b(?:password|passwd|pwd|client_secret)\s*[:=]\s*['\"]?([^\s'\"]{6,})['\"]?"
    ),
    "credit_card": re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
}

# ---------------------------------------------------------------------------
# Sensitive Topic Patterns (for heuristic classification)
# ---------------------------------------------------------------------------

SENSITIVE_TOPIC_PATTERNS: dict[str, re.Pattern[str]] = {
    "health_medical": re.compile(
        r"(?i)\b(?:my (?:medical records?|diagnosis|prescription|doctor|"
        r"blood test|symptoms?)|prescribed to me|health condition)\b"
    ),
    "financial_banking": re.compile(
        r"(?i)\b(?:my (?:bank account|routing number|credit card|"
        r"salary|tax return|wire transfer))\b"
    ),
    "auth_credentials": re.compile(
        r"(?i)\b(?:my (?:master password|recovery phrase|seed phrase|private key))\b"
    ),
}


def scan_for_secrets(text: str) -> list[SecretMatch]:
    """Scan text for credentials, keys, tokens, and PII patterns.

    Returns a list of ``SecretMatch`` objects sorted by start position.
    """
    matches: list[SecretMatch] = []
    for name, pattern in SECRET_PATTERNS.items():
        for m in pattern.finditer(text):
            # For capture groups (e.g. bearer, password assignment), highlight the capture group
            if m.lastindex and m.lastindex >= 1:
                start, end = m.span(1)
                matched_val = m.group(1)
            else:
                start, end = m.span()
                matched_val = m.group(0)
            matches.append(
                SecretMatch(
                    pattern_name=name,
                    matched_text=matched_val,
                    start=start,
                    end=end,
                )
            )

    matches.sort(key=lambda m: m.start)
    return matches


def mask_secrets(text: str, mask: str = "[REDACTED_SECRET]") -> str:
    """Mask all detected secret patterns in the text with a safe replacement string.

    Overlapping spans are resolved so that earlier offsets remain consistent.
    """
    matches = scan_for_secrets(text)
    if not matches:
        return text

    # Sort descending by start position to safely replace in-place
    sorted_matches = sorted(matches, key=lambda m: m.start, reverse=True)
    filtered: list[SecretMatch] = []
    last_start = len(text) + 1

    for m in sorted_matches:
        if m.end <= last_start:
            filtered.append(m)
            last_start = m.start

    result = text
    for m in filtered:
        result = result[: m.start] + mask + result[m.end :]
    return result


@dataclass
class SensitivityResult:
    """Outcome of sensitivity detection on user input."""

    is_sensitive: bool
    reason: str | None = None
    secrets_found: list[str] = field(default_factory=list)
    sensitive_categories: list[str] = field(default_factory=list)


def detect_sensitivity(text: str) -> SensitivityResult:
    """Analyze text for secrets or sensitive personal/financial/medical topics."""
    secrets = scan_for_secrets(text)
    secret_names = [s.pattern_name for s in secrets]
    topics: list[str] = []
    for topic, pattern in SENSITIVE_TOPIC_PATTERNS.items():
        if pattern.search(text):
            topics.append(topic)
    is_sens = bool(secrets or topics)
    reason = None
    if secrets:
        reason = f"Contains secret token ({secrets[0].pattern_name})"
    elif topics:
        reason = f"Contains sensitive topic ({topics[0]})"
    return SensitivityResult(
        is_sensitive=is_sens,
        reason=reason,
        secrets_found=secret_names,
        sensitive_categories=topics,
    )


def is_sensitive_prompt(text: str) -> tuple[bool, str | None]:
    """Evaluate whether a prompt is sensitive and should run on the local lane.

    Returns ``(True, reason)`` if sensitive, or ``(False, None)`` if safe for cloud.
    """
    res = detect_sensitivity(text)
    return res.is_sensitive, res.reason
