import time
from datetime import datetime, timedelta
from typing import Callable, Optional, Union

from ovos_utils.events import EventContainer, create_basic_wrapper
from ovos_bus_client.message import Message, dig_for_message
from ovos_utils.log import LOG
from ovos_config.locale import get_default_tz
from ovos_utils.time import now_local


class EventSchedulerInterface:
    """Interface for accessing the event scheduler over the message bus."""

    def __init__(self, bus=None, skill_id=None):
        self.skill_id = skill_id or self.__class__.__name__.lower()
        self.bus = bus
        self.events = EventContainer(bus)
        self.scheduled_repeats = []

    def set_bus(self, bus):
        """Attach the messagebus of the parent skill

        Args:
            bus (MessageBusClient): websocket connection to the messagebus
        """
        self.bus = bus
        self.events.set_bus(bus)

    def set_id(self, skill_id: str):
        """
        Attach the skill_id of the parent skill

        Args:
            skill_id (str): skill_id of the parent skill
        """
        self.skill_id = skill_id

    def _get_source_message(self):
        message = dig_for_message() or Message("")
        message.context['skill_id'] = self.skill_id
        return message

    def _create_unique_name(self, name: str) -> str:
        """
        Return a name unique to this skill using the format [skill_id]:[name].
        @param name: Name to use internally
        @return name unique to this skill
        """
        # TODO: Is a null name valid or should it raise an exception?
        return self.skill_id + ':' + (name or '')

    def _schedule_event(self, handler: Callable[..., None],
                        when: Union[datetime, int, float],
                        data: Optional[dict],
                        name: Optional[str],
                        repeat_interval: Optional[Union[float, int]] = None,
                        context: Optional[dict] = None):
        """
        Underlying method for schedule_event and schedule_repeating_event.
        Takes scheduling information and sends it off on the message bus.
        @param handler: method to be called at the scheduled time(s)
        @param when: time (tzaware or default to system tz) or delta seconds to
            first call the handler
        @param data: Message data to send to `handler
        @param name: Event name, must be unique in the context of this object
        @param repeat_interval:  time in seconds between calls
        @param context: Message context to send to `handler`

        """
        if isinstance(when, (int, float)):
            if when < 0:
                raise ValueError(f"Expected datetime or positive int/float. "
                                 f"got: {when}")
            when = now_local() + timedelta(seconds=when)
        if not isinstance(when, datetime):
            raise TypeError(f"Expected datetime, int, or float but got: {when}")
        if when.tzinfo is None:
            # ensure correct timezone before conversion to unix timestamp
            # naive datetime objects method relies on the platform C mktime() function to perform the conversion
            # and may not match mycroft.conf
            when = when.replace(tzinfo=get_default_tz())
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

        message = self._get_source_message()
        context = context or message.context
        context["skill_id"] = self.skill_id
        self.bus.emit(Message('mycroft.scheduler.schedule_event',
                              data=event_data, context=context))

    def schedule_event(self, handler: Callable[..., None],
                       when: Union[datetime, int, float],
                       data: Optional[dict] = None,
                       name: Optional[str] = None,
                       context: Optional[dict] = None):
        """
        Schedule a single-shot event.
        @param handler: method to be called at the scheduled time(s)
        @param when: time (tzaware or default to system tz) or delta seconds
            to first call the handler
        @param data: Message data to send to `handler
        @param name: Event name, must be unique in the context of this object
        @param context: Message context to send to `handler`
        """
        self._schedule_event(handler, when, data, name, context=context)

    def schedule_repeating_event(self,
                                 handler: Callable[..., None],
                                 when: Optional[Union[datetime, int, float]],
                                 interval: Union[float, int],
                                 data: Optional[dict] = None,
                                 name: Optional[str] = None,
                                 context: Optional[dict] = None):
        """
        Schedule a repeating event.
        @param handler: method to be called at the scheduled time(s)
        @param when: time (tzaware or default to system tz) or delta seconds to
            first call the handler. If None, first call is in `repeat_interval`
        @param data: Message data to send to `handler
        @param name: Event name, must be unique in the context of this object
        @param interval:  time in seconds between calls
        @param context: Message context to send to `handler`
        """
        # Ensure name is defined to avoid re-scheduling
        name = name or self.skill_id + handler.__name__

        # Do not schedule if this event is already scheduled by the skill
        if name not in self.scheduled_repeats:
            # If only interval is given set to trigger in [interval] seconds
            # from now.
            if not when:
                when = now_local() + timedelta(seconds=interval)
            self._schedule_event(handler, when, data, name, interval, context)
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
        data = {
            'event': self._create_unique_name(name),
            'data': data or {}
        }
        message = self._get_source_message()
        self.bus.emit(message.forward('mycroft.schedule.update_event', data))

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
            message = self._get_source_message()
            self.bus.emit(message.forward('mycroft.scheduler.remove_event', data))

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
        message = self._get_source_message()
        msg = message.forward('mycroft.scheduler.get_event', data)
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
