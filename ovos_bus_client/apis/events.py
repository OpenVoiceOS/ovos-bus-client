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
        """
        Initialize the scheduler interface with an optional message bus and skill identifier.
        
        Parameters:
        	bus: Message bus client used to send and receive scheduler messages; may be None.
        	skill_id (str): Identifier for the skill. Defaults to the class name lowercased.
        
        """
        self.skill_id = skill_id or self.__class__.__name__.lower()
        self.bus = bus
        self.events = EventContainer(bus)
        self.scheduled_repeats = []

    def set_bus(self, bus):
        """
        Attach the message bus client to this interface and propagate it to the internal EventContainer.
        
        Also sets the interface's `bus` attribute to the provided client.
        """
        self.bus = bus
        self.events.set_bus(bus)

    def set_id(self, skill_id: str):
        """
        Set the interface's skill identifier used as the event name prefix.
        
        Parameters:
            skill_id (str): Identifier for the parent skill to use when namespacing scheduled events.
        """
        self.skill_id = skill_id

    def _get_source_message(self):
        """
        Retrieve the current source Message and ensure its context contains this interface's skill_id.
        
        Returns:
            Message: The source Message from the current context, or a new empty Message if none was found. The returned message's `context['skill_id']` is set to this interface's skill_id.
        """
        message = dig_for_message() or Message("")
        message.context['skill_id'] = self.skill_id
        return message

    def _create_unique_name(self, name: str) -> str:
        """
        Build a skill-scoped unique name by prefixing the provided name with the skill ID and a colon.
        
        Parameters:
            name (str): Friendly event name; if falsy (e.g., empty or None), an empty suffix is used after the colon.
        
        Returns:
            str: The unique name in the format "<skill_id>:<name>" (or "<skill_id>:" when `name` is falsy).
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
                        Schedule a handler to run at a specific time (or after a delay) and optionally repeat, then announce the event on the message bus.
                        
                        Schedule the provided handler to be invoked at `when` (a timezone-aware datetime or a seconds offset from now) and register the event with the internal EventContainer. If `repeat_interval` is provided the event will be treated as repeating. After registration an event message is emitted to 'mycroft.scheduler.schedule_event' containing the event metadata.
                        
                        Parameters:
                            handler (Callable[..., None]): Function to call when the event fires.
                            when (datetime | int | float): Absolute time (datetime) or seconds from now (int/float) for the first invocation.
                            data (Optional[dict]): Payload passed to the handler when the event fires.
                            name (Optional[str]): Friendly event name; if falsy a default is derived from the skill id and handler name.
                            repeat_interval (Optional[float | int]): Interval in seconds between repeats; omit or None for a single-shot event.
                            context (Optional[dict]): Message context to send with the scheduler bus message; skill_id will be ensured in the context.
                        
                        Raises:
                            ValueError: If `when` is a negative numeric offset.
                            TypeError: If `when` is not a datetime, int, or float.
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
            """
            Log an exception raised during execution of a scheduled event.
            
            Parameters:
                e (Exception): The exception that was raised.
            """
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
                       Schedule a one-time event to invoke the given handler at a specified time or after a delay.
                       
                       Parameters:
                           handler (Callable[..., None]): Function to call when the event fires; it will receive the event data.
                           when (datetime | int | float): Absolute time (timezone-aware datetime) or a number of seconds from now.
                           data (dict, optional): Payload passed to the handler when invoked. Defaults to empty dict if None.
                           name (str, optional): Friendly event name; if omitted a name derived from the skill ID and handler will be used.
                           context (dict, optional): Message context to associate with the scheduled event; skill identity will be ensured.
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
                                 Schedule a repeating event that invokes `handler` at a start time and then repeatedly at a fixed interval.
                                 
                                 Parameters:
                                 	handler (Callable[..., None]): Function to call for each occurrence.
                                 	when (datetime | int | float | None): Absolute start time (timezone-aware or naive — default tz applied), or a number of seconds from now. If `None`, the first call is scheduled interval seconds from now.
                                 	interval (float | int): Seconds between consecutive calls.
                                 	data (dict, optional): Payload passed to the handler when the event fires.
                                 	name (str, optional): Friendly event name; defaults to `<skill_id><handler.__name__>`. Name must be unique per skill; if an event with the same name is already scheduled, this call is ignored.
                                 	context (dict, optional): Message/context dictionary forwarded with the scheduled event.
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
        Update the payload data for a scheduled event identified by `name`.
        
        Parameters:
            name (str): Friendly reference name of the scheduled event to update.
            data (dict, optional): New event data to replace the existing payload; defaults to an empty dict.
        """
        data = {
            'event': self._create_unique_name(name),
            'data': data or {}
        }
        message = self._get_source_message()
        self.bus.emit(message.forward('mycroft.schedule.update_event', data))

    def cancel_scheduled_event(self, name: str):
        """
        Cancel a scheduled event identified by its friendly name.
        
        Parameters:
            name (str): Friendly reference name used when the event was scheduled.
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
        Get remaining seconds until a scheduled event triggers.
        
        Parameters:
            name (str): Friendly reference name used when the event was scheduled.
        
        Returns:
            int: Remaining time in seconds until the scheduled event; may be negative if the event time is already past.
        
        Raises:
            Exception: If no status response is received (messagebus timeout).
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
        Cancel all repeating events that were scheduled by this skill.
        
        Iterates over the internally tracked repeating event names and cancels each one.
        """
        # NOTE: Gotta make a copy of the list due to the removes that happen
        #       in cancel_scheduled_event().
        for e in list(self.scheduled_repeats):
            self.cancel_scheduled_event(e)

    def shutdown(self):
        """
        Shut down the scheduler by cancelling all repeating events and clearing registered events.
        
        This stops any tracked repeating schedules and removes all event handlers from the internal EventContainer.
        """
        self.cancel_all_repeating_events()
        self.events.clear()