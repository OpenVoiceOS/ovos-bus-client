"""SCHEDULER-1: the service that fires a named event on the bus at a
wall-clock instant, once or on a recurrence, on behalf of a component.

The service owns the ``scheduler.*`` topic family and, for one stable
cycle, the ``mycroft.scheduler.*`` topics that preceded it.
"""
import json
import os
import time
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, Thread
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ovos_config.config import Configuration
from ovos_config.locations import get_xdg_config_save_path
from ovos_config.meta import get_xdg_base
from ovos_utils.log import LOG
from ovos_utils.xdg_utils import xdg_state_home

from ovos_bus_client.message import Message
from ovos_bus_client.version import VERSION_MAJOR

#: topics kept as aliases for the pre-specification protocol
LEGACY_REMOVAL_VERSION = f"{VERSION_MAJOR + 1}.0.0"

#: compact UTF-8 JSON bytes allowed in a record's ``data``
MAX_DATA_BYTES = 16384
#: cap on the instants reported in one ``scheduler.missed``
MAX_REPORTED = 100
#: occurrences one evaluation will work through; a longer backlog drains
#: over the ticks that follow
MAX_BACKLOG = 10000
TICK_SECONDS = 0.5
#: a wall-clock/monotonic divergence above this is a step, not drift
CLOCK_STEP_THRESHOLD = 2.0

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

#: what one schedule owes the bus in this evaluation
_Plan = namedtuple("_Plan", "schedule key due emitted reported fired_late")


