"""Tests for structured logging, secret redaction, and log rotation settings (Phase E4.6)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from noxai.logging_setup import (
    BACKUP_COUNT,
    MAX_BYTES,
    _HumanFormatter,
    _JsonFormatter,
    redact_data,
    redact_text,
    setup_logging,
)


def test_redact_text_patterns() -> None:
    """Verify redaction of all required secret patterns in text."""
    # nvapi-
    assert redact_text("key nvapi-1234567890abcdef") == "key nvapi-[REDACTED]"
    # sk-
    assert redact_text("OpenAI sk-abcdef1234567890") == "OpenAI sk-[REDACTED]"
    # ghp_
    assert redact_text("GitHub ghp_abcdef1234567890") == "GitHub ghp-[REDACTED]"
    # Bearer
    assert redact_text("Bearer abc.def.1234567890") == "Bearer [REDACTED]"
    # Authorization:
    assert redact_text("Authorization: Bearer secret_token_12345") == "Authorization: [REDACTED]"
    # password=
    assert redact_text("password=my_super_secret_pass;") == "password=[REDACTED];"
    assert redact_text("PASSWORD: admin123") == "PASSWORD: [REDACTED]"
    # token=
    assert redact_text("token=eyJhbGciOiJIUzI1NiJ9;") == "token=[REDACTED];"
    # api_key=
    assert redact_text("api_key=my_custom_api_key_999") == "api_key=[REDACTED]"


def test_redact_data_nested_structures() -> None:
    """Verify recursive redaction in dicts, lists, and sensitive keys."""
    data = {
        "user": "alice",
        "auth": {
            "password": "plain_password",
            "token": "secret_session_token",
            "api_key": "raw_api_key",
            "nested_list": [
                "normal",
                "nvapi-1234567890abcdef",
                {"deep_secret": "my_secret"},
            ],
        },
        "prompt": "This is a private user prompt about sensitive personal data",
        "system_prompt": "Confidential system prompt instructions",
    }
    redacted = redact_data(data)

    assert redacted["user"] == "alice"
    assert redacted["auth"]["password"] == "[REDACTED]"
    assert redacted["auth"]["token"] == "[REDACTED]"
    assert redacted["auth"]["api_key"] == "[REDACTED]"
    assert redacted["auth"]["nested_list"][0] == "normal"
    assert redacted["auth"]["nested_list"][1] == "nvapi-[REDACTED]"
    assert redacted["prompt"] == "[REDACTED]"
    assert redacted["system_prompt"] == "[REDACTED]"


def test_json_formatter_plain_and_percent_args() -> None:
    """Verify _JsonFormatter with plain messages and %-style args."""
    formatter = _JsonFormatter()

    # Plain message with secret
    record = logging.LogRecord(
        name="noxai.test",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Using key nvapi-abcdef1234567890 for request",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)
    doc = json.loads(formatted)
    assert doc["level"] == "INFO"
    assert doc["logger"] == "noxai.test"
    assert "nvapi-[REDACTED]" in doc["msg"]
    assert "1234567890" not in doc["msg"]

    # %-style args with password
    record_args = logging.LogRecord(
        name="noxai.test",
        level=logging.WARNING,
        pathname="test.py",
        lineno=20,
        msg="Authentication failed for user %s with password=%s",
        args=("bob", "secret_pass_123"),
        exc_info=None,
    )
    formatted_args = formatter.format(record_args)
    doc_args = json.loads(formatted_args)
    assert "password=[REDACTED]" in doc_args["msg"]
    assert "secret_pass_123" not in doc_args["msg"]


def test_json_formatter_exception_traceback() -> None:
    """Verify secrets inside exception tracebacks are redacted."""
    formatter = _JsonFormatter()

    try:
        raise ValueError("Failed connecting to https://api.com with ghp_secrettoken1234567")
    except ValueError:
        import sys
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="noxai.test",
        level=logging.ERROR,
        pathname="test.py",
        lineno=30,
        msg="Encountered an unhandled exception",
        args=(),
        exc_info=exc_info,
    )
    formatted = formatter.format(record)
    doc = json.loads(formatted)
    assert "exc" in doc
    assert "ghp-[REDACTED]" in doc["exc"]
    assert "ghp_secrettoken1234567" not in doc["exc"]


def test_json_formatter_model_logger_prompt_protection() -> None:
    """Verify that private prompt text is never logged by model-call loggers."""
    formatter = _JsonFormatter()

    record = logging.LogRecord(
        name="noxai.models.router",
        level=logging.INFO,
        pathname="router.py",
        lineno=45,
        msg="Dispatching request to model with prompt: confidential financial analysis",
        args=(),
        exc_info=None,
    )
    record.prompt = "Private user health records"  # type: ignore[attr-defined]
    record.custom_data = {"key": "value"}  # type: ignore[attr-defined]

    formatted = formatter.format(record)
    doc = json.loads(formatted)
    assert doc["prompt"] == "[REDACTED]"
    assert doc["custom_data"] == {"key": "value"}


def test_human_formatter_output() -> None:
    """Verify _HumanFormatter formats human-readable lines with color codes."""
    formatter = _HumanFormatter()
    record = logging.LogRecord(
        name="noxai.test",
        level=logging.INFO,
        pathname="test.py",
        lineno=50,
        msg="Normal message with token=abc123456789",
        args=(),
        exc_info=None,
    )
    output = formatter.format(record)
    assert "INFO" in output
    assert "token=[REDACTED]" in output
    assert "abc123456789" not in output


def test_setup_logging_rotation_and_handlers(tmp_path: Path) -> None:
    """Verify setup_logging configures 5 files x 5 MB rotation and JSON formatting."""
    log_file = tmp_path / "test_noxd.log"
    setup_logging(level="DEBUG", log_dir=tmp_path, log_file=log_file, force_tty=True)

    root = logging.getLogger()
    assert root.level == logging.DEBUG

    # Verify RotatingFileHandler constants
    assert MAX_BYTES == 5 * 1024 * 1024
    assert BACKUP_COUNT == 5

    file_handlers = [
        h for h in root.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert len(file_handlers) == 1
    rfh = file_handlers[0]
    assert rfh.maxBytes == 5 * 1024 * 1024
    assert rfh.backupCount == 5
    assert isinstance(rfh.formatter, _JsonFormatter)

    # Verify stderr handler present when force_tty is True
    stderr_handlers = [
        h
        for h in root.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert len(stderr_handlers) == 1
    assert isinstance(stderr_handlers[0].formatter, _HumanFormatter)

    # Write a log entry and verify file content
    test_logger = logging.getLogger("test.daemon")
    test_logger.info("Daemon started with nvapi-supersecrettoken")

    # Flush handlers
    rfh.flush()

    assert log_file.exists()
    content = log_file.read_text()
    assert "nvapi-[REDACTED]" in content
    assert "nvapi-supersecrettoken" not in content

    parsed = json.loads(content.strip().splitlines()[-1])
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test.daemon"
    assert "nvapi-[REDACTED]" in parsed["msg"]
