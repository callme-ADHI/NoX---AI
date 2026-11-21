"""Secret store abstraction for NoX.

Supports:
- System keyring (via `keyring` library when available and working).
- Fallback to a protected, 0600-permission file under:
  ``~/.local/share/nox/connectors/secrets.json``
- Zero-leak guarantee: values are never written to logs or displayed in listing.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from noxai.db import get_data_dir

log = logging.getLogger(__name__)

SERVICE_NAME = "nox-ai"


def _get_secrets_file_path() -> Path:
    connectors_dir = get_data_dir() / "connectors"
    connectors_dir.mkdir(parents=True, exist_ok=True)
    try:
        connectors_dir.chmod(0o700)
    except OSError:
        pass
    return connectors_dir / "secrets.json"


def _read_file_store() -> dict[str, str]:
    path = _get_secrets_file_path()
    if not path.exists():
        return {}
    try:
        # Enforce 0600 permissions
        mode = path.stat().st_mode & 0o777
        if mode != 0o600:
            path.chmod(0o600)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
    except Exception as exc:
        log.warning("Failed to read secrets file store: %s", exc)
    return {}


def _write_file_store(store: dict[str, str]) -> None:
    path = _get_secrets_file_path()
    temp_path = path.with_suffix(".tmp")
    # Write to temp file with 0600 permissions then atomic replace
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(temp_path, flags, 0o600)
    with open(fd, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)
    os.replace(temp_path, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _has_working_keyring() -> bool:
    try:
        import keyring
        backend = keyring.get_keyring()
        # Ensure it's not a dummy or fail backend
        name = backend.__class__.__name__.lower()
        if "fail" in name or "null" in name:
            return False
        return True
    except Exception:
        return False


class SecretStore:
    """Manages credentials and tokens securely."""

    @classmethod
    def get(cls, name: str) -> str | None:
        """Retrieve a secret by name without logging the value."""
        if _has_working_keyring():
            try:
                import keyring
                val = keyring.get_password(SERVICE_NAME, name)
                if isinstance(val, str):
                    return val
            except Exception as exc:
                log.debug("Keyring get failed for secret '%s': %s", name, exc)

        store = _read_file_store()
        return store.get(name)

    @classmethod
    def set(cls, name: str, value: str) -> None:
        """Store a secret by name without logging the value."""
        if not name or not isinstance(name, str):
            raise ValueError("Secret name must be a non-empty string")
        if not isinstance(value, str):
            raise ValueError("Secret value must be a string")

        if _has_working_keyring():
            try:
                import keyring
                keyring.set_password(SERVICE_NAME, name, value)
                log.info("Secret '%s' stored in system keyring", name)
                return
            except Exception as exc:
                log.debug("Keyring set failed for secret '%s': %s", name, exc)

        store = _read_file_store()
        store[name] = value
        _write_file_store(store)
        log.info("Secret '%s' stored in protected file store", name)

    @classmethod
    def delete(cls, name: str) -> bool:
        """Delete a secret by name."""
        found = False
        if _has_working_keyring():
            try:
                import keyring

                keyring.delete_password(SERVICE_NAME, name)
                found = True
            except Exception as exc:
                log.debug("Keyring delete note: %s", exc)

        store = _read_file_store()
        if name in store:
            del store[name]
            _write_file_store(store)
            found = True

        log.info("Secret '%s' deleted (found=%s)", name, found)
        return found

    @classmethod
    def list_names(cls) -> list[str]:
        """Return names of stored secrets (never returns values)."""
        store = _read_file_store()
        return sorted(store.keys())


# Module-level convenience functions
def get_secret(name: str) -> str | None:
    return SecretStore.get(name)


def set_secret(name: str, value: str) -> None:
    SecretStore.set(name, value)


def delete_secret(name: str) -> bool:
    return SecretStore.delete(name)


def list_secrets() -> list[str]:
    return SecretStore.list_names()