class ScheduleError(ValueError):
    """A request the scheduler refuses, carrying its wire error code."""

    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def parse_instant(value, field: str) -> datetime:
    """Parse an RFC 3339 instant. An instant without an offset is refused:
    without one the string does not name a point on the time line."""
    if not isinstance(value, str):
        raise ScheduleError("bad_instant", f"{field} must be an RFC 3339 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ScheduleError("bad_instant", f"{field} is not RFC 3339: {value}")
    if parsed.tzinfo is None:
        raise ScheduleError("bad_instant", f"{field} has no UTC offset: {value}")
    return parsed


def format_instant(when: datetime) -> str:
    return when.isoformat()


def _parse_clock(value) -> Tuple[int, int, int]:
    if not isinstance(value, str):
        raise ScheduleError("bad_recurrence", "local.time must be HH:MM or HH:MM:SS")
    parts = value.split(":")
    if len(parts) not in (2, 3):
        raise ScheduleError("bad_recurrence", f"local.time is not HH:MM[:SS]: {value}")
    try:
        hour, minute = int(parts[0]), int(parts[1])
        second = int(parts[2]) if len(parts) == 3 else 0
    except ValueError:
        raise ScheduleError("bad_recurrence", f"local.time is not HH:MM[:SS]: {value}")
    if not (0 <= hour < 24 and 0 <= minute < 60 and 0 <= second < 60):
        raise ScheduleError("bad_recurrence", f"local.time out of range: {value}")
    return hour, minute, second


def _positive_number(value, field: str, code: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ScheduleError(code, f"{field} must be a number > 0")
    return value


class Schedule:
    """One validated record plus the state the service keeps for it.

    ``cursor`` is the instant up to and including which occurrences have
    been consumed, and ``last_fired`` the due instant of the most recent
    occurrence that reached the bus. Together they are the whole of the
    no-double-fire guarantee: an occurrence is only produced strictly
    after the cursor, and never at or before ``last_fired``.
    """

    def __init__(self, record: dict, cursor: Optional[datetime] = None,
                 consumed: int = 0, anchored: bool = True,
                 last_fired: Optional[datetime] = None,
                 missed: Optional[List[str]] = None,
                 estimate: Optional[datetime] = None,
                 deadline: Optional[float] = None):
        self.record = record
        self.consumed = consumed
        self.anchored = anchored
        self.last_fired = last_fired
        self.missed = list(missed or [])
        # ``in`` schedules run off the monotonic clock while the service is
        # up; the wall estimate is what survives a restart
        self.estimate = estimate
        self.deadline = deadline
        if "in" in record and self.estimate is None:
            seconds = record["in"]["seconds"]
            self.estimate = datetime.now(timezone.utc) + timedelta(seconds=seconds)
            self.deadline = time.monotonic() + seconds
        self.cursor = cursor if cursor is not None else self._initial_cursor()

    @property
    def key(self) -> Tuple[str, str]:
        return self.record["owner"], self.record["id"]

    def _initial_cursor(self) -> datetime:
        one_second = timedelta(seconds=1)
        if "at" in self.record:
            return parse_instant(self.record["at"], "at") - one_second
        if "in" in self.record:
            return datetime.now(timezone.utc) - one_second
        if "every" in self.record:
            return parse_instant(self.record["every"]["start"], "every.start") - one_second
        return datetime.now(timezone.utc)

    def anchor(self, now: datetime):
        """Re-anchor a recurrence whose first occurrence was relative to a
        clock that was not yet synchronized."""
        if self.anchored:
            return
        self.anchored = True
        if "local" in self.record:
            self.cursor = now
        elif "every" in self.record:
            seconds = self.record["every"]["seconds"]
            self.record["every"]["start"] = format_instant(
                now + timedelta(seconds=seconds))
            self.cursor = now

    def retime(self, now: datetime):
        """Project an ``in`` schedule onto the wall clock. A wall-clock step
        moves the estimate, never the delay the owner asked for."""
        if self.deadline is None:
            return
        left = self.deadline - time.monotonic()
        self.estimate = now + timedelta(seconds=max(left, 0.0))
        if left <= 0:
            self.estimate = now

    def next_after(self, after: datetime,
                   consumed: Optional[int] = None) -> Optional[datetime]:
        """The first occurrence strictly after ``after``, or None.

        ``consumed`` overrides the record's own tally, so that planning can
        walk a backlog without spending it.
        """
        if consumed is None:
            consumed = self.consumed
        if "count" in self.record and consumed >= self.record["count"]:
            return None
        if self.last_fired is not None and after < self.last_fired:
            after = self.last_fired
        occurrence = self._raw_next_after(after)
        if occurrence is None:
            return None
        if "until" in self.record and \
                occurrence > parse_instant(self.record["until"], "until"):
            return None
        return occurrence

    def _raw_next_after(self, after: datetime) -> Optional[datetime]:
        if "at" in self.record:
            at = parse_instant(self.record["at"], "at")
            return at if at > after else None
        if "in" in self.record:
            return self.estimate if self.estimate > after else None
        if "every" in self.record:
            start = parse_instant(self.record["every"]["start"], "every.start")
            seconds = self.record["every"]["seconds"]
            if after < start:
                return start
            # occurrences are anchored on start, never on the previous fire
            elapsed = (after - start).total_seconds()
            return start + timedelta(seconds=(int(elapsed // seconds) + 1) * seconds)
        return self._next_local_after(after)

    def _next_local_after(self, after: datetime) -> Optional[datetime]:
        local = self.record["local"]
        zone = ZoneInfo(local["zone"])
        hour, minute, second = _parse_clock(local["time"])
        days = local.get("days") or list(WEEKDAYS)
        day = after.astimezone(zone).date()
        for _ in range(400):
            if WEEKDAYS[day.weekday()] in days:
                occurrence = self._wall_clock_instant(day, hour, minute, second, zone)
                # a spring-forward gap can push the reading onto the next
                # day, which the owner did not ask for
                landed = occurrence.astimezone(zone)
                if occurrence > after and WEEKDAYS[landed.weekday()] in days:
                    return occurrence
            day += timedelta(days=1)
        return None

    @staticmethod
    def _wall_clock_instant(day, hour, minute, second, zone) -> datetime:
        """The instant at which the wall clock in ``zone`` reads the given
        time on ``day``. Across a spring-forward gap the wall clock never
        reads it, and the occurrence is the first instant after the gap;
        across a fall-back overlap it reads it twice, and the occurrence
        is the first of the two."""
        naive = datetime(day.year, day.month, day.day, hour, minute, second)
        first = naive.replace(tzinfo=zone, fold=0)
        if first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == naive:
            return first
        # the wall clock skipped this reading: the occurrence is the first
        # instant at which the wall clock reads at or past it
        low = (naive - timedelta(hours=6)).replace(tzinfo=zone).astimezone(timezone.utc)
        high = (naive + timedelta(hours=6)).replace(tzinfo=zone).astimezone(timezone.utc)
        while (high - low) > timedelta(seconds=1):
            middle = low + (high - low) / 2
            if middle.astimezone(zone).replace(tzinfo=None) < naive:
                low = middle
            else:
                high = middle
        return high.astimezone(zone)

    def remaining(self) -> Optional[int]:
        if "at" in self.record or "in" in self.record:
            return 0
        if "count" in self.record:
            return max(0, self.record["count"] - self.consumed)
        return None

    def state(self, now: datetime) -> dict:
        nxt = self.next_after(max(self.cursor, now))
        return {"next": format_instant(nxt) if nxt else None,
                "last_fired": format_instant(self.last_fired) if self.last_fired else None,
                "missed": list(self.missed),
                "remaining": self.remaining()}

    def as_stored(self) -> dict:
        return {"record": self.record, "cursor": format_instant(self.cursor),
                "consumed": self.consumed, "anchored": self.anchored,
                "missed": list(self.missed),
                "estimate": format_instant(self.estimate) if self.estimate else None,
                "last_fired": format_instant(self.last_fired) if self.last_fired else None}

    @classmethod
    def from_stored(cls, stored: dict) -> "Schedule":
        last_fired = stored.get("last_fired")
        estimate = stored.get("estimate")
        return cls(stored["record"],
                   cursor=parse_instant(stored["cursor"], "cursor"),
                   consumed=stored.get("consumed", 0),
                   anchored=stored.get("anchored", True),
                   last_fired=parse_instant(last_fired, "last_fired") if last_fired else None,
                   missed=stored.get("missed"),
                   estimate=parse_instant(estimate, "estimate") if estimate else None)


def validate_record(data: dict, namespaced: bool = True,
                    previous: Optional[dict] = None) -> dict:
    """Validate a request against §3.1 and return the record to store."""
    if not isinstance(data, dict):
        raise ScheduleError("invalid_record", "request data must be an object")

    schedule_id = data.get("id")
    owner = data.get("owner")
    event = data.get("event")
    for name, value in (("id", schedule_id), ("owner", owner)):
        if not isinstance(value, str) or not value:
            raise ScheduleError("invalid_record", f"{name} is required")
    if not isinstance(event, str) or not event:
        raise ScheduleError("bad_event", "event is required")
    if namespaced:
        prefix = f"{owner}."
        name = event[len(prefix):] if event.startswith(prefix) else None
        if not name or ":" in name:
            raise ScheduleError(
                "bad_event", f"event must be <owner>.<name> for owner {owner}, "
                             f"with a non-empty name free of ':'; got {event}")

    timings = [k for k in ("at", "in", "every", "local")
               if k in data and data[k] is not None]
    if len(timings) != 1:
        raise ScheduleError("invalid_record",
                            "exactly one of at, in, every, local is required")
    timing = timings[0]

    payload = data.get("data") or {}
    if not isinstance(payload, dict):
        raise ScheduleError("invalid_record", "data must be an object")
    try:
        size = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        raise ScheduleError("invalid_record", "data is not JSON serializable")
    if size > MAX_DATA_BYTES:
        raise ScheduleError("payload_too_large",
                            f"data is {size} bytes, the limit is {MAX_DATA_BYTES}")

    record = {"id": schedule_id, "owner": owner, "event": event, "data": payload}
    bounded = timing in ("every", "local")

    if timing in ("at", "in"):
        if data.get("until") is not None or data.get("count") is not None:
            raise ScheduleError("invalid_record",
                                "until and count are for recurring schedules")
    if timing == "at":
        record["at"] = format_instant(parse_instant(data["at"], "at"))
    elif timing == "in":
        delay = data["in"]
        if not isinstance(delay, dict):
            raise ScheduleError("invalid_record", "in must be an object")
        record["in"] = {"seconds": _positive_number(delay.get("seconds"),
                                                    "in.seconds", "invalid_record")}
    elif timing == "every":
        every = data["every"]
        if not isinstance(every, dict):
            raise ScheduleError("bad_recurrence", "every must be an object")
        seconds = _positive_number(every.get("seconds"), "every.seconds",
                                   "bad_recurrence")
        start = every.get("start")
        if start is not None:
            start = format_instant(parse_instant(start, "every.start"))
        elif previous and previous.get("every", {}).get("seconds") == seconds:
            # an owner re-creating an unchanged recurrence keeps its phase
            start = previous["every"]["start"]
        else:
            start = format_instant(datetime.now(timezone.utc) + timedelta(seconds=seconds))
        record["every"] = {"seconds": seconds, "start": start}
    else:
        local = data["local"]
        if not isinstance(local, dict):
            raise ScheduleError("bad_recurrence", "local must be an object")
        _parse_clock(local.get("time"))
        zone = local.get("zone")
        if not isinstance(zone, str) or not zone:
            raise ScheduleError("bad_recurrence", "local.zone is required")
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ScheduleError("bad_recurrence", f"unknown time zone: {zone}")
        days = local.get("days")
        if days is not None and (not isinstance(days, list) or not days or
                                 any(d not in WEEKDAYS for d in days)):
            raise ScheduleError("bad_recurrence",
                                "local.days must be a non-empty list of mon..sun")
        record["local"] = {"time": local["time"], "zone": zone}
        if days is not None:
            record["local"]["days"] = list(days)

    if bounded:
        if data.get("until") is not None:
            record["until"] = format_instant(parse_instant(data["until"], "until"))
        if data.get("count") is not None:
            count = data["count"]
            if not isinstance(count, int) or isinstance(count, bool) or count < 1:
                raise ScheduleError("invalid_record", "count must be an integer >= 1")
            record["count"] = count

    misfire = data.get("misfire", "late")
    if misfire not in ("late", "skip", "all"):
        raise ScheduleError("invalid_record", "misfire must be late, skip or all")
    record["misfire"] = misfire

    grace = data.get("grace_s", 60)
    if not isinstance(grace, (int, float)) or isinstance(grace, bool) or grace < 0:
        raise ScheduleError("invalid_record", "grace_s must be a number >= 0")
    record["grace_s"] = grace

    ephemeral = data.get("ephemeral", False)
    if not isinstance(ephemeral, bool):
        raise ScheduleError("invalid_record", "ephemeral must be a boolean")
    record["ephemeral"] = ephemeral

    return record


def default_store_path(filename: str = "schedule.json") -> str:
    return os.path.join(xdg_state_home(), get_xdg_base(), filename)


class ScheduledEventService(Thread):
    """The SCHEDULER-1 service.

    Requests arrive on ``scheduler.schedule``, ``scheduler.cancel``,
    ``scheduler.get`` and ``scheduler.list``; each is answered exactly
    once on the matching ``.response`` topic. Accepted state reaches the
    store before the response goes out, and the due instant of every
    fired occurrence reaches it immediately after the event, so an
    occurrence is delivered at least once and never twice.
    """

    def __init__(self, bus, store_path: Optional[str] = None,
                 autostart: bool = True, admins: Optional[list] = None):
        super().__init__(daemon=True)
        self.bus = bus
        self.store_path = store_path or default_store_path()
        self.schedules = {}
        self.lock = Lock()

        self._wall_reference = time.time()
        self._mono_reference = time.monotonic()
        #: newest instant any past run wrote; a wall clock behind it is a
        #: board that has not reached its time source yet
        self._written_at: Optional[datetime] = None
        self._clock_synced = True

        self._legacy_notices = set()
        #: component ids allowed to act under the pseudo-owner ``*``; an
        #: empty allowlist means no caller can
        if admins is None:
            admins = Configuration().get("scheduler", {}).get("admins") or []
        self.admins = list(admins)

        self._migrate_legacy_store()
        self._load()
        self._clock_synced = self._written_at is None or self._now() >= self._written_at

        self._handlers = (
            ("scheduler.schedule", self.handle_schedule),
            ("scheduler.cancel", self.handle_cancel),
            ("scheduler.get", self.handle_get),
            ("scheduler.list", self.handle_list),
            ("system.clock.synced", self.handle_clock_synced),
            ("mycroft.scheduler.schedule_event", self.handle_legacy_schedule),
            ("mycroft.scheduler.remove_event", self.handle_legacy_remove),
            ("mycroft.scheduler.update_event", self.handle_legacy_update),
            ("mycroft.scheduler.get_event", self.handle_legacy_get),
            ("mycroft.scheduler.list_events", self.handle_legacy_list))
        for topic, handler in self._handlers:
            self.bus.on(topic, handler)

        self._running = Event()
        self._stopping = Event()
        if autostart:
            self.start()
            self._running.wait(10)
        else:
            self._stopping.set()
            self._running.clear()

    # --- clock ------------------------------------------------------------

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _clock_stepped(self) -> float:
        """Seconds by which the wall clock moved beyond the passage of time
        since the last reading. Zero when the two clocks agree."""
        wall, mono = time.time(), time.monotonic()
        drift = (wall - self._wall_reference) - (mono - self._mono_reference)
        self._wall_reference, self._mono_reference = wall, mono
        return drift if abs(drift) > CLOCK_STEP_THRESHOLD else 0.0

    # --- persistence ------------------------------------------------------

    def _migrate_legacy_store(self):
        """The store used to live in the configuration directory. Move it
        once so that schedules written by the previous service are read."""
        if os.path.isfile(self.store_path):
            return
        old = os.path.join(get_xdg_config_save_path(), "schedule.json")
        if not os.path.isfile(old) or os.path.isfile(f"{old}.migrated"):
            return
        try:
            with open(old) as handle:
                legacy = json.load(handle)
            for name, entries in legacy.items():
                for entry in entries:
                    payload = entry[2] if len(entry) > 2 else {}
                    record = self._legacy_record(name, entry[0], entry[1], payload)
                    self.schedules[record["owner"], record["id"]] = Schedule(record)
        except Exception as err:
            LOG.error(f"could not migrate the legacy schedule store: {err}")
            return
        self._persist()
        # the original stays where it is, so a downgrade still finds it
        open(f"{old}.migrated", "w").close()
        LOG.info(f"migrated {len(self.schedules)} schedules to {self.store_path}")

    def _load(self):
        if not os.path.isfile(self.store_path):
            return
        try:
            with open(self.store_path) as handle:
                stored = json.load(handle)
        except Exception as err:
            LOG.error(f"unreadable schedule store {self.store_path}: {err}")
            return
        written_at = stored.get("written_at")
        if written_at:
            try:
                self._written_at = parse_instant(written_at, "written_at")
            except ScheduleError as err:
                LOG.warning(f"ignoring unreadable store timestamp: {err}")
        for entry in stored.get("schedules", []):
            try:
                schedule = Schedule.from_stored(entry)
            except ScheduleError as err:
                LOG.error(f"dropping unreadable schedule: {err}")
                continue
            self.schedules[schedule.key] = schedule
            # only instants that were already in the past when they were
            # written count; a schedule due next week says nothing about
            # whether this device knows what time it is
            if schedule.last_fired and (self._written_at is None or
                                        schedule.last_fired > self._written_at):
                self._written_at = schedule.last_fired

    def _persist(self):
        """Atomic replace: a reader sees the previous file or the new one."""
        directory = os.path.dirname(self.store_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        payload = {"version": 1, "written_at": format_instant(self._now()),
                   "schedules": [s.as_stored() for s in self.schedules.values()
                                 if not s.record["ephemeral"]]}
        tmp = f"{self.store_path}.tmp"
        with open(tmp, "w") as handle:
            json.dump(payload, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.store_path)

    # --- request handling -------------------------------------------------

    @staticmethod
    def _identity(message: Message) -> Optional[str]:
        """The authenticated component identity the bus carries, if any."""
        return message.context.get("skill_id")

    def _check_owner(self, message: Message, owner, wildcard: bool = False) -> str:
        if not isinstance(owner, str) or not owner:
            raise ScheduleError("invalid_record", "owner is required")
        identity = self._identity(message)
        if owner == "*":
            if not wildcard:
                raise ScheduleError("not_owner",
                                    "* is not an owner a schedule can belong to")
            if not identity or identity not in self.admins:
                raise ScheduleError(
                    "not_owner",
                    f"{identity or 'an anonymous caller'} is not allowed to act "
                    f"across every owner")
            return owner
        if identity and identity != owner:
            raise ScheduleError("not_owner",
                                f"{identity} may not act on schedules of {owner}")
        return owner

    def _respond(self, message: Message, data: dict):
        self.bus.emit(message.response(data))

    def _fail(self, message: Message, err: ScheduleError):
        LOG.warning(f"{message.msg_type} refused: {err.reason}")
        self._respond(message, {"ok": False, "error": err.code, "reason": err.reason})

    def handle_schedule(self, message: Message):
        try:
            owner = self._check_owner(message, message.data.get("owner"))
            with self.lock:
                previous = self.schedules.get((owner, message.data.get("id")))
                record = validate_record(
                    message.data, previous=previous.record if previous else None)
                key = (owner, record["id"])
                replaced = key in self.schedules
                schedule = Schedule(record, anchored=self._clock_synced)
                self.schedules[key] = schedule
                try:
                    self._persist()
                except OSError:
                    # a request that could not be stored did not happen; the
                    # running process must not disagree with the store it
                    # will be restarted from
                    if replaced:
                        self.schedules[key] = previous
                    else:
                        self.schedules.pop(key, None)
                    raise
                nxt = schedule.next_after(max(schedule.cursor, self._now()))
        except ScheduleError as err:
            return self._fail(message, err)
        except OSError as err:
            return self._fail(message, ScheduleError(
                "internal", f"the schedule store could not be written: {err}"))
        self._respond(message, {"ok": True, "id": record["id"], "owner": owner,
                                "next": format_instant(nxt) if nxt else None,
                                "replaced": replaced})

    def handle_cancel(self, message: Message):
        try:
            owner = self._check_owner(message, message.data.get("owner"),
                                      wildcard=True)
            schedule_id = message.data.get("id")
            if not isinstance(schedule_id, str) or not schedule_id:
                raise ScheduleError("invalid_record", "id is required")
            with self.lock:
                if owner == "*":
                    keys = [k for k in self.schedules if k[1] == schedule_id]
                else:
                    keys = [k for k in ((owner, schedule_id),) if k in self.schedules]
                dropped = {key: self.schedules.pop(key) for key in keys}
                if keys:
                    try:
                        self._persist()
                    except OSError:
                        self.schedules.update(dropped)
                        raise
        except ScheduleError as err:
            return self._fail(message, err)
        except OSError as err:
            return self._fail(message, ScheduleError(
                "internal", f"the schedule store could not be written: {err}"))
        self._respond(message, {"ok": True, "id": schedule_id, "owner": owner,
                                "existed": bool(keys)})

    def _view(self, schedule: Schedule) -> dict:
        return {"record": dict(schedule.record),
                "state": schedule.state(self._now())}

    def handle_get(self, message: Message):
        try:
            owner = self._check_owner(message, message.data.get("owner"))
            schedule_id = message.data.get("id")
            if not isinstance(schedule_id, str) or not schedule_id:
                raise ScheduleError("invalid_record", "id is required")
            with self.lock:
                schedule = self.schedules.get((owner, schedule_id))
                view = self._view(schedule) if schedule else None
        except ScheduleError as err:
            return self._fail(message, err)
        data = {"ok": True, "id": schedule_id, "owner": owner,
                "existed": view is not None,
                "record": view["record"] if view else None,
                "state": view["state"] if view else None}
        self._respond(message, data)

    def handle_list(self, message: Message):
        try:
            owner = self._check_owner(message, message.data.get("owner"),
                                      wildcard=True)
            with self.lock:
                views = [self._view(s) for k, s in self.schedules.items()
                         if owner == "*" or k[0] == owner]
        except ScheduleError as err:
            return self._fail(message, err)
        self._respond(message, {"ok": True, "owner": owner, "schedules": views})

    def handle_clock_synced(self, message: Message):
        if not self._clock_synced:
            LOG.info("clock synchronized, replaying deferred schedules")
        self._clock_synced = True
        self._clock_stepped()
        self.replay()

    # --- evaluation -------------------------------------------------------

    def run(self):
        LOG.info("ScheduledEventService started")
        self._stopping.clear()
        self._running.set()
        self.replay()
        while not self._stopping.wait(TICK_SECONDS):
            try:
                self.tick()
            except Exception as err:
                LOG.exception(err)
        LOG.info("ScheduledEventService stopped")

    def tick(self):
        step = self._clock_stepped()
        if not self._clock_synced:
            if self._written_at is None or self._now() >= self._written_at:
                LOG.info("wall clock reached a plausible reading, replaying")
                self._clock_synced = True
                return self.replay()
            return
        if step:
            LOG.info(f"wall clock stepped by {step:.0f}s, re-evaluating schedules")
        self._evaluate()

    def replay(self):
        """Announce readiness, then apply the misfire policy to everything
        that was due while the service was down."""
        restored = len(self.schedules)
        plan = self._plan(replaying=True)
        self.bus.emit(Message("scheduler.ready", {
            "schedules": restored,
            "missed": len(plan),
            "clock": "synchronized" if self._clock_synced else "unsynchronized"}))
        self._execute(plan)

    def _evaluate(self):
        self._execute(self._plan())

    def _plan(self, replaying: bool = False) -> list:
        """Work out what every schedule owes the bus, without consuming
        anything: consumption belongs next to the emission it pays for."""
        if not self._clock_synced:
            return []
        now = self._now()
        plans = []
        with self.lock:
            for key in list(self.schedules):
                schedule = self.schedules[key]
                schedule.anchor(now)
                schedule.retime(now)
                due = []
                cursor = schedule.cursor
                consumed = schedule.consumed
                while len(due) < MAX_BACKLOG:
                    occurrence = schedule.next_after(cursor, consumed)
                    if occurrence is None or occurrence > now:
                        break
                    due.append(occurrence)
                    cursor = occurrence
                    consumed += 1
                if not due:
                    continue
                grace = schedule.record["grace_s"]
                late = [d for d in due if (now - d).total_seconds() > grace]
                on_time = [d for d in due if d not in late]
                policy = schedule.record["misfire"]
                fired_late = list(late) if policy == "all" else \
                    late[-1:] if policy == "late" else []
                # after a restart an occurrence may have reached the bus
                # already and lost its record to the crash window, so every
                # occurrence replay produces is reported too; owners dedupe
                # on (id, due)
                plans.append(_Plan(schedule=schedule, key=key, due=due,
                                   emitted=sorted(fired_late + on_time),
                                   reported=due if replaying else late,
                                   fired_late=fired_late))
        return plans

    def _execute(self, plans: list):
        for plan in plans:
            if plan.reported:
                self._report(plan)
            self._deliver(plan)

    def _report(self, plan):
        reported, fired_late = plan.reported, plan.fired_late
        self.bus.emit(Message("scheduler.missed", {
            "id": plan.schedule.record["id"],
            "owner": plan.schedule.record["owner"],
            "missed": [format_instant(d) for d in reported[:MAX_REPORTED]],
            "fired_late": [format_instant(d) for d in fired_late[:MAX_REPORTED]],
            "truncated": len(reported) > MAX_REPORTED or
                         len(fired_late) > MAX_REPORTED,
            "next": self._next_instant(plan.schedule)}))

    def _deliver(self, plan):
        """Emit one occurrence, then persist the consumption it represents,
        then the next. A kill anywhere in here can repeat at most the
        occurrence that was in flight."""
        schedule = plan.schedule
        base = schedule.consumed
        index = {occurrence: position for position, occurrence in enumerate(plan.due)}
        for occurrence in plan.emitted:
            schedule.cursor = occurrence
            schedule.consumed = base + index[occurrence] + 1
            self._fire(schedule, occurrence)
            with self.lock:
                self._persist()
        # occurrences the policy dropped are consumed all the same
        schedule.cursor = max(schedule.cursor, plan.due[-1])
        schedule.consumed = base + len(plan.due)
        if plan.emitted:
            schedule.missed = [format_instant(d) for d in plan.reported
                               if d > plan.emitted[-1]]
        else:
            schedule.missed.extend(format_instant(d) for d in plan.reported)
        del schedule.missed[:-MAX_REPORTED]
        with self.lock:
            if "at" in schedule.record or "in" in schedule.record or \
                    schedule.next_after(schedule.cursor) is None:
                self.schedules.pop(plan.key, None)
            self._persist()

    def _next_instant(self, schedule: Schedule) -> Optional[str]:
        nxt = schedule.next_after(max(schedule.cursor, self._now()))
        return format_instant(nxt) if nxt else None

    def _fire(self, schedule: Schedule, due: datetime):
        """Emit one occurrence. The message originates here: nothing of the
        request that created the schedule is carried into it."""
        record = schedule.record
        context = {"scheduler": {"id": record["id"], "owner": record["owner"],
                                 "due": format_instant(due),
                                 "fired": format_instant(self._now()),
                                 "remaining": schedule.remaining()}}
        LOG.debug(f"scheduled event fired: {record['id']}")
        self.bus.emit(Message(record["event"], dict(record["data"]), context))
        if schedule.last_fired is None or due > schedule.last_fired:
            schedule.last_fired = due

    # --- legacy adapter ---------------------------------------------------

    def _legacy_notice(self, topic: str):
        if topic not in self._legacy_notices:
            self._legacy_notices.add(topic)
            LOG.warning(f"{topic} is deprecated in favour of the scheduler.* "
                        f"topics and will be removed in ovos-bus-client "
                        f"{LEGACY_REMOVAL_VERSION}")

    @staticmethod
    def _legacy_owner(name: str) -> str:
        return name.split(":", 1)[0] if ":" in name else "legacy"

    def _legacy_record(self, name: str, when: float, repeat, data) -> dict:
        instant = format_instant(datetime.fromtimestamp(when, timezone.utc))
        record = {"id": name, "owner": self._legacy_owner(name), "event": name,
                  "data": data or {}}
        if repeat:
            record["every"] = {"seconds": repeat, "start": instant}
        else:
            record["at"] = instant
        return validate_record(record, namespaced=False)

    def handle_legacy_schedule(self, message: Message):
        self._legacy_notice(message.msg_type)
        name = message.data.get("event")
        when = message.data.get("time")
        if not name or when is None:
            LOG.error("legacy schedule request is missing event or time")
            return
        try:
            record = self._legacy_record(name, when, message.data.get("repeat"),
                                         message.data.get("data"))
        except ScheduleError as err:
            LOG.error(f"legacy schedule request refused: {err.reason}")
            return
        with self.lock:
            # replacing rather than appending is what stops a skill that
            # re-creates its schedules on boot from doubling them
            self.schedules[record["owner"], record["id"]] = Schedule(
                record, anchored=self._clock_synced)
            self._persist()

    def handle_legacy_remove(self, message: Message):
        self._legacy_notice(message.msg_type)
        name = message.data.get("event")
        if not name:
            return
        with self.lock:
            if self.schedules.pop((self._legacy_owner(name), name), None):
                self._persist()

    def handle_legacy_update(self, message: Message):
        self._legacy_notice(message.msg_type)
        name = message.data.get("event")
        with self.lock:
            schedule = self.schedules.get((self._legacy_owner(name or ""), name))
            if schedule is None:
                return
            schedule.record["data"] = message.data.get("data") or {}
            self._persist()

    def _legacy_entry(self, schedule: Schedule) -> list:
        nxt = schedule.next_after(max(schedule.cursor, self._now()))
        return [nxt.timestamp() if nxt else 0,
                schedule.record.get("every", {}).get("seconds"),
                schedule.record["data"], {}]

    def handle_legacy_get(self, message: Message):
        self._legacy_notice(message.msg_type)
        name = message.data.get("name")
        with self.lock:
            schedule = self.schedules.get((self._legacy_owner(name or ""), name))
            entry = self._legacy_entry(schedule) if schedule else None
        # the wire refuses a list-shaped payload, so the tuple the previous
        # service tried to return travels under a key
        self.bus.emit(message.reply(f"mycroft.event_status.callback.{name}",
                                    data={"event": name, "schedule": entry}))

    def handle_legacy_list(self, message: Message):
        self._legacy_notice(message.msg_type)
        with self.lock:
            events = {s.record["id"]: [self._legacy_entry(s)]
                      for s in self.schedules.values()}
        self.bus.emit(message.response(data={"scheduled_events": events}))

    # --- lifecycle --------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return not self._stopping.is_set()

    @is_running.setter
    def is_running(self, value: bool):
        if value:
            self._stopping.clear()
        else:
            self._stopping.set()

    def shutdown(self):
        self._stopping.set()
        # only this service's own subscriptions go; an unrelated observer
        # on the same topic is none of our business
        for topic, handler in self._handlers:
            self.bus.remove(topic, handler)
        if self.is_alive():
            self.join(30)
        with self.lock:
            self._persist()
        self._running.clear()
