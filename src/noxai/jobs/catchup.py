"""Generalized Slot Catch-up Scheduling Rule (Section 5).

Evaluates whether a scheduled job is due for its most recent past slot.
Supports daily 20:00 Asia/Kolkata catch-up when powered off or rebooted.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from noxai.db import fetch_one


def get_clock_offset_seconds() -> float:
    """Return simulated clock offset in seconds from NOX_CLOCK_OFFSET env var (for testing)."""
    try:
        return float(os.environ.get("NOX_CLOCK_OFFSET", "0.0"))
    except ValueError:
        return 0.0


def get_current_time(tz_name: str = "Asia/Kolkata") -> datetime:
    """Return timezone-aware current time, with optional simulated offset for proofs."""
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    offset = get_clock_offset_seconds()
    if offset != 0.0:
        now = now + timedelta(seconds=offset)
    return now


def compute_due_slot(
    schedule_hour: int = 20,
    schedule_minute: int = 0,
    tz_name: str = "Asia/Kolkata",
    now_dt: datetime | None = None,
) -> datetime:
    """Calculate the most recent slot that is in the past.

    If current time >= today at schedule_hour:schedule_minute, due_slot is today.
    Otherwise, due_slot is yesterday at schedule_hour:schedule_minute.
    """
    tz = ZoneInfo(tz_name)
    current = now_dt if now_dt is not None else get_current_time(tz_name)
    if current.tzinfo is None:
        current = current.replace(tzinfo=tz)

    today_slot = current.replace(
        hour=schedule_hour,
        minute=schedule_minute,
        second=0,
        microsecond=0,
    )

    if current >= today_slot:
        return today_slot
    else:
        return today_slot - timedelta(days=1)


async def is_job_due(
    job_id: str,
    schedule_hour: int = 20,
    schedule_minute: int = 0,
    tz_name: str = "Asia/Kolkata",
    db: Any = None,
    now_dt: datetime | None = None,
) -> tuple[bool, str]:
    """Evaluate whether job is DUE for its most recent slot.

    Returns (is_due, due_slot_iso).
    """
    due_slot = compute_due_slot(
        schedule_hour=schedule_hour,
        schedule_minute=schedule_minute,
        tz_name=tz_name,
        now_dt=now_dt,
    )
    due_slot_utc = due_slot.astimezone(UTC)
    due_slot_iso = due_slot.strftime("%Y-%m-%d %H:%M:%S")
    due_slot_utc_str = due_slot_utc.strftime("%Y-%m-%d %H:%M:%S")

    if db is None:
        return True, due_slot_iso

    # Check if any run succeeded, is partial, or is currently running for this slot
    row = await fetch_one(
        db,
        """
        SELECT id, status, started_at, finished_at FROM job_runs
        WHERE job_id = ?
          AND (
            due_slot = ?
            OR (status IN ('succeeded', 'ok', 'partial') AND started_at >= ?)
            OR (status = 'running' AND started_at >= ?)
          )
        ORDER BY started_at DESC LIMIT 1
        """,
        (job_id, due_slot_iso, due_slot_utc_str, due_slot_utc_str),
    )

    if row is not None:
        return False, due_slot_iso

    return True, due_slot_iso
