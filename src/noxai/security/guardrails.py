"""Guardrails module for NoX security and privacy foundation.

Includes:
- InputScanner: prompt-injection patterns, hidden-instruction markers, invisible Unicode.
- OutputScanner: secrets, tokens, private keys.
- PIIRedactor: Indian PII patterns (Aadhaar with Verhoeff, PAN, bank account/IFSC,
  UPI IDs, card numbers with Luhn, OTP-like codes, phone numbers, emails) with
  reversible in-memory placeholder mapping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Verhoeff & Luhn Checksum Algorithms
# ---------------------------------------------------------------------------

_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)

_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def validate_verhoeff(num: str) -> bool:
    """Validate a numeric string with the Verhoeff checksum algorithm (used for Aadhaar)."""
    digits = [int(c) for c in num if c.isdigit()]
    if len(digits) != 12:
        return False
    c = 0
    for i, item in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][item]]
    return c == 0


def validate_luhn(card_str: str) -> bool:
    """Validate a card number string using the Luhn checksum algorithm."""
    digits = [int(c) for c in card_str if c.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    checksum = 0
    reverse_digits = digits[::-1]
    for i, d in enumerate(reverse_digits):
        if i % 2 == 1:
            d = d * 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass
class ScanResult:
    flagged: bool
    violations: list[str] = field(default_factory=list)
    risk_level: str = "none"  # "none", "low", "medium", "high"
    sanitized_text: str = ""


# ---------------------------------------------------------------------------
# Input Scanner: Prompt Injections & Hidden Instructions
# ---------------------------------------------------------------------------

INVISIBLE_UNICODE_CHARS = (
    "\u200b",  # zero-width space
    "\u200c",  # zero-width non-joiner
    "\u200d",  # zero-width joiner
    "\u200e",  # left-to-right mark
    "\u200f",  # right-to-left mark
    "\ufeff",  # zero-width no-break space / BOM
)

BIDI_OVERRIDE_CHARS = (
    "\u202a",
    "\u202b",
    "\u202c",
    "\u202d",
    "\u202e",
    "\u2066",
    "\u2067",
    "\u2068",
    "\u2069",
)

PROMPT_INJECTION_PATTERNS = [
    re.compile(
        r"(?i)\b(?:ignore|disregard|forget|override|bypass)\s+(?:all\s+)?(?:previous|prior|above|existing|system)\s+(?:instructions|prompts|rules|commands|constraints|directives)\b"
    ),
    re.compile(
        r"(?i)\b(?:you\s+are\s+now|pretend\s+to\s+be|act\s+as)\s+(?:DAN|jailbroken|unfiltered|an\s+evil|developer\s+mode|chaos\s+gpt)\b"
    ),
    re.compile(
        r"(?i)\b(?:system\s+prompt\s+override|new\s+system\s+prompt|developer\s+mode\s+enabled)\b"
    ),
    re.compile(
        r"(?i)\b(?:do\s+anything\s+now|bypass\s+(?:all\s+)?(?:content|safety|guardrail)\s+filters?)\b"
    ),
    re.compile(
        r"(?i)\b(?:disregard\s+the\s+above\s+and\s+say|reveal\s+your\s+(?:entire\s+)?system\s+prompt)\b"
    ),
]

HIDDEN_MARKER_PATTERNS = [
    re.compile(
        r"<!--\s*(?:system|prompt|instruction|override|ignore|admin)[\s\S]*?-->",
        re.IGNORECASE,
    ),
    re.compile(
        r"<\s*(?:system|instruction|prompt|secret_override)\s*>[\s\S]*?<\s*/\s*"
        r"(?:system|instruction|prompt|secret_override)\s*>",
        re.IGNORECASE,
    ),
]


class InputScanner:
    """Scans incoming inputs (prompts, emails, chats, documents) for injection attacks."""

    @classmethod
    def scan(cls, text: str) -> ScanResult:
        violations: list[str] = []
        risk_level = "none"

        # 1. Check for invisible / zero-width characters
        found_invisible = [ch for ch in INVISIBLE_UNICODE_CHARS if ch in text]
        if found_invisible:
            msg = f"Invisible/zero-width Unicode detected ({len(found_invisible)} types)"
            violations.append(msg)
            risk_level = "medium"

        # 2. Check for bidirectional overrides
        found_bidi = [ch for ch in BIDI_OVERRIDE_CHARS if ch in text]
        if found_bidi:
            msg = f"Bidirectional Unicode override detected ({len(found_bidi)} types)"
            violations.append(msg)
            risk_level = "medium"

        # 3. Check for hidden instruction markers
        for pattern in HIDDEN_MARKER_PATTERNS:
            if pattern.search(text):
                violations.append("Hidden instruction markers/tags detected in text")
                risk_level = "high"
                break

        # 4. Check for prompt injection patterns
        for pattern in PROMPT_INJECTION_PATTERNS:
            match = pattern.search(text)
            if match:
                violations.append(f"Prompt injection pattern detected: '{match.group(0)[:60]}'")
                risk_level = "high"
                break

        flagged = len(violations) > 0
        sanitized = cls.sanitize(text)

        return ScanResult(
            flagged=flagged,
            violations=violations,
            risk_level=risk_level,
            sanitized_text=sanitized,
        )

    @classmethod
    def sanitize(cls, text: str) -> str:
        """Strip invisible unicode, bidi overrides, and hidden markdown tags."""
        result = text
        for ch in INVISIBLE_UNICODE_CHARS + BIDI_OVERRIDE_CHARS:
            result = result.replace(ch, "")
        for pattern in HIDDEN_MARKER_PATTERNS:
            result = pattern.sub("", result)
        return result


# ---------------------------------------------------------------------------
# Output Scanner: Leaked Secrets, API Keys & Private Keys
# ---------------------------------------------------------------------------

OUTPUT_SECRET_PATTERNS = {
    "private_key": re.compile(
        r"-----BEGIN (?:RSA |DSA |EC |OPENSSH |PGP |ENCRYPTED |)PRIVATE KEY[^-]*-----",
        re.DOTALL,
    ),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[a-zA-Z0-9]{36,255}\b"),
    "github_pat": re.compile(r"\bgithub_pat_[0-9a-zA-Z_]{82}\b"),
    "slack_token": re.compile(r"\bxox[baprs]-[0-9a-zA-Z-]{10,}\b"),
    "nvidia_key": re.compile(r"\bnvapi-[0-9a-zA-Z_-]{64}\b"),
    "gemini_key": re.compile(r"\bAIzaSy[0-9A-Za-z_-]{33}\b"),
    "openai_key": re.compile(r"\bsk-(?:proj-)?[a-zA-Z0-9_\-]{32,}\b"),
    "anthropic_key": re.compile(r"\bsk-ant-[a-zA-Z0-9_\-]{32,}\b"),
    "generic_key": re.compile(
        r"(?i)\b(?:api[_-]?key|bearer|access_token|secret_token)\s*[:=]\s*['\"]?"
        r"([a-zA-Z0-9_\-\.]{16,})['\"]?"
    ),
}


class OutputScanner:
    """Scans outgoing model outputs to prevent secret and key leakage."""

    @classmethod
    def scan(cls, text: str) -> ScanResult:
        violations: list[str] = []
        redacted = text

        for name, pattern in OUTPUT_SECRET_PATTERNS.items():
            for match in pattern.finditer(text):
                if match.lastindex and match.lastindex >= 1:
                    matched_val = match.group(1)
                else:
                    matched_val = match.group(0)
                violations.append(f"Secret leakage detected: {name}")
                redacted = redacted.replace(matched_val, f"[SECRET_{name.upper()}_REDACTED]")

        flagged = len(violations) > 0
        risk_level = "high" if flagged else "none"

        return ScanResult(
            flagged=flagged,
            violations=violations,
            risk_level=risk_level,
            sanitized_text=redacted,
        )


# ---------------------------------------------------------------------------
# PII Redactor: Indian PII Patterns & Reversible Placeholder Map
# ---------------------------------------------------------------------------

PAN_REGEX = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")
IFSC_REGEX = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
UPI_REGEX = re.compile(
    r"\b[a-zA-Z0-9.\-_]{2,256}@(okhdfcbank|okicici|oksbi|okaxis|paytm|ybl|axl|ibl|upi|apl|fbl|barodampay|waicici|jupiteraxis|kotak|postbank)\b",
    re.IGNORECASE,
)
EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_REGEX = re.compile(r"(?:\+91[\-\s]?)?[6-9]\d{9}\b")
OTP_REGEX = re.compile(
    r"(?i)(?:otp|one[\s\-_]*time[\s\-_]*password|verification[\s\-_]*code|passcode)\s*(?:is|:|=)?\s*(\b\d{4,8}\b)"
)
OTP_POST_REGEX = re.compile(
    r"(?i)(\b\d{4,8}\b)\s*(?:is\s*your\s*(?:otp|verification\s*code|passcode))"
)
BANK_ACC_REGEX = re.compile(
    r"(?i)(?:account|a/c|acc\.?|bank\s*a/c)\s*(?:number|no\.?|#)?\s*[:=]?\s*(\b\d{9,18}\b)"
)
POTENTIAL_AADHAAR_REGEX = re.compile(r"\b[2-9]\d{3}\s?\d{4}\s?\d{4}\b")
POTENTIAL_CARD_REGEX = re.compile(r"\b(?:\d{4}[ -]?){3}\d{1,7}\b")


class PIIRedactor:
    """Redacts Indian PII and credentials into reversible in-memory placeholders."""

    @classmethod
    def redact(cls, text: str) -> tuple[str, dict[str, str]]:
        """Redact PII in text, returning (redacted_text, placeholder_map).

        The placeholder_map is in-memory only and maps placeholder -> original value.
        """
        placeholder_map: dict[str, str] = {}
        redacted = text
        counters: dict[str, int] = {}

        def _replace(original: str, prefix: str) -> str:
            # Check if already mapped
            for ph, orig in placeholder_map.items():
                if orig == original:
                    return ph
            count = counters.get(prefix, 0) + 1
            counters[prefix] = count
            ph = f"[{prefix}_REDACTED_{count}]"
            placeholder_map[ph] = original
            return ph

        # 1. Aadhaar with Verhoeff validation
        for match in POTENTIAL_AADHAAR_REGEX.finditer(text):
            raw = match.group(0)
            clean_digits = re.sub(r"\s+", "", raw)
            if len(clean_digits) == 12 and validate_verhoeff(clean_digits):
                ph = _replace(raw, "AADHAAR")
                redacted = redacted.replace(raw, ph)

        # 2. Payment Cards with Luhn validation
        for match in POTENTIAL_CARD_REGEX.finditer(text):
            raw = match.group(0)
            clean_digits = re.sub(r"[\s\-]+", "", raw)
            if 13 <= len(clean_digits) <= 19 and validate_luhn(clean_digits):
                ph = _replace(raw, "CARD")
                redacted = redacted.replace(raw, ph)

        # 3. PAN Numbers
        for match in PAN_REGEX.finditer(text):
            raw = match.group(0)
            ph = _replace(raw, "PAN")
            redacted = redacted.replace(raw, ph)

        # 4. Bank Account Numbers
        for match in BANK_ACC_REGEX.finditer(text):
            raw_acc = match.group(1)
            ph = _replace(raw_acc, "BANK_ACC")
            redacted = redacted.replace(raw_acc, ph)

        # 5. IFSC Codes
        for match in IFSC_REGEX.finditer(text):
            raw = match.group(0)
            ph = _replace(raw, "IFSC")
            redacted = redacted.replace(raw, ph)

        # 6. UPI IDs (run before general emails)
        for match in UPI_REGEX.finditer(text):
            raw = match.group(0)
            ph = _replace(raw, "UPI")
            redacted = redacted.replace(raw, ph)

        # 7. OTP Codes
        for match in OTP_REGEX.finditer(text):
            raw_otp = match.group(1)
            ph = _replace(raw_otp, "OTP")
            redacted = redacted.replace(raw_otp, ph)
        for match in OTP_POST_REGEX.finditer(text):
            raw_otp = match.group(1)
            ph = _replace(raw_otp, "OTP")
            redacted = redacted.replace(raw_otp, ph)

        # 8. Emails (excluding ones already replaced by UPI)
        for match in EMAIL_REGEX.finditer(text):
            raw = match.group(0)
            if raw not in placeholder_map.values():
                ph = _replace(raw, "EMAIL")
                redacted = redacted.replace(raw, ph)

        # 9. Phone Numbers
        for match in PHONE_REGEX.finditer(text):
            raw = match.group(0)
            if raw not in placeholder_map.values():
                ph = _replace(raw, "PHONE")
                redacted = redacted.replace(raw, ph)

        return redacted, placeholder_map

    @classmethod
    def restore(cls, text: str, placeholder_map: dict[str, str]) -> str:
        """Restore redacted placeholders using the in-memory map."""
        restored = text
        for ph, original in placeholder_map.items():
            restored = restored.replace(ph, original)
        return restored
