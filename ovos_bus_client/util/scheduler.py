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
"""The pre-specification name and entry point of the scheduler service.

``EventScheduler`` is the SCHEDULER-1 service under the name the rest of
the stack starts it by. The behaviour is the specification's; the
``mycroft.scheduler.*`` topics and the epoch-float API remain available
through the legacy adapter for one stable cycle.
"""
import os
import time
from typing import Optional

from ovos_bus_client.message import Message
from ovos_bus_client.util.scheduled_events import (
    ScheduledEventService, default_store_path)


def repeat_time(sched_time: float, repeat: float) -> float:
    """Next scheduled time for a repeating event, anchored on the schedule.

    Args:
        sched_time: unix time of the occurrence that just passed
        repeat: period in seconds

    Returns: unix time of the next occurrence
    """
    period = abs(repeat)
    next_time = sched_time + period
    now = time.time()
    if next_time < now:
        # stay on the schedule's phase rather than re-anchoring on the miss
        periods = (now - sched_time) // period + 1
        next_time = sched_time + periods * period
    return next_time


class EventScheduler(ScheduledEventService):
    """SCHEDULER-1 service, reachable under its historical name.

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

        The ``context`` argument is accepted and ignored: a fired event
        carries a fresh context (SCHEDULER-1 §4.2).
        """
        self.handle_legacy_schedule(
            Message("mycroft.scheduler.schedule_event",
                    {"event": event, "time": sched_time, "repeat": repeat,
                     "data": data}))

    def remove_event(self, event: str):
        """Remove an event from the schedule."""
        self.handle_legacy_remove(
            Message("mycroft.scheduler.remove_event", {"event": event}))

    def update_event(self, event: str, data: dict):
        """Change the data an existing event fires with."""
        self.handle_legacy_update(
            Message("mycroft.scheduler.update_event",
                    {"event": event, "data": data}))

    def check_state(self):
        """Fire every occurrence that is due."""
        self._evaluate()

    def store(self):
        """Write the schedule to disk."""
        with self.lock:
            self._persist()
