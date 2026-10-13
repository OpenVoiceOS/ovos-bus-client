"""When a record's occurrences fall (SCHEDULER-1 §3.4).

These functions answer one question — the first occurrence strictly after a
given instant — for each of the four timing forms. They know nothing about
``count``, ``until`` or what has already fired; those bounds belong to the
schedule (see :mod:`.schedules`).
"""
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from ovos_bus_client.util.scheduled_events.records import (
    WEEKDAYS, parse_instant, parse_wall_clock)

#: how far ahead a wall-clock rule looks before giving up; a rule listing a
#: single weekday needs a week, and a year of slack costs nothing
_LOOKAHEAD_DAYS = 400


def next_occurrence(record: dict, after: datetime,
                    estimate: Optional[datetime] = None) -> Optional[datetime]:
    """The first occurrence of ``record`` strictly after ``after``.

    ``estimate`` is the wall-clock instant a relative delay is projected
    onto; it is the only timing form whose answer is not a function of the
    record alone (§3.4.3).
    """
    if "at" in record:
        at = parse_instant(record["at"], "at")
        return at if at > after else None
    if "in" in record:
        return estimate if estimate is not None and estimate > after else None
    if "every" in record:
        return _next_period_occurrence(record["every"], after)
    return next_wall_clock_occurrence(record["local"], after)


def _next_period_occurrence(every: dict, after: datetime) -> datetime:
    """Occurrence *n* is ``start + n x seconds``.

    The series is anchored on the schedule and never on the previous fire, so
    a late fire does not shift the occurrences that follow it (§3.4.1).
    """
    start = parse_instant(every["start"], "every.start")
    seconds = every["seconds"]
    if after < start:
        return start
    elapsed = (after - start).total_seconds()
    periods = int(elapsed // seconds) + 1
    return start + timedelta(seconds=periods * seconds)


def next_wall_clock_occurrence(local: dict, after: datetime) -> Optional[datetime]:
    """The next instant at which the wall clock in ``local.zone`` reads
    ``local.time`` on one of ``local.days`` (§3.4.2)."""
    zone = ZoneInfo(local["zone"])
    hour, minute, second = parse_wall_clock(local["time"])
    days = local.get("days") or list(WEEKDAYS)

    day = after.astimezone(zone).date()
    for _ in range(_LOOKAHEAD_DAYS):
        if WEEKDAYS[day.weekday()] in days:
            occurrence = wall_clock_instant(day, hour, minute, second, zone)
            # a spring-forward gap can push the reading onto the next day,
            # which the owner did not ask for
            landed_on = occurrence.astimezone(zone)
            if occurrence > after and WEEKDAYS[landed_on.weekday()] in days:
                return occurrence
        day += timedelta(days=1)
    return None


def wall_clock_instant(day: date, hour: int, minute: int, second: int,
                       zone: ZoneInfo) -> datetime:
    """The instant at which the wall clock in ``zone`` reads the given time
    on ``day``.

    A daylight-saving change makes two readings ambiguous. Across a
    spring-forward gap the wall clock never reads the time at all, and the
    occurrence is the first instant after the gap; across a fall-back overlap
    it reads it twice, and the occurrence is the first of the two (§3.4.2).
    """
    wanted = datetime(day.year, day.month, day.day, hour, minute, second)
    earlier = wanted.replace(tzinfo=zone, fold=0)
    if _reading_in(earlier, zone) == wanted:
        return earlier
    return _first_instant_reading_at_or_past(wanted, zone)


def _reading_in(moment: datetime, zone: ZoneInfo) -> datetime:
    """What the wall clock in ``zone`` reads at ``moment``, as a naive time."""
    return moment.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)


def _first_instant_reading_at_or_past(wanted: datetime,
                                      zone: ZoneInfo) -> datetime:
    """Bisect the day around a gap for the instant the clock resumes at.

    The wall clock is monotonic across a gap, so the boundary is found by
    halving a window that starts inside the gap's own day.
    """
    before = (wanted - timedelta(hours=6)).replace(tzinfo=zone).astimezone(timezone.utc)
    after = (wanted + timedelta(hours=6)).replace(tzinfo=zone).astimezone(timezone.utc)
    while after - before > timedelta(seconds=1):
        middle = before + (after - before) / 2
        if _reading_in(middle, zone) < wanted:
            before = middle
        else:
            after = middle
    return after.astimezone(zone)
