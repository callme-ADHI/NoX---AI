"""Unit tests for slot catch-up scheduling rule (Section 5 & 8.1)."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pytest

from noxai.jobs.catchup import compute_due_slot, is_job_due

TZ = ZoneInfo("Asia/Kolkata")


def test_slot_at_1959():
    dt = datetime(2026, 11, 4, 19, 59, 0, tzinfo=TZ)
    slot = compute_due_slot(schedule_hour=20, schedule_minute=0, now_dt=dt)
    # At 19:59, today's 20:00 is in the future, so due_slot must be yesterday 20:00
    assert slot.year == 2026
    assert slot.month == 11
    assert slot.day == 3
    assert slot.hour == 20
    assert slot.minute == 0


def test_slot_at_2000():
    dt = datetime(2026, 11, 4, 20, 0, 0, tzinfo=TZ)
    slot = compute_due_slot(schedule_hour=20, schedule_minute=0, now_dt=dt)
    # At 20:00, due_slot becomes today 20:00
    assert slot.year == 2026
    assert slot.month == 11
    assert slot.day == 4
    assert slot.hour == 20
    assert slot.minute == 0


def test_slot_at_2130():
    dt = datetime(2026, 11, 4, 21, 30, 0, tzinfo=TZ)
    slot = compute_due_slot(schedule_hour=20, schedule_minute=0, now_dt=dt)
    # At 21:30, due_slot is today 20:00
    assert slot.year == 2026
    assert slot.month == 11
    assert slot.day == 4
    assert slot.hour == 20
    assert slot.minute == 0


def test_slot_boot_0900_next_day():
    dt = datetime(2026, 11, 5, 9, 0, 0, tzinfo=TZ)
    slot = compute_due_slot(schedule_hour=20, schedule_minute=0, now_dt=dt)
    # At 09:00 next day, due_slot is yesterday 20:00
    assert slot.year == 2026
    assert slot.month == 11
    assert slot.day == 4
    assert slot.hour == 20
    assert slot.minute == 0


@pytest.mark.asyncio
async def test_is_job_due_evaluation():
    # If no DB records exist, job is due
    due, slot_str = await is_job_due(
        job_id="job_test",
        schedule_hour=20,
        now_dt=datetime(2026, 11, 4, 20, 5, 0, tzinfo=TZ),
        db=None,
    )
    assert due is True
    assert "2026-11-04 20:00:00" in slot_str
