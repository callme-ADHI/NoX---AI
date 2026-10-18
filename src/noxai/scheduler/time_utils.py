"""Time calculation and natural language schedule parsing for NoX scheduler (Section 6.7)."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import dateparser
from croniter import croniter

log = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Asia/Kolkata"
MAX_CATCHUP_SECONDS = 12 * 3600  # 12 hours


def get_zoneinfo(tz_name: str | None) -> ZoneInfo:
    """Safely obtain ZoneInfo, falling back to DEFAULT_TIMEZONE or UTC."""
    if not tz_name:
        return ZoneInfo(DEFAULT_TIMEZONE)
    try:
        return ZoneInfo(tz_name)
    except Exception:
        log.warning("Invalid timezone '%s', falling back to %s", tz_name, DEFAULT_TIMEZONE)
        return ZoneInfo(DEFAULT_TIMEZONE)


def parse_interval_seconds(expr: str) -> int:
    """Parse interval string like '15m', '2h', '1d', '30s' or raw seconds integer."""
    expr = expr.strip().lower()
    match = re.match(r"^(\d+)\s*([smhd]?)$", expr)
    if not match:
        raise ValueError(f"Invalid interval expression: '{expr}'")

    val, unit = match.groups()
    num = int(val)
    if not unit or unit == "s":
        return num
    elif unit == "m":
        return num * 60
    elif unit == "h":
        return num * 3600
    elif unit == "d":
        return num * 86400
    raise ValueError(f"Unknown interval unit: '{unit}'")


def compute_next_run(
    schedule_type: str,
    schedule_expr: str,
    tz_name: str = DEFAULT_TIMEZONE,
    base_dt: datetime | None = None,
) -> datetime | None:
    """Compute the next occurrence as a timezone-aware datetime.

    Returns None for 'once' jobs whose time has passed.
    """
    tz = get_zoneinfo(tz_name)
    if base_dt is None:
        base_dt = datetime.now(UTC)
    base_in_tz = base_dt.astimezone(tz)

    if schedule_type == "cron":
        iter_obj = croniter(schedule_expr, base_in_tz)
        next_dt: datetime = iter_obj.get_next(datetime)
        return next_dt.astimezone(UTC)

    elif schedule_type == "interval":
        secs = parse_interval_seconds(schedule_expr)
        next_dt = base_in_tz + timedelta(seconds=secs)
        return next_dt.astimezone(UTC)

    elif schedule_type == "once":
        # schedule_expr can be ISO string or parsed datetime
        try:
            parsed = datetime.fromisoformat(schedule_expr)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=tz)
            utc_dt = parsed.astimezone(UTC)
            # Return if still in the future or equal
            return utc_dt if utc_dt >= base_dt else None
        except Exception as exc:
            # Try natural language parsing
            parsed_nat = parse_natural_time(schedule_expr, tz_name=tz_name, base_dt=base_dt)
            if parsed_nat:
                utc_dt = parsed_nat.astimezone(UTC)
                return utc_dt if utc_dt >= base_dt else None
            raise ValueError(
                f"Could not parse 'once' schedule expression: '{schedule_expr}'"
            ) from exc

    else:
        raise ValueError(f"Unknown schedule type: '{schedule_type}'")


def parse_natural_time(
    when_text: str,
    tz_name: str = DEFAULT_TIMEZONE,
    base_dt: datetime | None = None,
) -> datetime | None:
    """Parse a natural language time expression with dateparser into timezone-aware datetime."""
    tz = get_zoneinfo(tz_name)
    if base_dt is None:
        base_dt = datetime.now(UTC)
    base_in_tz = base_dt.astimezone(tz)

    settings: dict[str, Any] = {
        "TIMEZONE": tz_name,
        "TO_TIMEZONE": tz_name,
        "RETURN_AS_TIMEZONE_AWARE": True,
        "PREFER_DATES_FROM": "future",
        "RELATIVE_BASE": base_in_tz.replace(tzinfo=None),
    }

    raw = dateparser.parse(when_text, settings=settings)
    if not isinstance(raw, datetime):
        return None

    if raw.tzinfo is None:
        raw = raw.replace(tzinfo=tz)
    return raw.astimezone(UTC)


def format_human_time(dt: datetime, tz_name: str = DEFAULT_TIMEZONE) -> str:
    """Format datetime into a readable representation for the user in the target timezone."""
    tz = get_zoneinfo(tz_name)
    dt_tz = dt.astimezone(tz)
    return dt_tz.strftime("%a %d %b %Y, %H:%M %Z")


def should_catchup(
    next_run_at: datetime,
    now: datetime | None = None,
    catchup_policy: str = "once",
) -> bool:
    """Determine whether a missed run qualifies for catch-up per Section 6.7."""
    if catchup_policy != "once":
        return False

    if now is None:
        now = datetime.now(UTC)

    missed_seconds = (now - next_run_at).total_seconds()
    # If missed less than 12 hours ago
    return 0 <= missed_seconds <= MAX_CATCHUP_SECONDS
