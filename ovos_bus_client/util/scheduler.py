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

import json
import shutil

import time

from typing import Optional, Callable, Union
from threading import Event
from datetime import datetime, timedelta
from os.path import isfile, join, expanduser
from threading import Thread, Lock

from ovos_config.config import Configuration
from ovos_config.locations import get_xdg_data_save_path, get_xdg_config_save_path
from ovos_utils.log import LOG, log_deprecation, deprecated
from ovos_utils.messagebus import FakeBus
from ovos_utils.events import create_basic_wrapper
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

    def __init__(self, bus,
                 schedule_file: str = 'schedule.json', autostart: bool = True):
        super().__init__()

        self.events = {}
        self.event_lock = Lock()

        self.bus = bus

        core_conf = Configuration()
        data_dir = core_conf.get('data_dir') or get_xdg_data_save_path()
        old_schedule_path = join(expanduser(data_dir), schedule_file)

        self.schedule_file = join(get_xdg_config_save_path(), schedule_file)
        if isfile(old_schedule_path):
            shutil.move(old_schedule_path, self.schedule_file)

        if self.schedule_file:
            self.load()

        self.bus.on('mycroft.scheduler.schedule_event',
                    self.schedule_event_handler)
        self.bus.on('mycroft.scheduler.remove_event',
                    self.remove_event_handler)
        self.bus.on('mycroft.scheduler.update_event',
                    self.update_event_handler)
        self.bus.on('mycroft.scheduler.get_event',
                    self.get_event_handler)
        if autostart:
            self.start()

        self._stopping = Event()

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
        Add event to pending event schedule.

        Arguments:
            event (str): Handler for the event
            sched_time (float): epoch time of event
            repeat ([type], optional): Defaults to None. [description]
            data ([type], optional): Defaults to None. [description]
            context (dict, optional): context (dict, optional): message
                                      context to send when the
                                      handler is called
        """
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

    def schedule_event_handler(self, message: Message):
        """
        Messagebus interface to the schedule_event method.
        Required data in the message envelope is
            event: event to emit
            time:  time to emit the event

        Optional data is
            repeat: repeat interval
            data:   data to send along with the event
        """
        event = message.data.get('event')
        sched_time = message.data.get('time')
        repeat = message.data.get('repeat')
        data = message.data.get('data')
        context = message.context
        if event and sched_time:
            self.schedule_event(event, sched_time, repeat, data, context)
        elif not event:
            LOG.error('Scheduled event name not provided')
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

    def remove_event_handler(self, message: Message):
        """
        Messagebus interface to the remove_event method.
        """
        event = message.data.get('event')
        self.remove_event(event)

    def update_event(self, event: str, data: dict):
        """
        Change an existing event's data.

        This will only update the first call if multiple calls are registered
        to the same event identifier.

        Arguments:
            event (str): event identifier
            data (dict): new data
        """
        with self.event_lock:
            # if there is an active event with this name
            if len(self.events.get(event, [])) > 0:
                event_time, repeat, _, context = self.events[event][0]
                self.events[event][0] = (event_time, repeat, data, context)

    def update_event_handler(self, message: Message):
        """
        Messagebus interface to the update_event method.
        """
        event = message.data.get('event')
        data = message.data.get('data')
        self.update_event(event, data)

    def get_event_handler(self, message: Message):
        """
        Messagebus interface to get_event.

        Emits another event sending event status.
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
        self._stopping.set()
        # Remove listeners
        self.bus.remove_all_listeners('mycroft.scheduler.schedule_event')
        self.bus.remove_all_listeners('mycroft.scheduler.remove_event')
        self.bus.remove_all_listeners('mycroft.scheduler.update_event')
        # Wait for thread to finish
        self.join()
        # Prune event list in preparation for saving
        self.clear_repeating()
        self.clear_empty()
        # Store all pending scheduled events
        self.store()


