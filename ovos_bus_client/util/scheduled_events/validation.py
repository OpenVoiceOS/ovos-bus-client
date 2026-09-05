"""What a request must contain to become a stored record (SCHEDULER-1 §3.1).

A refused request leaves through :class:`.ScheduleError` carrying the wire
error code the answer will name.
"""
import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ovos_bus_client.util.scheduled_events.records import (
    MAX_DATA_BYTES, TIMING_FIELDS, WEEKDAYS, ScheduleError, format_instant,
    parse_instant, parse_wall_clock)
from ovos_bus_client.util.scheduled_events.timing import next_occurrence


def validate_record(request: dict, namespaced: bool = True,
                    previous: Optional[dict] = None) -> dict:
    """Check a request against §3.1 and return the record to store.

    ``namespaced`` is false only for the legacy adapter, whose event names
    predate the ``<owner>.<name>`` rule. ``previous`` is the stored record of
    the same identity, if any; it is what lets an unchanged recurrence keep
    its anchor.

    The requesting message's ``context`` is kept as it arrived (§3.5), so
    that the occurrence can be emitted with it. It is not part of the
    identity: two requests that differ only in context are the same schedule,
    and the later one's context is the one that fires.
    """
    if not isinstance(request, dict):
        raise ScheduleError("invalid_record", "request data must be an object")

    owner = _required_name(request.get("owner"), "owner")
    record = {"id": _required_name(request.get("id"), "id"),
              "owner": owner,
              "event": _validated_event(request.get("event"), owner, namespaced),
              "data": _validated_payload(request.get("data"))}

    context = _validated_context(request.get("context"))
    if context:
        record["context"] = context

    timing = _sole_timing_field(request)
    record[timing] = _validated_timing(timing, request, previous)
    record.update(_validated_bounds(timing, request))
    record.update(_validated_policies(request))
    _reject_a_recurrence_that_can_never_fire(record)
    return record


def _reject_a_recurrence_that_can_never_fire(record: dict):
    """§4 makes "describes no occurrence" a ``bad_recurrence``.

    A recurrence bounded before its own first occurrence would otherwise be
    stored forever: only a fire retires a schedule, and this one never fires.
    """
    if "at" in record or "in" in record:
        return
    if "every" in record:
        first = parse_instant(record["every"]["start"], "every.start")
    else:
        first = next_occurrence(record, datetime.now(timezone.utc))
    if first is None:
        raise ScheduleError("bad_recurrence",
                            "the recurrence describes no occurrence")
    until = record.get("until")
    if until is not None and first > parse_instant(until, "until"):
        raise ScheduleError(
            "bad_recurrence",
            f"the recurrence's first occurrence {format_instant(first)} is "
            f"after until {until}")


