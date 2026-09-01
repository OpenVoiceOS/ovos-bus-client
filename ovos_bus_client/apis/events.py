import time
import warnings
from datetime import datetime, timedelta
from typing import Callable, Optional, Union
from zoneinfo import ZoneInfo

from ovos_utils.events import EventContainer, create_basic_wrapper
from ovos_bus_client.message import Message, dig_for_message
from ovos_utils.log import LOG
from ovos_config.locale import get_config_tz
from ovos_utils.time import now_local

from ovos_bus_client.version import VERSION_MAJOR

#: the legacy ``mycroft.scheduler.*`` wrappers go with the wire topics
LEGACY_REMOVAL_VERSION = f"{VERSION_MAJOR + 1}.0.0"


class SchedulerError(RuntimeError):
    """A scheduler request the service refused, or did not answer."""

    def __init__(self, error: str, reason: str):
        super().__init__(f"{error}: {reason}")
        self.error = error
        self.reason = reason


def _legacy_notice(method: str, replacement: str):
    warnings.warn(
        f"EventSchedulerInterface.{method} speaks the pre-specification "
        f"mycroft.scheduler.* protocol; use {replacement} instead. It will "
        f"be removed in ovos-bus-client {LEGACY_REMOVAL_VERSION}.",
        DeprecationWarning, stacklevel=3)


class EventSchedulerInterface:
    """Interface for accessing the event scheduler over the message bus.

    The SCHEDULER-1 methods :meth:`schedule`, :meth:`cancel`, :meth:`get`
    and :meth:`list` send a request and wait for its answer, raising
    :class:`SchedulerError` when the scheduler refuses. The older
    ``*_scheduled_event`` methods speak the legacy protocol and are kept
    for one stable cycle.
    """

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
            when = when.replace(tzinfo=get_config_tz())
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
        _legacy_notice("schedule_event", "schedule()")
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
        _legacy_notice("schedule_repeating_event", "schedule()")
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
        _legacy_notice("update_scheduled_event", "schedule()")
        data = {
            'event': self._create_unique_name(name),
            'data': data or {}
        }
        message = self._get_source_message()
        self.bus.emit(message.forward('mycroft.scheduler.update_event', data))

    def cancel_scheduled_event(self, name: str):
        """
        Cancel a pending event. The event will no longer be scheduled.

        Args:
            name (str): reference name of event (from original scheduling)
        """
        _legacy_notice("cancel_scheduled_event", "cancel()")
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
        _legacy_notice("get_scheduled_event_status", "get()")
        event_name = self._create_unique_name(name)
        data = {'name': event_name}

        reply_name = f'mycroft.event_status.callback.{event_name}'
        message = self._get_source_message()
        msg = message.forward('mycroft.scheduler.get_event', data)
        status = self.bus.wait_for_response(msg, reply_type=reply_name)

        if status and status.data.get("schedule"):
            event_time = int(status.data["schedule"][0])
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

    # --- SCHEDULER-1 -------------------------------------------------------

    def _request(self, topic: str, data: dict, timeout: float = 3.0) -> dict:
        """Send a request and return the answer, raising when refused."""
        message = self._get_source_message()
        response = self.bus.wait_for_response(
            message.forward(topic, dict(data, owner=self.skill_id)),
            reply_type=f"{topic}.response", timeout=timeout)
        if response is None:
            raise SchedulerError("timeout", f"no answer to {topic}")
        if not response.data.get("ok"):
            raise SchedulerError(response.data.get("error", "unknown"),
                                 response.data.get("reason", ""))
        return response.data

    def _default_id(self, event: str) -> str:
        """The id of the one schedule this component keeps for ``event``.

        It comes from the event name alone, never from the timing, so
        scheduling the same event again replaces the previous schedule
        rather than orphaning it. Pass ``schedule_id`` to keep several
        schedules for one event.
        """
        return event[len(self.skill_id) + 1:] or "schedule"

    def schedule(self, event: str, handler: Optional[Callable[..., None]] = None,
                 at: Optional[datetime] = None,
                 every: Optional[dict] = None,
                 local: Optional[dict] = None,
                 in_seconds: Optional[float] = None,
                 data: Optional[dict] = None,
                 schedule_id: Optional[str] = None,
                 zone: Optional[str] = None,
                 **options) -> str:
        """Create or replace a schedule and return its id.

        ``event`` is namespaced to this component when it is not already.
        ``at`` must be a time-zone-aware datetime unless ``zone`` names the
        zone to read it in. ``handler`` is subscribed before the request
        goes out, so an occurrence due immediately is not lost.

        Without ``schedule_id`` the component keeps one schedule per
        event, and calling this again for the same event replaces it. Give
        distinct ids when one event needs several schedules.
        """
        if not event.startswith(f"{self.skill_id}."):
            event = f"{self.skill_id}.{event}"
        timing = {}
        if at is not None:
            if not isinstance(at, datetime):
                raise TypeError(f"at must be a datetime, got {at!r}")
            if at.tzinfo is None:
                if zone is None:
                    raise ValueError("at is a naive datetime and no zone was "
                                     "given; pass an aware datetime or zone=")
                at = at.replace(tzinfo=ZoneInfo(zone))
            timing["at"] = at.isoformat()
        if every is not None:
            timing["every"] = every
        if local is not None:
            timing["local"] = local
        if in_seconds is not None:
            timing["in"] = {"seconds": in_seconds}
        if len(timing) != 1:
            raise ValueError("exactly one of at, in_seconds, every, local "
                             "is required")

        schedule_id = schedule_id or self._default_id(event)
        if handler is not None:
            wrapped = create_basic_wrapper(
                handler, lambda e: LOG.exception(
                    f"error in scheduled event handler: {e}"))
            self.events.add(event, wrapped,
                            once=at is not None or in_seconds is not None)

        request = {"id": schedule_id, "event": event, "data": data or {}}
        request.update(timing)
        request.update(options)
        self._request("scheduler.schedule", request)
        return schedule_id

    def cancel(self, schedule_id: str) -> bool:
        """Delete a schedule. Returns whether it existed."""
        existed = self._request("scheduler.cancel",
                                {"id": schedule_id})["existed"]
        return existed

    def get(self, schedule_id: str) -> Optional[dict]:
        """Read one of this component's schedules as ``record`` plus
        computed ``state``, or None when it does not exist."""
        answer = self._request("scheduler.get", {"id": schedule_id})
        if not answer["existed"]:
            return None
        return {"record": answer["record"], "state": answer["state"]}

    def list(self) -> list:
        """Read every schedule this component owns."""
        return self._request("scheduler.list", {})["schedules"]
