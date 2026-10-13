"""The vocabulary a schedule record is written in.

Instants, wall-clock times, the field names, and the error a refused request
leaves through — :class:`ScheduleError`, whose ``code`` is one of the wire
errors of SCHEDULER-1 §4.
"""
from datetime import datetime
from typing import Tuple

#: compact UTF-8 JSON bytes allowed in a record's ``data`` (§3.1)
MAX_DATA_BYTES = 16384

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

TIMING_FIELDS = ("at", "in", "every", "local")


class ScheduleError(ValueError):
    """A request the scheduler refuses, carrying its wire error code."""

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def parse_instant(value, field: str) -> datetime:
    """Read an RFC 3339 instant.

    An instant without a UTC offset is refused: without one the string does
    not name a point on the time line (§3.2).
    """
    if not isinstance(value, str):
        raise ScheduleError("bad_instant", f"{field} must be an RFC 3339 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ScheduleError("bad_instant", f"{field} is not RFC 3339: {value}")
    if parsed.tzinfo is None:
        raise ScheduleError("bad_instant", f"{field} has no UTC offset: {value}")
    return parsed


def format_instant(when: datetime) -> str:
    """Write an instant in the form :func:`parse_instant` reads back."""
    return when.isoformat()


def timing_of(record: dict) -> Tuple[str, dict]:
    """The one timing a record carries, as (field name, value).

    Two records describe the same series when this is equal for both.
    """
    for field in TIMING_FIELDS:
        if field in record:
            return field, record[field]
    raise ScheduleError("invalid_record", "record carries no timing")


def parse_wall_clock(value) -> Tuple[int, int, int]:
    """Read the ``HH:MM`` or ``HH:MM:SS`` of a wall-clock recurrence."""
    if not isinstance(value, str):
        raise ScheduleError("bad_recurrence", "local.time must be HH:MM or HH:MM:SS")
    parts = value.split(":")
    if len(parts) not in (2, 3):
        raise ScheduleError("bad_recurrence", f"local.time is not HH:MM[:SS]: {value}")
    try:
        hour, minute = int(parts[0]), int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
    except ValueError:
        raise ScheduleError("bad_recurrence", f"local.time is not HH:MM[:SS]: {value}")
    if not (0 <= hour < 24 and 0 <= minute < 60 and 0 <= second < 60):
        raise ScheduleError("bad_recurrence", f"local.time out of range: {value}")
    return hour, minute, second
