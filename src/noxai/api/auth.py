"""Authentication, session token management, CSRF validation, and gate verification."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from pathlib import Path

from fastapi import HTTPException, Request, WebSocket, status

from noxai.config import Settings, get_config

log = logging.getLogger(__name__)

DEFAULT_TOKEN_PATH = Path(os.path.expanduser("~/.local/share/nox/session_token"))
COOKIE_NAME = "nox_session"


def generate_session_token(token_path: Path = DEFAULT_TOKEN_PATH) -> str:
    """Generate a random 32-byte hex token and write to file with mode 0600."""
    token = secrets.token_hex(32)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    # Write with restrictive permissions
    token_path.write_text(token, encoding="utf-8")
    os.chmod(token_path, 0o600)
    log.info("Generated new session token at %s", token_path)
    return token


def load_session_token(token_path: Path = DEFAULT_TOKEN_PATH) -> str | None:
    """Load existing session token from disk if present."""
    if token_path.exists():
        try:
            return token_path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            log.warning("Could not read session token from %s: %s", token_path, exc)
    return None


async def check_gate_authenticated(config: Settings | None = None) -> bool:
    """Evaluate gate mode: 'off' | 'token_only' | 'command' per Section 6.9."""
    if config is None:
        try:
            config = get_config()
        except Exception:
            return True

    mode = config.gate.mode
    if mode == "off" or mode == "token_only":
        return True

    if mode == "command":
        cmd = config.gate.command
        if not cmd:
            log.warning("Gate mode is 'command' but gate.command is empty; refusing access.")
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            return proc.returncode == 0
        except Exception as exc:
            log.error("Gate command execution failed: %s", exc)
            return False

    return False


def verify_session(request: Request) -> str:
    """Validate session token from cookie, header, or query param."""
    # Check for test bypass env
    if os.environ.get("NOX_AUTH_BYPASS") == "1":
        return "bypass_token"

    valid_token = load_session_token()
    if not valid_token:
        # If token file not present, generate one
        valid_token = generate_session_token()

    # 1. Check HttpOnly cookie
    cookie_token = request.cookies.get(COOKIE_NAME)
    if cookie_token and secrets.compare_digest(cookie_token, valid_token):
        return valid_token

    # 2. Check Authorization header (Bearer <token>)
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        bearer_token = auth_header[7:].strip()
        if secrets.compare_digest(bearer_token, valid_token):
            return valid_token

    # 3. Check X-Nox-Token header
    custom_header = request.headers.get("X-Nox-Token")
    if custom_header and secrets.compare_digest(custom_header, valid_token):
        return valid_token

    # 4. Check query param ?t=
    query_token = request.query_params.get("t")
    if query_token and secrets.compare_digest(query_token, valid_token):
        return valid_token

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized: invalid or missing session token.",
    )


def verify_csrf(request: Request) -> None:
    """Verify CSRF header X-Nox-Client for state-changing requests per Section 6.1."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        # Test bypass
        if os.environ.get("NOX_AUTH_BYPASS") == "1":
            return

        client_header = request.headers.get("X-Nox-Client")
        if client_header not in ("web", "cli"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: missing or invalid X-Nox-Client header.",
            )


def verify_websocket_auth(websocket: WebSocket) -> bool:
    """Validate WebSocket connection origin and session token."""
    if os.environ.get("NOX_AUTH_BYPASS") == "1":
        return True

    valid_token = load_session_token()
    if not valid_token:
        valid_token = generate_session_token()

    # Verify origin
    origin = websocket.headers.get("origin")
    if origin:
        host = websocket.headers.get("host", "127.0.0.1:7777")
        allowed = [
            f"http://{host}",
            f"https://{host}",
            "http://localhost:7777",
            "http://127.0.0.1:7777",
        ]
        if not any(origin.startswith(a) for a in allowed):
            log.warning("WebSocket rejected invalid origin: %s", origin)
            return False

    # Check cookie
    cookie = websocket.cookies.get(COOKIE_NAME)
    if cookie and secrets.compare_digest(cookie, valid_token):
        return True

    # Check query param ?token= or ?t=
    token_param = websocket.query_params.get("token") or websocket.query_params.get("t")
    if token_param and secrets.compare_digest(token_param, valid_token):
        return True

    return False
