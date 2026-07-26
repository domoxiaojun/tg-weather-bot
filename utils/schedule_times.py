"""Time-of-day parsing helpers shared by config validation and the scheduler."""
from datetime import time as dtime
from typing import Optional


def parse_brief_time(value) -> Optional[tuple]:
    """Parse HH:MM; returns (hour, minute) or None."""
    if not isinstance(value, str):
        return None
    try:
        hour_text, minute_text = value.strip().split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except ValueError:
        return None
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return None


def parse_quiet_hours(value) -> Optional[tuple]:
    """Parse "HH:MM-HH:MM" into (start, end) time tuples; None if unset/invalid.

    An empty string disables quiet hours. Start == end is rejected (would
    mean either "never" or "always" depending on interpretation).
    """
    if not isinstance(value, str) or not value.strip():
        return None
    parts = value.split("-", 1)
    if len(parts) != 2:
        return None
    start = parse_brief_time(parts[0])
    end = parse_brief_time(parts[1])
    if start is None or end is None or start == end:
        return None
    return start, end


def is_within_quiet_hours(now: dtime, window: tuple) -> bool:
    """Check a time against a quiet window; supports ranges crossing midnight."""
    (start_h, start_m), (end_h, end_m) = window
    start = dtime(start_h, start_m)
    end = dtime(end_h, end_m)
    if start < end:
        return start <= now < end
    return now >= start or now < end
