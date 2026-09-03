"""The component-facing half of the scheduler protocol (SCHEDULER-1 §8).

A component creates a schedule, subscribes to the event it fires, and later
changes, cancels or reads it back. The client owns the subscription:
cancelling a schedule removes the handler it registered, and re-scheduling
the same id replaces that handler rather than leaving a second one behind.

``schedule`` says what a schedule is from scratch; ``reschedule`` changes one
part of an existing schedule and leaves the rest, including its phase, where
it was.
"""
from datetime import datetime
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from ovos_utils.events import EventContainer, create_basic_wrapper
from ovos_utils.log import LOG

from ovos_bus_client.message import Message, dig_for_message
from ovos_bus_client.util.scheduled_events import topics

#: seconds to wait for the scheduler's answer to a request
DEFAULT_TIMEOUT = 3.0

#: seconds to wait when only asking whether a scheduler is there at all
PRESENCE_TIMEOUT = 0.5


class SchedulerError(RuntimeError):
    """A scheduler request that was refused, or that went unanswered.

    ``error`` is the wire code of SCHEDULER-1 §4, or ``"timeout"`` when no
    answer arrived.
    """

    def __init__(self, error: str, reason: str):
        super().__init__(f"{error}: {reason}")
        self.error = error
        self.reason = reason


