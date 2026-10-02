# Copyright 2019 Mycroft AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""The scheduler under the name the rest of the stack starts it by.

:class:`EventScheduler` is :class:`~ovos_bus_client.util.scheduled_events.
ScheduledEventService` with the epoch-float methods the pre-specification
service offered. New code talks the SCHEDULER-1 protocol instead, through
:class:`~ovos_bus_client.apis.events.SchedulerClient`.
"""
import os
import time
from typing import Dict, List, Optional, Tuple

from ovos_utils.log import log_deprecation

from ovos_bus_client.message import Message
from ovos_bus_client.util.scheduled_events import topics
from ovos_bus_client.util.scheduled_events.legacy import LEGACY_REMOVAL_VERSION
from ovos_bus_client.util.scheduled_events.service import ScheduledEventService
from ovos_bus_client.util.scheduled_events.store import default_store_path


def repeat_time(sched_time: float, repeat: float) -> float:
    """Next occurrence of a repeating event, anchored on the schedule.

    Args:
        sched_time: unix time of the occurrence that just passed
        repeat: period in seconds

    Returns: unix time of the next occurrence
    """
    period = abs(repeat)
    next_time = sched_time + period
    now = time.time()
    if next_time < now:
        # stay on the schedule's own phase rather than re-anchoring on the miss
        periods = (now - sched_time) // period + 1
        next_time = sched_time + periods * period
    return next_time


class EventScheduler(ScheduledEventService):
    """The scheduler service, reachable under its historical name.

    Args:
        bus: message bus client
        schedule_file: store file name, resolved inside the XDG state
            directory unless an absolute path is given
        autostart: start the thread on construction
    """

    def __init__(self, bus, schedule_file: str = "schedule.json",
                 autostart: bool = True):
        store = schedule_file if os.path.isabs(schedule_file) \
            else default_store_path(schedule_file)
        super().__init__(bus, store_path=store, autostart=autostart)

    @property
    def schedule_file(self) -> str:
        return self.store_path

    def schedule_event(self, event: str, sched_time: float,
                       repeat: Optional[float] = None,
                       data: Optional[dict] = None,
                       context: Optional[dict] = None):
        """Add an event to the schedule using the epoch-float API.

        ``context`` is accepted and ignored: a fired event carries a fresh
        context of its own.
        """
        self.legacy.handle_schedule(
            Message(topics.LEGACY_SCHEDULE,
                    {"event": event, "time": sched_time, "repeat": repeat,
                     "data": data}))

    def remove_event(self, event: str):
        """Remove an event from the schedule."""
        self.legacy.handle_remove(
            Message(topics.LEGACY_REMOVE, {"event": event}))

    def update_event(self, event: str, data: dict):
        """Change the data an existing event fires with."""
        self.legacy.handle_update(
            Message(topics.LEGACY_UPDATE, {"event": event, "data": data}))

    def check_state(self):
        """Fire every occurrence that is due."""
        self._evaluate()

    def store(self):
        """Write the schedule to disk."""
        self._persist()

    # --- pre-SCHEDULER-1 public surface, kept for one stable cycle --------
    #
    # PR #311 dropped these along with the ``events``/``event_lock``
    # attributes when the epoch-float scheduler became this class. Every
    # caller below is a thin delegate onto the bus handlers or state the
    # SCHEDULER-1 service already keeps.

    @property
    def events(self) -> Dict[str, List[Tuple[float, Optional[float], dict, dict]]]:
        """A snapshot in the pre-specification ``name -> [(time, repeat,
        data, context), ...]`` shape. Read-only: mutating the old dict in
        place scheduled nothing, so there is nothing to delegate a write to.
        """
        log_deprecation(
            "EventScheduler.events is a read-only snapshot of the "
            "SCHEDULER-1 schedules; use EventScheduler.schedules instead.",
            LEGACY_REMOVAL_VERSION)
        view = {}
        with self.lock:
            for (owner, schedule_id), schedule in self.schedules.items():
                entry = self.legacy._entry(schedule)
                view.setdefault(schedule.record["event"], []).append(tuple(entry))
        return view

    @property
    def event_lock(self):
        """The lock guarding ``schedules``, under its historical name."""
        log_deprecation(
            "EventScheduler.event_lock is the SCHEDULER-1 store lock; use "
            "EventScheduler.lock instead.", LEGACY_REMOVAL_VERSION)
        return self.lock

    def handle_schedule_event(self, message: Message):
        """Bus handler for ``mycroft.scheduler.schedule_event``."""
        log_deprecation(
            "EventScheduler.handle_schedule_event speaks the "
            "pre-specification protocol; schedule() instead.",
            LEGACY_REMOVAL_VERSION)
        self.legacy.handle_schedule(message)

    def handle_remove_event(self, message: Message):
        """Bus handler for ``mycroft.scheduler.remove_event``."""
        log_deprecation(
            "EventScheduler.handle_remove_event speaks the "
            "pre-specification protocol; cancel() instead.",
            LEGACY_REMOVAL_VERSION)
        self.legacy.handle_remove(message)

    def handle_update_event(self, message: Message):
        """Bus handler for ``mycroft.scheduler.update_event``."""
        log_deprecation(
            "EventScheduler.handle_update_event speaks the "
            "pre-specification protocol; schedule() instead.",
            LEGACY_REMOVAL_VERSION)
        self.legacy.handle_update(message)

    def handle_get_event(self, message: Message):
        """Bus handler for ``mycroft.scheduler.get_event``.

        Answers with the object shape ``{"event", "schedule"}``, not the
        bare list this used to reply with; the wire refuses a list-shaped
        payload (SCHEDULER-1 §4).
        """
        log_deprecation(
            "EventScheduler.handle_get_event speaks the pre-specification "
            "protocol; get() instead.", LEGACY_REMOVAL_VERSION)
        self.legacy.handle_get(message)

    def handle_list_events(self, message: Message):
        """Bus handler for ``mycroft.scheduler.list_events``."""
        log_deprecation(
            "EventScheduler.handle_list_events speaks the "
            "pre-specification protocol; list() instead.",
            LEGACY_REMOVAL_VERSION)
        self.legacy.handle_list(message)

    def handle_system_clock_sync(self, message: Message):
        """Bus handler for ``system.clock.synced``, emitted by raspOVOS."""
        log_deprecation(
            "EventScheduler.handle_system_clock_sync is served by "
            "handle_clock_synced now.", LEGACY_REMOVAL_VERSION)
        self.handle_clock_synced(message)

    def clear_repeating(self):
        """Drop every repeating schedule, under its historical name."""
        log_deprecation(
            "EventScheduler.clear_repeating drops repeating schedules; "
            "there is no SCHEDULER-1 replacement, cancel() them by id "
            "instead.", LEGACY_REMOVAL_VERSION)
        with self.lock:
            repeating = [key for key, schedule in self.schedules.items()
                        if "every" in schedule.record]
            for key in repeating:
                self.schedules.pop(key, None)

    def clear_empty(self):
        """No-op: a SCHEDULER-1 schedule is dropped the instant it is spent,
        so there is never an empty entry left behind to clear.
        """
        log_deprecation(
            "EventScheduler.clear_empty is a no-op: SCHEDULER-1 schedules "
            "are dropped as soon as they are spent.", LEGACY_REMOVAL_VERSION)

    def load(self):
        """Load the store into memory, under its historical name."""
        log_deprecation(
            "EventScheduler.load is served by the constructor now; call "
            "it only to re-read a store changed on disk.",
            LEGACY_REMOVAL_VERSION)
        with self.lock:
            self.schedules.update(self.schedule_store.load())
