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
    """Parse a shift time like 9:00 or 09:00 into HH:MM (24-hour IST)."""
    text = value.strip()
    if not text:
        raise ValueError("Time cannot be empty.")

    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        raise ValueError(f"Invalid time `{value}`. Use 24-hour format like 09:00 or 14:30.")

    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Invalid time `{value}`. Use 24-hour format like 09:00 or 14:30.")
    return f"{hour:02d}:{minute:02d}"


def parse_shift_window(from_raw: str | None, to_raw: str | None) -> tuple[str | None, str | None]:
    from_text = (from_raw or "").strip()
    to_text = (to_raw or "").strip()
    if not from_text and not to_text:
        return None, None
    if not from_text or not to_text:
        raise ValueError("Set both from and to times, e.g. from1:09:00 to1:14:00")
    return parse_shift_time(from_text), parse_shift_time(to_text)


def format_shift_window(shift_from: str | None, shift_to: str | None) -> str:
    if shift_from and shift_to:
        return f" ({shift_from}–{shift_to} IST)"
    return ""
