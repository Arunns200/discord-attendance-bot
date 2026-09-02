"""Timezone helpers — store UTC, display IST."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
IST = ZoneInfo("Asia/Kolkata")


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_ist(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(IST)


def format_ist(value: datetime) -> str:
    return to_ist(value).strftime("%Y-%m-%d %H:%M:%S IST")


def parse_shift_time(value: str) -> str:
    """Parse a shift time like 11, 9:00, or 09:00 into HH:MM (24-hour IST)."""
    text = value.strip()
    if not text:
        raise ValueError("Time cannot be empty.")

    if re.fullmatch(r"\d{1,2}", text):
        hour = int(text)
        if 0 <= hour <= 23:
            return f"{hour:02d}:00"
        raise ValueError(f"Invalid time `{value}`. Pick a time slot like 09:00 or 14:00.")

    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        raise ValueError(
            f"Invalid time `{value}`. Pick from the time slot list, or use 24-hour format like 09:00."
        )

    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid time `{value}`. Pick a time slot like 09:00 or 14:00.")
    return f"{hour:02d}:{minute:02d}"


def list_shift_time_slots(*, step_minutes: int = 30) -> tuple[str, ...]:
    slots: list[str] = []
    for hour in range(24):
        for minute in range(0, 60, step_minutes):
            slots.append(f"{hour:02d}:{minute:02d}")
    return tuple(slots)


def hourly_shift_time_slots() -> tuple[str, ...]:
    return tuple(f"{hour:02d}:00" for hour in range(24))


SHIFT_TIME_SLOTS = list_shift_time_slots(step_minutes=30)
HOURLY_SHIFT_TIME_SLOTS = hourly_shift_time_slots()


def filter_shift_time_slots(query: str, *, limit: int = 25) -> list[str]:
    text = query.strip()
    matches: list[str] = []
    hour_prefix: str | None = None
    if text.isdigit() and len(text) <= 2:
        hour_prefix = f"{int(text):02d}:"

    for slot in SHIFT_TIME_SLOTS:
        if text and not slot.startswith(text):
            if hour_prefix is None or not slot.startswith(hour_prefix):
                continue
        matches.append(slot)
        if len(matches) >= limit:
            break
    return matches


def parse_shift_window(from_raw: str | None, to_raw: str | None) -> tuple[str | None, str | None]:
    from_text = (from_raw or "").strip()
    to_text = (to_raw or "").strip()
    if not from_text and not to_text:
        return None, None
    if not from_text or not to_text:
        raise ValueError("Pick both start and end time slots, e.g. from1:09:00 to1:14:00")
    return parse_shift_time(from_text), parse_shift_time(to_text)


def format_shift_window(shift_from: str | None, shift_to: str | None) -> str:
    if shift_from and shift_to:
        return f" ({shift_from}–{shift_to} IST)"
    return ""
