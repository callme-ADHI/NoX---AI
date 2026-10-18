"""Single-instance daemon process lock via fcntl.flock."""

from __future__ import annotations

import fcntl
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_PID_PATH = Path(os.path.expanduser("~/.local/share/nox/noxd.pid"))


class DaemonLock:
    """Manages an exclusive file lock and PID file for the daemon."""

    def __init__(self, pid_path: Path | None = None) -> None:
        env_path = os.environ.get("NOX_PID_FILE")
        self.pid_path = pid_path or (Path(env_path) if env_path else DEFAULT_PID_PATH)
        self._fd: int | None = None

    def acquire(self) -> None:
        """Acquire the exclusive lock.

        Raises:
            RuntimeError: If another instance is already holding the lock.
        """
        self.pid_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.pid_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            existing_pid = "unknown"
            try:
                raw = os.read(fd, 32).decode("utf-8").strip()
                if raw:
                    existing_pid = raw
            except Exception as read_exc:
                log.debug("Could not read PID from lock file: %s", read_exc)
            os.close(fd)
            raise RuntimeError(f"Daemon already running (PID {existing_pid})") from exc

        # Lock acquired: write current process PID
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, f"{os.getpid()}\n".encode())
        os.fsync(fd)
        self._fd = fd
        log.info("Acquired daemon lock at %s (PID %d)", self.pid_path, os.getpid())

    def release(self) -> None:
        """Release the lock and remove the PID file."""
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
            if self.pid_path.exists():
                try:
                    self.pid_path.unlink()
                except OSError:
                    pass
            log.info("Released daemon lock at %s", self.pid_path)

    def __enter__(self) -> DaemonLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()