def _required_name(value, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ScheduleError("invalid_record", f"{field} is required")
    return value


def _validated_event(event, owner: str, namespaced: bool) -> str:
    """The message type each occurrence fires (§6.1).

    The ``<owner>.<name>`` shape makes a fired event attributable to its
    owner, and the ban on ``:`` keeps it clear of the dispatch shape of
    MSG-1 §2.1.1, so a schedule can never address another component's
    registered handler.
    """
    if not isinstance(event, str) or not event:
        raise ScheduleError("bad_event", "event is required")
    if not namespaced:
        return event
    prefix = f"{owner}."
    name = event[len(prefix):] if event.startswith(prefix) else ""
    if not name or ":" in name:
        raise ScheduleError(
            "bad_event",
            f"event must be <owner>.<name> for owner {owner}, with a "
            f"non-empty name free of ':'; got {event}")
    return event


def _validated_payload(data) -> dict:
    payload = data or {}
    if not isinstance(payload, dict):
        raise ScheduleError("invalid_record", "data must be an object")
    try:
        size = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        raise ScheduleError("invalid_record", "data is not JSON serializable")
    if size > MAX_DATA_BYTES:
        raise ScheduleError("payload_too_large",
                            f"data is {size} bytes, the limit is {MAX_DATA_BYTES}")
    return payload


def _validated_context(context) -> dict:
    """The message context to fire the occurrence with, as it arrived.

    §3.5 forbids adding to it, removing from it or rewriting it, and the
    caller takes it from the request message rather than from the request
    body, so a component can only schedule a fire into a context it reached
    the scheduler from. Nothing here reads it; it is only checked for shape
    and for size, which it shares with the payload because it is stored and
    replayed exactly as the payload is.
    """
    if context is None:
        return {}
    if not isinstance(context, dict):
        raise ScheduleError("invalid_record", "context must be an object")
    try:
        size = len(json.dumps(context, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        raise ScheduleError("invalid_record", "context is not JSON serializable")
    if size > MAX_DATA_BYTES:
        raise ScheduleError("payload_too_large",
                            f"context is {size} bytes, the limit is "
                            f"{MAX_DATA_BYTES}")
    return context


def _sole_timing_field(request: dict) -> str:
    present = [field for field in TIMING_FIELDS
               if request.get(field) is not None]
    if len(present) != 1:
        raise ScheduleError("invalid_record",
                            "exactly one of at, in, every, local is required")
    return present[0]


def _validated_timing(timing: str, request: dict, previous: Optional[dict]):
    if timing == "at":
        return format_instant(parse_instant(request["at"], "at"))
    if timing == "in":
        return _validated_delay(request["in"])
    if timing == "every":
        return _validated_period(request["every"], previous)
    return _validated_wall_clock_rule(request["local"])


def _validated_delay(delay) -> dict:
    if not isinstance(delay, dict):
        raise ScheduleError("invalid_record", "in must be an object")
    return {"seconds": _positive_number(delay.get("seconds"), "in.seconds",
                                        "invalid_record")}


def _validated_period(every, previous: Optional[dict]) -> dict:
    if not isinstance(every, dict):
        raise ScheduleError("bad_recurrence", "every must be an object")
    seconds = _positive_number(every.get("seconds"), "every.seconds",
                               "bad_recurrence")
    start = every.get("start")
    if start is not None:
        start = format_instant(parse_instant(start, "every.start"))
    elif previous and previous.get("every", {}).get("seconds") == seconds:
        # an owner re-creating an unchanged recurrence keeps its phase (§3.4.1)
        start = previous["every"]["start"]
    else:
        start = format_instant(datetime.now(timezone.utc) +
                               timedelta(seconds=seconds))
    return {"seconds": seconds, "start": start}


def _validated_wall_clock_rule(local) -> dict:
    if not isinstance(local, dict):
        raise ScheduleError("bad_recurrence", "local must be an object")
    parse_wall_clock(local.get("time"))
    zone = local.get("zone")
    if not isinstance(zone, str) or not zone:
        raise ScheduleError("bad_recurrence", "local.zone is required")
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ScheduleError("bad_recurrence", f"unknown time zone: {zone}")
    rule = {"time": local["time"], "zone": zone}
    days = local.get("days")
    if days is not None:
        if not isinstance(days, list) or not days or \
                any(day not in WEEKDAYS for day in days):
            raise ScheduleError("bad_recurrence",
                                "local.days must be a non-empty list of mon..sun")
        rule["days"] = list(days)
    return rule


def _validated_bounds(timing: str, request: dict) -> dict:
    """``until`` and ``count`` bound a recurrence and mean nothing to a
    one-shot, which has exactly one occurrence to begin with (§3.1)."""
    given = {field for field in ("until", "count")
             if request.get(field) is not None}
    if timing in ("at", "in"):
        if given:
            raise ScheduleError("invalid_record",
                                "until and count are for recurring schedules")
        return {}

    bounds = {}
    if "until" in given:
        bounds["until"] = format_instant(parse_instant(request["until"], "until"))
    if "count" in given:
        count = request["count"]
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ScheduleError("invalid_record", "count must be an integer >= 1")
        bounds["count"] = count
    return bounds


def _validated_policies(request: dict) -> dict:
    misfire = request.get("misfire", "late")
    if misfire not in ("late", "skip", "all"):
        raise ScheduleError("invalid_record", "misfire must be late, skip or all")

    grace = request.get("grace_s", 60)
    if not isinstance(grace, (int, float)) or isinstance(grace, bool) or grace < 0:
        raise ScheduleError("invalid_record", "grace_s must be a number >= 0")

    ephemeral = request.get("ephemeral", False)
    if not isinstance(ephemeral, bool):
        raise ScheduleError("invalid_record", "ephemeral must be a boolean")

    return {"misfire": misfire, "grace_s": grace, "ephemeral": ephemeral}


def _positive_number(value, field: str, code: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ScheduleError(code, f"{field} must be a number > 0")
    return value
