"""Structured JSON logging for the NoX daemon.

Writes JSON-formatted logs to ``~/.local/state/nox/logs/noxd.log`` with
rotation (5 files, 5 MB each). Also writes human-readable lines to stderr
when attached to a terminal.

Usage::

    from noxai.logging_setup import setup_logging
    from noxai.config import get_config
    setup_logging(get_config())
    import logging
    log = logging.getLogger(__name__)
    log.info("Ready")
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Any, ClassVar

LOG_DIR = Path("~/.local/state/nox/logs").expanduser()
LOG_FILE = LOG_DIR / "noxd.log"
MAX_BYTES = 5 * 1024 * 1024   # 5 MB per file
BACKUP_COUNT = 5


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object."""

    # Keys that might contain secrets — redacted before logging
    _SENSITIVE_KEYS: ClassVar[frozenset[str]] = frozenset(
        {"password", "token", "api_key", "secret", "nvapi", "bearer", "authorization"}
    )

    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        # Extra fields attached by callers via logging.extra
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in {
                "args", "created", "exc_info", "exc_text", "filename",
                "funcName", "levelname", "levelno", "lineno", "message",
                "module", "msecs", "msg", "name", "pathname", "process",
                "processName", "relativeCreated", "stack_info", "taskName",
                "thread", "threadName",
            }:
                continue
            # Redact sensitive-looking keys
            if any(s in key.lower() for s in self._SENSITIVE_KEYS):
                doc[key] = "[REDACTED]"
            else:
                doc[key] = value
        return json.dumps(doc, default=str)


class _HumanFormatter(logging.Formatter):
    """Simple human-readable formatter for stderr output."""

    _LEVEL_COLORS: ClassVar[dict[str, str]] = {
        "DEBUG":    "\033[36m",
        "INFO":     "\033[32m",
        "WARNING":  "\033[33m",
        "ERROR":    "\033[31m",
        "CRITICAL": "\033[35m",
    }
    _RESET: ClassVar[str] = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self._LEVEL_COLORS.get(record.levelname, "")
        reset = self._RESET if color else ""
        ts = self.formatTime(record, datefmt="%H:%M:%S")
        base = f"{ts} {color}{record.levelname:8s}{reset} [{record.name}] {record.getMessage()}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def setup_logging(level: str = "INFO") -> None:
    """Configure structured JSON logging for the daemon.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
               Defaults to INFO.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(numeric_level)

    # Remove any default handlers added by other libraries
    root.handlers.clear()

    # ── Rotating JSON file handler ───────────────────────────────────────
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(numeric_level)
    file_handler.setFormatter(_JsonFormatter())
    root.addHandler(file_handler)

    # ── Human-readable stderr handler (only when attached to a tty) ──────
    if sys.stderr.isatty():
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(numeric_level)
        stderr_handler.setFormatter(_HumanFormatter())
        root.addHandler(stderr_handler)

    # Quiet down noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("feedparser").setLevel(logging.WARNING)