class EventContainer:
    """
    Container tracking messagbus handlers.

    This container tracks events added by a skill, allowing unregistering
    all events on shutdown.
    """

    def __init__(self, bus=None):
        self.bus = bus or FakeBus()
        self.events = []

    def set_bus(self, bus):
        self.bus = bus

    def add(self, name: str, handler: Callable, once: bool = False):
        """
        Create event handler for executing intent or other event.

        Arguments:
            name (string): IntentParser name
            handler (func): Method to call
            once (bool, optional): Event handler will be removed after it has
                                   been run once.
        """

        def once_wrapper(message):
            # Remove registered one-time handler before invoking,
            # allowing them to re-schedule themselves.
            self.remove(name)
            handler(message)

        if handler:
            if once:
                self.bus.once(name, once_wrapper)
                self.events.append((name, once_wrapper))
            else:
                self.bus.on(name, handler)
                self.events.append((name, handler))

            LOG.debug('Added event: {}'.format(name))

    def remove(self, name: str):
        """
        Removes an event from bus emitter and events list.

        Args:
            name (string): Name of Intent or Scheduler Event
        Returns:
            bool: True if found and removed, False if not found
        """
        LOG.debug("Removing event {}".format(name))
        removed = False
        for _name, _handler in list(self.events):
            if name == _name:
                try:
                    self.events.remove((_name, _handler))
                except ValueError:
                    LOG.error('Failed to remove event {}'.format(name))
                    pass
                removed = True

        # Because of function wrappers, the emitter doesn't always directly
        # hold the _handler function, it sometimes holds something like
        # 'wrapper(_handler)'.  So a call like:
        #     self.bus.remove(_name, _handler)
        # will not find it, leaving an event handler with that name left behind
        # waiting to fire if it is ever re-installed and triggered.
        # Remove all handlers with the given name, regardless of handler.
        if removed:
            self.bus.remove_all_listeners(name)
        return removed

    def __iter__(self):
        return iter(self.events)

    def clear(self):
        """
        Unregister all registered handlers and clear the list of registered
        events.
        """
        for e, f in self.events:
            self.bus.remove(e, f)
        self.events = []  # Remove reference to wrappers


