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
"""
The scheduler module allows setting up scheduled messages.

A scheduled message will be kept and not sent until the system clock time
criteria is met.
"""
import datetime
import json
import shutil
import time
from os.path import isfile, join, expanduser
from threading import Event
from threading import Thread, Lock
from typing import Optional

from ovos_config.config import Configuration
from ovos_config.locations import get_xdg_data_save_path, get_xdg_config_save_path
from ovos_utils.log import LOG
from ovos_utils.time import now_local
from ovos_bus_client.message import Message


def repeat_time(sched_time: float, repeat: float) -> float:
    """
    Next scheduled time for repeating event. Guarantees that the
    time is not in the past (but could skip interim events)

    Args:
        sched_time (float): Scheduled unix time for the event
        repeat (float):     Repeat period in seconds

    Returns: (float) time for next event
    """
    next_time = sched_time + repeat
    while next_time < time.time():
        # Schedule at an offset to assure no doubles
        next_time = time.time() + abs(repeat)
    return next_time


class EventScheduler(Thread):
    """
    Create an event scheduler thread. Will send messages at a
    predetermined time to the registered targets.

    Arguments:
        bus:            Mycroft messagebus client
        schedule_file:  filename used to store pending events to on
                        shutdown. File is created in XDG_DATA_HOME
        autostart: if True, start scheduler on init
    """

    def __init__(self, bus, schedule_file: str = 'schedule.json', autostart: bool = True):
        """
        Initialize the EventScheduler, load any existing schedule, register messagebus handlers, and optionally start the scheduler thread.
        
        Parameters:
            bus: MessageBus client used to emit and receive scheduler-related messages.
            schedule_file (str): Filename for persisting scheduled events; an existing legacy file in the old data directory will be migrated to the current config path.
            autostart (bool): If True, start the scheduler thread immediately; if False, initialize internal running/stopping state without starting the thread.
        """
        super().__init__()

        self.events = {}
        self.event_lock = Lock()

        # to check if its our first connection to the internet via clock_skew
        self._last_sync: datetime.datetime = now_local()
        self._dropped_events = 0
        self._past_date = datetime.datetime(day=1, month=12, year=2024, tzinfo=self._last_sync.tzinfo)
        LOG.debug(f"Boot time clock: {self._last_sync}")

        self.bus = bus

        core_conf = Configuration()
        data_dir = core_conf.get('data_dir') or get_xdg_data_save_path()
        old_schedule_path = join(expanduser(data_dir), schedule_file)

        self.schedule_file = join(get_xdg_config_save_path(), schedule_file)
        if isfile(old_schedule_path):
            shutil.move(old_schedule_path, self.schedule_file)

        if self.schedule_file:
            self.load()

        self.bus.on('mycroft.scheduler.schedule_event', self.handle_schedule_event)
        self.bus.on('mycroft.scheduler.remove_event', self.handle_remove_event)
        self.bus.on('mycroft.scheduler.update_event', self.handle_update_event)
        self.bus.on('mycroft.scheduler.get_event', self.handle_get_event)
        self.bus.on('mycroft.scheduler.list_events', self.handle_list_events)
        self.bus.on('system.clock.synced', self.handle_system_clock_sync)  # emitted by raspOVOS

        self._running = Event()
        self._stopping = Event()
        if autostart:
            self.start()
            self._running.wait(10)
        else:
            # Explicitly define event states
            self._stopping.set()
            self._running.clear()

    @property
    def is_running(self) -> bool:
        """
        Return True while scheduler is running
        """
        return not self._stopping.is_set()

    @is_running.setter
    def is_running(self, value: bool):
        if value is True:
            self._stopping.clear()
        elif value is False:
            self._stopping.set()

    def load(self):
        """
        Load json data with active events from json file.
        """
        if isfile(self.schedule_file):
            json_data = {}
            with open(self.schedule_file) as schedule_file:
                try:
                    json_data = json.load(schedule_file)
                except Exception as exc:
                    LOG.error(exc)
            current_time = time.time()
            with self.event_lock:
                for key in json_data:
                    event_list = json_data[key]
                    # discard non repeating events that has already happened
                    self.events[key] = [tuple(evt) for evt in event_list
                                        if evt[0] > current_time or evt[1]]

    def run(self):
        """
        Check events periodically until stopped
        """
        LOG.info("EventScheduler Started")
        self._stopping.clear()
        self._running.set()
        while not self._stopping.wait(0.5):
            try:
                self.check_state()
            except Exception as e:
                LOG.exception(e)
        LOG.info("EventScheduler Stopped")

    def check_state(self):
        """
        Check if any event should be triggered.
        """
        with self.event_lock:
            # Check all events
            pending_messages = []
            for event in self.events:
                current_time = time.time()
                e = self.events[event]
                # Get scheduled times that has passed
                passed = ((t, r, d, c) for
                          (t, r, d, c) in e if t <= current_time)
                # and remaining times that we're still waiting for
                remaining = [(t, r, d, c) for
                             t, r, d, c in e if t > current_time]
                # Trigger registered methods
                for sched_time, repeat, data, context in passed:
                    pending_messages.append(Message(event, data, context))
                    # if this is a repeated event add a new trigger time
                    if repeat:
                        next_time = repeat_time(sched_time, repeat)
                        remaining.append((next_time, repeat, data, context))
                # update list of events
                self.events[event] = remaining

        # Remove events that are now completed
        self.clear_empty()

        # Finally, emit the queued up events that triggered
        for msg in pending_messages:
            LOG.debug(f"Call scheduled event: {msg.msg_type}")
            self.bus.emit(msg)

    def schedule_event(self, event: str, sched_time: float,
                       repeat: Optional[float] = None,
                       data: Optional[dict] = None,
                       context: Optional[dict] = None):
        """
        Schedule a named event to be emitted at a specific epoch time, optionally repeating.
        
        Parameters:
            event (str): Event name/key to schedule.
            sched_time (float): Unix epoch time (seconds) when the event should be triggered.
            repeat (Optional[float]): If provided, interval in seconds for repeating the event.
            data (Optional[dict]): Payload to include with the emitted message.
            context (Optional[dict]): Message context to send when the event is emitted.
        
        Behavior:
            If the scheduler's stored clock indicates the system time is in the past, the event is not scheduled and an internal dropped-event counter is incremented. If a repeating event with the same name is already scheduled, the new repeating schedule is discarded. If sched_time is already past, the event is accepted but a warning is logged and the event will be triggered immediately.
        """
        if self._last_sync < self._past_date:
            # this works around problems in raspOVOS images and other
            # systems without RTC that didnt sync clock with the internet yet
            # eg. issue demonstration without this:
            #   date time skill schedulling the hour change sound N times (+1hour every time until present)
            LOG.error("Refusing to schedule event, system clock is in the past!")
            self._dropped_events += 1
            return
        
        data = data or {}
        with self.event_lock:
            # get current list of scheduled times for event, [] if missing
            event_list = self.events.get(event, [])

            # Don't schedule if the event is repeating and already scheduled
            if repeat and event in self.events:
                LOG.debug(f'Repeating event {event} is already scheduled, '
                          f'discarding')
            else:
                LOG.debug(f"Scheduled event: {event} for time {sched_time}")
                # add received event and time
                event_list.append((sched_time, repeat, data, context))
                self.events[event] = event_list
                if sched_time < time.time():
                    LOG.warning(f"Added event is scheduled in the past and "
                                f"will be called immediately: {event}")

    def handle_schedule_event(self, message: Message):
        """
        Handle an incoming schedule request message and forward it to schedule_event.
        
        Expects message.data to contain:
            event: (str) name of the event to schedule (required)
            time: (float) scheduled time as a Unix timestamp or timezone-aware timestamp (required)
            repeat: (float) repeat interval in seconds (optional)
            data: (dict) payload to attach to the scheduled event (optional)
        
        If both `event` and `time` are present this forwards the values (plus `repeat`, `data`, and message.context) to schedule_event; otherwise logs an error indicating the missing field.
        """
        event = message.data.get('event')
        sched_time = message.data.get('time')
        repeat = message.data.get('repeat')
        data = message.data.get('data')
        context = message.context
        if event and sched_time:
            self.schedule_event(event, sched_time, repeat, data, context)
        elif not event:
            LOG.error('Scheduled event msg_type not provided')
        else:
            LOG.error('Scheduled event time not provided')

    def remove_event(self, event: str):
        """
        Remove an event from the list of scheduled events.

        Arguments:
            event (str): event identifier
        """
        with self.event_lock:
            if event in self.events:
                self.events.pop(event)

    def handle_system_clock_sync(self, message: Message):
        # clock sync, are we in the past?
        """
        Handle a system clock synchronization notification and update the scheduler's last-sync timestamp.
        
        If the scheduler previously believed the clock to be in the past, a warning is logged indicating how many scheduled events were dropped. Updates self._last_sync to the current local time and logs the new sync time.
        
        Parameters:
            message (Message): Incoming bus message that triggered the clock synchronization.
        """
        if self._last_sync < self._past_date:
            LOG.warning(f"Clock was in the past!!! {self._dropped_events} scheduled events have been dropped")
        self._last_sync = now_local()
        LOG.info(f"clock sync: {self._last_sync}")

    def handle_remove_event(self, message: Message):
        """
        Handle an incoming message that requests removal of a scheduled event.
        
        Extracts the 'event' field from message.data and removes the named scheduled event if present.
        
        Parameters:
            message (Message): Messagebus message expected to contain an 'event' key in its data.
        """
        event = message.data.get('event')
        self.remove_event(event)

    def update_event(self, event: str, data: dict):
        """
        Update the data payload for the first scheduled occurrence of a named event.
        
        Only the first scheduled entry for the given event identifier is modified when multiple entries exist.
        
        Parameters:
            event (str): Event identifier whose first scheduled entry will be updated.
            data (dict): New data payload to replace the existing entry's data.
        """
        with self.event_lock:
            # if there is an active event with this name
            if len(self.events.get(event, [])) > 0:
                event_time, repeat, _, context = self.events[event][0]
                self.events[event][0] = (event_time, repeat, data, context)

    def handle_update_event(self, message: Message):
        """
        Handle a bus message that updates the payload for a scheduled event.
        
        Extracts the 'event' and 'data' fields from message.data and updates the stored data for the first scheduled occurrence of that event if it exists.
        """
        event = message.data.get('event')
        data = message.data.get('data')
        self.update_event(event, data)

    def handle_list_events(self, message: Message):
        """
        Emit a snapshot of all scheduled events in response to the incoming message.
        
        The snapshot is sent in the response data under the "scheduled_events" key.
        
        Parameters:
            message (Message): Incoming message whose response will receive the scheduled events snapshot.
        """
        with self.event_lock:
            events_snapshot = dict(self.events)
        self.bus.emit(message.response(data={"scheduled_events": events_snapshot}))

    def handle_get_event(self, message: Message):
        """
        Emit the current scheduled entries for the named event as a reply on the message bus.
        
        If the incoming message contains a "name" field, looks up the schedule for that event and emits a reply message to the emitter `mycroft.event_status.callback.{name}` with the event's data (or `None` if the event is not scheduled).
        """
        event_name = message.data.get("name")
        event = None
        with self.event_lock:
            if event_name in self.events:
                event = self.events[event_name]
        emitter_name = f'mycroft.event_status.callback.{event_name}'
        self.bus.emit(message.reply(emitter_name, data=event))

    def store(self):
        """
        Write current schedule to disk.
        """
        with self.event_lock:
            with open(self.schedule_file, 'w') as schedule_file:
                json.dump(self.events, schedule_file)

    def clear_repeating(self):
        """
        Remove repeating events from events dict.
        """
        with self.event_lock:
            for evt in self.events:
                self.events[evt] = [tup for tup in self.events[evt]
                                    if tup[1] is None]

    def clear_empty(self):
        """
        Remove empty event entries from events dict.
        """
        with self.event_lock:
            self.events = {k: self.events[k] for k in self.events
                           if self.events[k] != []}

    def shutdown(self):
        """
        Stop the running thread.
        """
        try:
            self._stopping.set()
            # Remove listeners
            self.bus.remove_all_listeners('mycroft.scheduler.get_event')
            self.bus.remove_all_listeners('mycroft.scheduler.list_events')
            self.bus.remove_all_listeners('mycroft.scheduler.remove_event')
            self.bus.remove_all_listeners('mycroft.scheduler.schedule_event')
            self.bus.remove_all_listeners('mycroft.scheduler.update_event')
            self.bus.remove_all_listeners('system.clock.synced')
            # Wait for thread to finish
            self.join(30)
            # Prune event list in preparation for saving
            self.clear_repeating()
            self.clear_empty()
            # Store all pending scheduled events
            self.store()
            self._running.clear()
        except Exception as e:
            self._running.clear()
            if not isinstance(e, OSError):
                self.store()
            raise e
