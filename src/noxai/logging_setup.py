"""Structured JSON logging for the NoX daemon.

Writes JSON-formatted logs to ``~/.local/state/nox/logs/noxd.log`` with
rotation (5 files, 5 MB each). Also writes human-readable lines to stderr
when attached to a terminal.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import sys
from pathlib import Path
from typing import Any, ClassVar

LOG_DIR = Path(os.path.expanduser("~/.local/state/nox/logs"))
LOG_FILE = LOG_DIR / "noxd.log"
MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file
BACKUP_COUNT = 5

_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {"password", "token", "api_key", "secret", "nvapi", "bearer", "authorization"}
)


def redact_text(text: str) -> str:
    """Redact known secret patterns and sensitive key-value pairs from text."""
    if not isinstance(text, str):
        return text

    # Exact token patterns
    text = re.sub(r"nvapi-[A-Za-z0-9_\-]+", "nvapi-[REDACTED]", text)
    text = re.sub(r"sk-[A-Za-z0-9_\-]+", "sk-[REDACTED]", text)
    text = re.sub(r"ghp_[A-Za-z0-9_\-]+", "ghp-[REDACTED]", text)
    text = re.sub(
        r"(?i)\bAuthorization:\s*(?:Bearer\s+)?[A-Za-z0-9_\-\.]+",
        "Authorization: [REDACTED]",
        text,
    )
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.]+", "Bearer [REDACTED]", text)

    # Key-value assignments (e.g. password=..., token=..., api_key=...)
    text = re.sub(
        r"(?i)\b(password\s*[:=]\s*)[^\s,;&'\"]+",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)\b(token\s*[:=]\s*)[^\s,;&'\"]+",
        r"\1[REDACTED]",
        text,
    )
    text = re.sub(
        r"(?i)\b(api_key\s*[:=]\s*)[^\s,;&'\"]+",
        r"\1[REDACTED]",
        text,
    )
    return text


def redact_data(data: Any) -> Any:
    """Recursively redact secrets and prompt text from dicts, lists, and strings."""
    if isinstance(data, dict):
        result: dict[str, Any] = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(s in k_lower for s in _SENSITIVE_KEYS):
                result[k] = "[REDACTED]"
            elif k_lower in ("prompt", "system_prompt", "user_prompt", "raw_prompt"):
                result[k] = "[REDACTED]"
            else:
                result[k] = redact_data(v)
        return result
    elif isinstance(data, (list, tuple)):
        return [redact_data(item) for item in data]
    elif isinstance(data, str):
        return redact_text(data)
    return data


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        # Message may be produced from record.msg % record.args
        raw_msg = redact_text(record.getMessage())

        doc: dict[str, Any] = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": raw_msg,
        }
        if record.exc_info:
            doc["exc"] = redact_text(self.formatException(record.exc_info))

        # Extra fields attached by callers via extra={...}
        prompt_keys = {"prompt", "system_prompt", "user_prompt"}
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in {
                "args",
                "created",
                "exc_info",
                "exc_text",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "message",
                "module",
                "msecs",
                "msg",
                "name",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "stack_info",
                "taskName",
                "thread",
                "threadName",
            }:
                continue

            k_lower = key.lower()
            if any(s in k_lower for s in _SENSITIVE_KEYS) or k_lower in prompt_keys:
                doc[key] = "[REDACTED]"
            else:
                doc[key] = redact_data(value)

        return json.dumps(doc, default=str)


class _HumanFormatter(logging.Formatter):
    """Simple human-readable formatter for stderr output."""

    _LEVEL_COLORS: ClassVar[dict[str, str]] = {
        "DEBUG": "\033[36m",
        "INFO": "\033[32m",
        "WARNING": "\033[33m",
        "ERROR": "\033[31m",
        "CRITICAL": "\033[35m",
    }
    _RESET: ClassVar[str] = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self._LEVEL_COLORS.get(record.levelname, "")
        reset = self._RESET if color else ""
        ts = self.formatTime(record, datefmt="%H:%M:%S")
        msg = redact_text(record.getMessage())
        base = f"{ts} {color}{record.levelname:8s}{reset} [{record.name}] {msg}"
        if record.exc_info:
            base += "\n" + redact_text(self.formatException(record.exc_info))
        return base


def setup_logging(
    level: str = "INFO",
    log_dir: Path | None = None,
    log_file: Path | None = None,
    force_tty: bool | None = None,
) -> None:
    """Configure structured JSON logging for the daemon.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_dir: Override log directory (for testing).
        log_file: Override log file path (for testing).
        force_tty: Force or disable human stderr handler.
    """
    target_dir = log_dir or LOG_DIR
    target_file = log_file or (target_dir / "noxd.log")
    target_dir.mkdir(parents=True, exist_ok=True)

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(numeric_level)

    # Remove any default handlers added by other libraries
    root.handlers.clear()

    # ── Rotating JSON file handler ───────────────────────────────────────
    file_handler = logging.handlers.RotatingFileHandler(
        target_file,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(_JsonFormatter())
    root.addHandler(file_handler)

    # ── Human-readable stderr handler (only when attached to a tty) ──────
    is_tty = force_tty if force_tty is not None else sys.stderr.isatty()
    if is_tty:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(numeric_level)
        stderr_handler.setFormatter(_HumanFormatter())
        root.addHandler(stderr_handler)

    # Quiet down noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("feedparser").setLevel(logging.WARNING)
