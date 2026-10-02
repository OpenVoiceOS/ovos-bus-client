"""The scheduler: fires a named event on the bus at a wall-clock instant.

A component asks for an event to be emitted once, or on a recurrence, and the
scheduler emits it — surviving restarts, clock steps and daylight saving on
the component's behalf. The protocol is SCHEDULER-1; the specification's
section numbers appear in the docstrings that implement them.

The implementation is split by concern, and a reader looking for one thing
finds it in one place:

``topics``
    the bus topic names, mirroring the platform's message registry.
``records``
    the vocabulary a record is written in: instants, wall-clock times, and
    the error a refused request carries.
``validation``
    what a request must contain to become a stored record.
``timing``
    occurrence arithmetic for the four timing forms, including the
    daylight-saving rules.
``schedules``
    a stored record plus the state the scheduler keeps beside it, and the
    bounds (``count``, ``until``, the last fire) that state imposes.
``store``
    the file schedules survive a restart in, written atomically.
``service``
    the service itself: requests, answers, evaluation, firing.
``legacy``
    the pre-specification ``mycroft.scheduler.*`` protocol, translated.
"""
from ovos_bus_client.util.scheduled_events import topics
from ovos_bus_client.util.scheduled_events.legacy import (
    LEGACY_REMOVAL_VERSION, LegacyAdapter)
from ovos_bus_client.util.scheduled_events.records import (
    MAX_DATA_BYTES, ScheduleError, format_instant, parse_instant)
from ovos_bus_client.util.scheduled_events.schedules import MAX_REPORTED, Schedule
from ovos_bus_client.util.scheduled_events.service import ScheduledEventService
from ovos_bus_client.util.scheduled_events.store import (
    ScheduleStore, default_store_path)
from ovos_bus_client.util.scheduled_events.validation import validate_record

__all__ = ["LEGACY_REMOVAL_VERSION", "LegacyAdapter", "MAX_DATA_BYTES",
           "MAX_REPORTED", "Schedule", "ScheduleError", "ScheduleStore",
           "ScheduledEventService", "default_store_path", "format_instant",
           "parse_instant", "topics", "validate_record"]