class SchedulerClient:
    """Creates, cancels and reads this component's schedules.

    Every method sends one request and waits for its answer, raising
    :class:`SchedulerError` when the scheduler refuses. The component id is
    the owner of everything the client creates, so a client can neither see
    nor touch another component's schedules.

    ``is_available`` asks whether there is a scheduler to talk to at all,
    which a component running against an older core needs to know before it
    spends a timeout finding out the hard way.
    """

    def __init__(self, bus=None, skill_id: Optional[str] = None):
        self.skill_id = skill_id or self.__class__.__name__.lower()
        self.bus = bus
        self.events = EventContainer(bus)
        #: the event each schedule this client created is subscribed on, so
        #: that cancelling or replacing one can take its handler with it
        self._handled_events = {}
        self._presence = None

    def set_bus(self, bus):
        """Attach the message bus of the parent component."""
        self.bus = bus
        self.events.set_bus(bus)

    def set_id(self, skill_id: str):
        """Attach the component id of the parent component."""
        self.skill_id = skill_id

    def is_available(self, timeout: float = PRESENCE_TIMEOUT) -> bool:
        """Whether a scheduler is answering on this bus.

        A component can be running against a core older than the protocol,
        where every request would cost a full timeout and then raise. Asking
        first costs one short request, and the answer is remembered: a
        scheduler that is there when a component starts does not go away and
        leave the component talking to nothing.

        A refusal counts as present — something answered.
        """
        if self._presence is None:
            try:
                self._request(topics.SCHEDULER_LIST, {}, timeout=timeout)
                self._presence = True
            except SchedulerError as refusal:
                self._presence = refusal.error != "timeout"
        return self._presence

    def schedule(self, event: str, handler: Optional[Callable[..., None]] = None,
                 at: Optional[datetime] = None,
                 in_seconds: Optional[float] = None,
                 every: Optional[dict] = None,
                 local: Optional[dict] = None,
                 data: Optional[dict] = None,
                 schedule_id: Optional[str] = None,
                 zone: Optional[str] = None,
                 until: Optional[datetime] = None,
                 count: Optional[int] = None,
                 misfire: str = "late",
                 grace_s: Optional[float] = None,
                 ephemeral: bool = False,
                 context: Optional[dict] = None) -> str:
        """Create or replace a schedule and return its id.

        Give exactly one timing: ``at`` a datetime, ``in_seconds`` from now,
        ``every`` a fixed period, or ``local`` a wall-clock rule. ``at`` and
        ``until`` must be time-zone-aware datetimes unless ``zone`` names the
        IANA zone to read them in; the process's own zone is never assumed.

        ``handler`` is subscribed to ``event`` before the request goes out, so
        an occurrence due immediately is not lost. When the same
        ``schedule_id`` is scheduled again with a handler, the previous
        handler is dropped instead of accumulating.

        Without ``schedule_id`` the component keeps one schedule per event,
        derived from the event name and never from the handler, so scheduling
        the same event again replaces it. A handler renamed while the id is
        derived from the handler's own name would orphan the old schedule,
        which is why it is not.

        The occurrence fires with ``context``, or with the context of the
        message being handled when none is given. It is stored with the
        schedule and replayed on every fire (§3.5), so it survives a restart
        along with it. It travels as the request message's context, never in
        the request body, which §8 forbids.

        ``misfire`` decides what happens to occurrences that came due while
        the scheduler was down: ``"late"`` fires the most recent one,
        ``"skip"`` fires none, ``"all"`` fires every one. ``grace_s`` is how
        late a fire may be before it counts as missed. ``ephemeral``
        schedules are held in memory and die with the scheduler.
        """
        event = self._namespaced(event)
        timing = self._timing(at=at, in_seconds=in_seconds, every=every,
                              local=local, zone=zone)
        schedule_id = schedule_id or self._default_id(event)

        if handler is not None:
            self._take_over_handler(schedule_id, event, handler,
                                    once=("at" in timing or "in" in timing))

        request = {"id": schedule_id, "event": event, "data": data or {},
                   "misfire": misfire, "ephemeral": ephemeral}
        request.update(timing)
        if grace_s is not None:
            request["grace_s"] = grace_s
        if until is not None:
            request["until"] = self._instant(until, zone, "until")
        if count is not None:
            request["count"] = count

        self._request(topics.SCHEDULER_SCHEDULE, request, context=context)
        return schedule_id

    def reschedule(self, schedule_id: str, **changes) -> str:
        """Change one of this component's schedules, keeping the rest of it.

        Any keyword :meth:`schedule` takes may be given; what is not given is
        read back from the stored schedule. Changing only the payload leaves
        the schedule's phase alone — a period keeps the anchor it was created
        with (§3.4.1), and a one-shot keeps the instant it was already
        counting down to rather than starting the delay over.

        Replacing a schedule is how the protocol changes one: a request whose
        identity matches a stored schedule replaces it (§5.2). There is no
        separate update on the wire, and this is that replacement, filled in
        from what is already there.

        The handler stays subscribed unless a new one is given.
        """
        stored = self.get(schedule_id)
        if stored is None:
            raise SchedulerError("not_found",
                                 f"no schedule {schedule_id} to reschedule")
        arguments = self._as_arguments(stored["record"], stored["state"])
        if any(field in changes for field in ("at", "in_seconds", "every", "local")):
            for field in ("at", "in_seconds", "every", "local"):
                arguments.pop(field, None)
        arguments.update(changes)
        return self.schedule(**arguments)

    def _as_arguments(self, record: dict, state: dict) -> dict:
        """A stored schedule as the arguments that would create it again."""
        arguments = {"event": record["event"], "schedule_id": record["id"],
                     "data": record["data"], "misfire": record["misfire"],
                     "grace_s": record["grace_s"],
                     "ephemeral": record["ephemeral"],
                     "context": record.get("context", {})}
        arguments.update(self._kept_timing(record, state))
        if "until" in record:
            arguments["until"] = datetime.fromisoformat(record["until"])
        if "count" in record:
            arguments["count"] = record["count"]
        return arguments

    @staticmethod
    def _kept_timing(record: dict, state: dict) -> dict:
        """The stored timing, in the form that keeps the schedule's phase.

        A recurrence goes back unchanged, anchor and all. A one-shot goes
        back as the instant it is still waiting for, which is the same
        instant whether it was asked for as a time or as a delay.
        """
        if "every" in record:
            return {"every": record["every"]}
        if "local" in record:
            return {"local": record["local"]}
        upcoming = state["next"] or record.get("at")
        if upcoming is None:
            raise SchedulerError("bad_instant",
                                 f"{record['id']} has no occurrence left to "
                                 f"keep; give a new timing")
        return {"at": datetime.fromisoformat(upcoming)}

    def cancel(self, schedule_id: str) -> bool:
        """Delete a schedule and drop the handler it registered.

        Returns whether the schedule existed; cancelling one that does not is
        not an error.
        """
        self._release_handler(schedule_id)
        answer = self._request(topics.SCHEDULER_CANCEL, {"id": schedule_id})
        return answer["existed"]

    def get(self, schedule_id: str) -> Optional[dict]:
        """One of this component's schedules as ``record`` and ``state``, or
        None when it does not exist."""
        answer = self._request(topics.SCHEDULER_GET, {"id": schedule_id})
        if not answer["existed"]:
            return None
        return {"record": answer["record"], "state": answer["state"]}

    def list(self) -> list:
        """Every schedule this component owns, as ``record`` and ``state``.

        A component reconciles its own state against this after it starts;
        schedules outlive the process that created them.
        """
        return self._request(topics.SCHEDULER_LIST, {})["schedules"]

    # --- request plumbing -------------------------------------------------

    def _request(self, topic: str, data: dict,
                 timeout: float = DEFAULT_TIMEOUT,
                 context: Optional[dict] = None) -> dict:
        message = self._get_source_message(context).forward(
            topic, dict(data, owner=self.skill_id))
        answer = self.bus.wait_for_response(message,
                                            reply_type=f"{topic}.response",
                                            timeout=timeout)
        if answer is None:
            raise SchedulerError("timeout", f"no answer to {topic}")
        if not answer.data.get("ok"):
            raise SchedulerError(answer.data.get("error", "unknown"),
                                 answer.data.get("reason", ""))
        return answer.data

    def _get_source_message(self,
                            context: Optional[dict] = None) -> Message:
        """A message to derive the request from, carrying this component's id.

        Its context is the caller's if one was given, else the context of the
        message being handled. The scheduler reads ``skill_id`` from it as
        the caller's identity, and keeps the rest to fire the occurrence
        with.

        The dug message is not stamped in place: that would leave our
        ``skill_id`` on everything the surrounding handler forwards
        afterwards.
        """
        if context is not None:
            return Message("", context=dict(context,
                                            skill_id=self.skill_id))
        source = dig_for_message()
        if source is None:
            return Message("", context={"skill_id": self.skill_id})
        return Message(source.msg_type, source.data,
                       dict(source.context, skill_id=self.skill_id))

    # --- naming -----------------------------------------------------------

    def _namespaced(self, event: str) -> str:
        """A fired event always belongs to its owner's namespace (§6.1)."""
        if event.startswith(f"{self.skill_id}."):
            return event
        return f"{self.skill_id}.{event}"

    def _default_id(self, event: str) -> str:
        return event[len(self.skill_id) + 1:] or "schedule"

    # --- timing -----------------------------------------------------------

    def _timing(self, at, in_seconds, every, local, zone) -> dict:
        given = {}
        if at is not None:
            given["at"] = self._instant(at, zone, "at")
        if in_seconds is not None:
            given["in"] = {"seconds": in_seconds}
        if every is not None:
            given["every"] = every
        if local is not None:
            given["local"] = local
        if len(given) != 1:
            raise ValueError("exactly one of at, in_seconds, every, local "
                             "is required")
        return given

    @staticmethod
    def _instant(when: datetime, zone: Optional[str], field: str) -> str:
        if not isinstance(when, datetime):
            raise TypeError(f"{field} must be a datetime, got {when!r}")
        if when.tzinfo is None:
            if zone is None:
                raise ValueError(f"{field} is a naive datetime and no zone "
                                 f"was given; pass an aware datetime or zone=")
            when = when.replace(tzinfo=ZoneInfo(zone))
        return when.isoformat()

    # --- handler ownership ------------------------------------------------

    def _take_over_handler(self, schedule_id: str, event: str,
                           handler: Callable[..., None], once: bool):
        self._release_handler(schedule_id)
        wrapped = create_basic_wrapper(
            handler,
            lambda err: LOG.exception(f"error in scheduled event handler: {err}"))
        self.events.add(event, wrapped, once=once)
        self._handled_events[schedule_id] = event

    def _release_handler(self, schedule_id: str):
        event = self._handled_events.pop(schedule_id, None)
        if event:
            self.events.remove(event)