class EventSchedulerInterface:
    """
    Interface for accessing the event scheduler over the message bus.
    """

    def __init__(self, name=None, sched_id=None, bus=None, skill_id=None):
        # NOTE: can not rename or move sched_id/name arguments to keep api compatibility
        if name:
            log_deprecation("name argument has been deprecated! "
                            "use skill_id instead", "0.1.0")
        if sched_id:
            log_deprecation("sched_id argument has been deprecated! "
                            "use skill_id instead", "0.1.0")

        self.skill_id = skill_id or sched_id or name or self.__class__.__name__
        self.bus = bus
        self.events = EventContainer(bus)
        self.scheduled_repeats = []

    def set_bus(self, bus):
        """
        Attach the messagebus of the parent skill

        Args:
            bus (MessageBusClient): websocket connection to the messagebus
        """
        self.bus = bus
        self.events.set_bus(bus)

    def set_id(self, sched_id: str):
        """
        Attach the skill_id of the parent skill

        Args:
            sched_id (str): skill_id of the parent skill
        """
        # NOTE: can not rename sched_id kwarg to keep api compatibility
        self.skill_id = sched_id

    def _create_unique_name(self, name: str) -> str:
        """
        Return a name unique to this skill using the format
        [skill_id]:[name].

        Args:
            name:   Name to use internally

        Returns:
            str: name unique to this skill
        """
        return self.skill_id + ':' + (name or '')

    def _schedule_event(self, handler: Callable, when: datetime,
                        data: dict, name: str,
                        repeat_interval: Optional[float] = None,
                        context: Optional[dict] = None):
        """Underlying method for schedule_event and schedule_repeating_event.

        Takes scheduling information and sends it off on the message bus.

        Args:
            handler:                method to be called
            when (datetime):        time (in system timezone) for first
                                    calling the handler, or None to
                                    initially trigger <frequency> seconds
                                    from now
            data (dict, optional):  data to send when the handler is called
            name (str, optional):   reference name, must be unique
            repeat_interval (float/int):  time in seconds between calls
            context (dict, optional): message context to send
                                      when the handler is called
        """
        if isinstance(when, (int, float)) and when >= 0:
            when = datetime.now() + timedelta(seconds=when)
        if not name:
            name = self.skill_id + handler.__name__
        unique_name = self._create_unique_name(name)
        if repeat_interval:
            self.scheduled_repeats.append(name)  # store "friendly name"

        data = data or {}

        def on_error(e):
            LOG.exception(f'An error occurred executing the scheduled event: '
                          f'{e}')

        wrapped = create_basic_wrapper(handler, on_error)
        self.events.add(unique_name, wrapped, once=not repeat_interval)
        event_data = {'time': when.timestamp(),  # Epoch timestamp
                      'event': unique_name,
                      'repeat': repeat_interval,
                      'data': data}
        context = context or {}
        context["skill_id"] = self.skill_id
        self.bus.emit(Message('mycroft.scheduler.schedule_event',
                              data=event_data, context=context))

    def schedule_event(self, handler: Callable,
                       when: datetime,
                       data: Optional[dict] = None,
                       name: Optional[str] = None,
                       context: Optional[dict] = None):
        """
        Schedule a single-shot event.

        Args:
            handler:               method to be called
            when (datetime/int/float):   datetime (in system timezone) or
                                   number of seconds in the future when the
                                   handler should be called
            data (dict, optional): data to send when the handler is called
            name (str, optional):  reference name
                                   NOTE: This will not warn or replace a
                                   previously scheduled event of the same
                                   name.
            context (dict, optional): message context to send
                                      when the handler is called
        """
        self._schedule_event(handler, when, data, name, context=context)

    def schedule_repeating_event(self, handler: Callable,
                                 when: Optional[datetime],
                                 interval: Union[float, int],
                                 data: Optional[dict] = None,
                                 name: Optional[str] = None,
                                 context: Optional[dict] = None):
        """
        Schedule a repeating event.

        Args:
            handler:                method to be called
            when (datetime):        time (in system timezone) for first
                                    calling the handler, or None to
                                    initially trigger <frequency> seconds
                                    from now
            interval (float/int):   time in seconds between calls
            data (dict, optional):  data to send when the handler is called
            name (str, optional):   reference name, must be unique
            context (dict, optional): message context to send
                                      when the handler is called
        """
        # Do not schedule if this event is already scheduled by the skill
        if name not in self.scheduled_repeats:
            # If only interval is given set to trigger in [interval] seconds
            # from now.
            if not when:
                when = datetime.now() + timedelta(seconds=interval)
            self._schedule_event(handler, when, data, name, interval,
                                 context=context)
        else:
            LOG.debug('The event is already scheduled, cancel previous '
                      'event if this scheduling should replace the last.')

    def update_scheduled_event(self, name: str, data: Optional[dict] = None):
        """
        Change data of event.

        Args:
            name (str): reference name of event (from original scheduling)
            data (dict): new data to update event with
        """
        data = data or {}
        data = {
            'event': self._create_unique_name(name),
            'data': data
        }
        self.bus.emit(Message('mycroft.schedule.update_event',
                              data=data, context={"skill_id": self.skill_id}))

    def cancel_scheduled_event(self, name: str):
        """
        Cancel a pending event. The event will no longer be scheduled.

        Args:
            name (str): reference name of event (from original scheduling)
        """
        unique_name = self._create_unique_name(name)
        data = {'event': unique_name}
        if name in self.scheduled_repeats:
            self.scheduled_repeats.remove(name)
        if self.events.remove(unique_name):
            self.bus.emit(Message('mycroft.scheduler.remove_event',
                                  data=data,
                                  context={"skill_id": self.skill_id}))

    def get_scheduled_event_status(self, name: str) -> int:
        """
        Get scheduled event data and return the amount of time left

        Args:
            name (str): reference name of event (from original scheduling)

        Returns:
            int: the time left in seconds

        Raises:
            Exception: Raised if event is not found
        """
        event_name = self._create_unique_name(name)
        data = {'name': event_name}

        reply_name = f'mycroft.event_status.callback.{event_name}'
        msg = Message('mycroft.scheduler.get_event', data=data,
                      context={"skill_id": self.skill_id})
        status = self.bus.wait_for_response(msg, reply_type=reply_name)

        if status:
            event_time = int(status.data[0][0])
            current_time = int(time.time())
            time_left_in_seconds = event_time - current_time
            LOG.info(time_left_in_seconds)
            return time_left_in_seconds
        else:
            raise Exception("Event Status Messagebus Timeout")

    def cancel_all_repeating_events(self):
        """
        Cancel any repeating events started by the skill.
        """
        # NOTE: Gotta make a copy of the list due to the removes that happen
        #       in cancel_scheduled_event().
        for e in list(self.scheduled_repeats):
            self.cancel_scheduled_event(e)

    def shutdown(self):
        """
        Shutdown the interface unregistering any event handlers.
        """
        self.cancel_all_repeating_events()
        self.events.clear()

    @property
    @deprecated("self.sched_id has been deprecated! use self.skill_id instead",
                "0.1.0")
    def sched_id(self):
        """DEPRECATED: do not use, method only for api backwards compatibility
        Logs a warning and returns self.skill_id
        """
        return self.skill_id

    @sched_id.setter
    @deprecated("self.sched_id has been deprecated! use self.skill_id instead",
                "0.1.0")
    def sched_id(self, skill_id):
        """DEPRECATED: do not use, method only for api backwards compatibility
        Logs a warning and sets self.skill_id
        """
        self.skill_id = skill_id

    @property
    @deprecated("self.name has been deprecated! use self.skill_id instead",
                "0.1.0")
    def name(self):
        """DEPRECATED: do not use, method only for api backwards compatibility
        Logs a warning and returns self.skill_id
        """
        return self.skill_id

    @name.setter
    @deprecated("self.name has been deprecated! use self.skill_id instead",
                "0.1.0")
    def name(self, skill_id):
        """DEPRECATED: do not use, method only for api backwards compatibility
        Logs a warning and sets self.skill_id
        """
        self.skill_id = skill_id

