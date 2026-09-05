"""The scheduler service (SCHEDULER-1).

Requests arrive on the four ``ovos.scheduler.*`` request topics and each is
answered exactly once on the matching response topic. Accepted state reaches
the store before the answer goes out, and the due instant of a fired
occurrence reaches it immediately after the event, so an occurrence is
delivered at least once and repeated at most once.
"""
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Event, RLock, Thread
from typing import Dict, List, Optional, Tuple

from ovos_config.config import Configuration
from ovos_utils.log import LOG

from ovos_bus_client.message import Message
from ovos_bus_client.util.scheduled_events import topics
from ovos_bus_client.util.scheduled_events.legacy import (
    LegacyAdapter, mark_migrated, pending_migration_path, read_migration_source)
from ovos_bus_client.util.scheduled_events.records import (
    ScheduleError, format_instant, timing_of)
from ovos_bus_client.util.scheduled_events.schedules import MAX_REPORTED, Schedule
from ovos_bus_client.util.scheduled_events.store import (
    ScheduleStore, default_store_path)
from ovos_bus_client.util.scheduled_events.validation import validate_record

#: seconds between evaluations
TICK_SECONDS = 0.5
#: occurrences one evaluation works through; a longer backlog drains over the
#: ticks that follow
MAX_BACKLOG = 10000
#: a wall-clock/monotonic divergence above this is a step, not drift
CLOCK_STEP_THRESHOLD = 2.0

ScheduleKey = Tuple[str, str]


@dataclass
class DueBatch:
    """What one schedule owes the bus in one evaluation.

    Nothing here is consumed yet: consumption belongs next to the emission it
    pays for, so that a crash mid-batch loses at most one occurrence's record.
    """

    schedule: Schedule
    #: every occurrence at or before the evaluation instant, oldest first
    due: List[datetime]
    #: the occurrences that reach the bus, oldest first, each with its
    #: position in ``due`` — which is what it consumes when it fires
    to_fire: List[Tuple[int, datetime]]
    #: the occurrences the missed message names
    to_report: List[datetime]
    #: the subset of ``to_fire`` that is firing after its grace period
    fired_late: List[datetime] = field(default_factory=list)


class ScheduledEventService(Thread):
    """Keeps schedules and fires their events.

    Construct it with a bus; it subscribes, restores its store and, unless
    ``autostart`` is false, starts evaluating on its own thread.
    """

    def __init__(self, bus, store_path: Optional[str] = None,
                 autostart: bool = True, admins: Optional[list] = None):
        super().__init__(daemon=True)
        self.bus = bus
        self.schedule_store = ScheduleStore(store_path or default_store_path())
        self.schedules: Dict[ScheduleKey, Schedule] = {}
        self.lock = RLock()
        self.legacy = LegacyAdapter(self)
        self.admins = self._configured_admins(admins)

        self._wall_reference = time.time()
        self._mono_reference = time.monotonic()
        #: newest instant a past run recorded as already past; a wall clock
        #: behind it belongs to a device that has not found a time source yet
        self.newest_past_instant: Optional[datetime] = None
        self.clock_synced = True

        self._restore()
        self._subscriptions = self._subscribe()

        self._running = Event()
        self._stopping = Event()
        if autostart:
            self.start()
            self._running.wait(10)
        else:
            # nothing is evaluating, so is_running must read False; a caller
            # that wants a pass drives tick() or replay() by hand
            self._stopping.set()

    @property
    def store_path(self) -> str:
        return self.schedule_store.path

    @staticmethod
    def _configured_admins(admins: Optional[list]) -> list:
        """Component ids allowed to act under the pseudo-owner ``*`` (§6.2).

        An empty allowlist, which is the default, means no caller can.
        """
        if admins is None:
            admins = Configuration().get("scheduler", {}).get("admins") or []
        return list(admins)

    def _subscribe(self):
        subscriptions = (
            (topics.SCHEDULER_SCHEDULE, self.handle_schedule),
            (topics.SCHEDULER_CANCEL, self.handle_cancel),
            (topics.SCHEDULER_GET, self.handle_get),
            (topics.SCHEDULER_LIST, self.handle_list),
            (topics.CLOCK_SYNCED, self.handle_clock_synced),
        ) + self.legacy.subscriptions()
        for topic, handler in subscriptions:
            self.bus.on(topic, handler)
        return subscriptions

    # --- start-up ---------------------------------------------------------

    def _restore(self):
        """Load the store and work out whether the clock can be trusted."""
        self._migrate_config_directory_store()
        self.schedules.update(self.schedule_store.load())
        self.newest_past_instant = self._newest_past_instant()
        self.clock_synced = (self.newest_past_instant is None or
                             self.now() >= self.newest_past_instant)

    def _newest_past_instant(self) -> Optional[datetime]:
        """The newest instant this scheduler has recorded as already past.

        That is the wall-clock time of the most recent store write or the due
        instant of the most recent fire, whichever is later. Only instants
        that were already in the past when they were written count: a
        schedule due next week says nothing about whether the device knows
        what time it is (§7.2).
        """
        newest = self.schedule_store.written_at
        for schedule in self.schedules.values():
            if schedule.last_fired and (newest is None or
                                        schedule.last_fired > newest):
                newest = schedule.last_fired
        return newest

    def _migrate_config_directory_store(self):
        """Adopt the store the previous scheduler kept in the configuration
        directory, once, leaving the original in place."""
        if os.path.isfile(self.schedule_store.path):
            return
        source = pending_migration_path()
        if source is None:
            return
        try:
            adopted = read_migration_source(source)
            self.schedule_store.save(adopted.values())
        except Exception as err:
            LOG.error(f"could not migrate the legacy schedule store: {err}")
            return
        mark_migrated(source)
        LOG.info(f"migrated {len(adopted)} schedules to {self.schedule_store.path}")

    # --- clock ------------------------------------------------------------

    @staticmethod
    def now() -> datetime:
        """The wall clock, which is what due-ness is measured against."""
        return datetime.now(timezone.utc)

    def _clock_step(self) -> float:
        """Seconds by which the wall clock moved beyond the passage of time
        since the last reading, or zero when the two clocks agree (§7.1)."""
        wall, mono = time.time(), time.monotonic()
        drift = (wall - self._wall_reference) - (mono - self._mono_reference)
        self._wall_reference, self._mono_reference = wall, mono
        return drift if abs(drift) > CLOCK_STEP_THRESHOLD else 0.0

    def handle_clock_synced(self, message: Message):
        """A deployment signalling that the device reached a time source."""
        if not self.clock_synced:
            LOG.info("clock synchronized, replaying deferred schedules")
        self.clock_synced = True
        self._clock_step()
        self.replay()

    # --- requests ---------------------------------------------------------

    def handle_schedule(self, message: Message):
        """Create or replace a schedule (§4.1)."""
        try:
            owner = self._authorized_owner(message, message.data.get("owner"))
            with self.lock:
                previous = self.schedules.get((owner, message.data.get("id")))
                record = validate_record(
                    dict(message.data, context=message.context),
                    previous=previous.record if previous else None)
                schedule = self._continuing(record, previous)
                self._put_schedule(schedule, previous)
                upcoming = schedule.next_from_now(self.now())
        except ScheduleError as err:
            return self._refuse(message, err)
        except OSError as err:
            return self._refuse(message, _unwritable_store(err))
        self._answer(message, {"ok": True, "id": record["id"], "owner": owner,
                               "next": format_instant(upcoming) if upcoming else None,
                               "replaced": previous is not None})

    def handle_cancel(self, message: Message):
        """Delete a schedule. Cancelling one that does not exist is not an
        error; the answer simply says it did not exist (§4.1)."""
        try:
            owner = self._authorized_owner(message, message.data.get("owner"),
                                           wildcard=True)
            schedule_id = _required_id(message)
            with self.lock:
                existed = self._drop_matching(owner, schedule_id)
        except ScheduleError as err:
            return self._refuse(message, err)
        except OSError as err:
            return self._refuse(message, _unwritable_store(err))
        self._answer(message, {"ok": True, "id": schedule_id, "owner": owner,
                               "existed": existed})

    def handle_get(self, message: Message):
        """Read one schedule as stored, plus its computed state (§4.1)."""
        try:
            owner = self._authorized_owner(message, message.data.get("owner"))
            schedule_id = _required_id(message)
            with self.lock:
                schedule = self.schedules.get((owner, schedule_id))
                view = self._view(schedule) if schedule else None
        except ScheduleError as err:
            return self._refuse(message, err)
        self._answer(message, {"ok": True, "id": schedule_id, "owner": owner,
                               "existed": view is not None,
                               "record": view["record"] if view else None,
                               "state": view["state"] if view else None})

    def handle_list(self, message: Message):
        """Read the caller's schedules, or every schedule under ``*``."""
        try:
            owner = self._authorized_owner(message, message.data.get("owner"),
                                           wildcard=True)
            with self.lock:
                views = [self._view(schedule)
                         for key, schedule in self.schedules.items()
                         if owner == "*" or key[0] == owner]
        except ScheduleError as err:
            return self._refuse(message, err)
        self._answer(message, {"ok": True, "owner": owner, "schedules": views})

    def _view(self, schedule: Schedule) -> dict:
        return {"record": dict(schedule.record),
                "state": schedule.state(self.now())}

    def _answer(self, message: Message, data: dict):
        self.bus.emit(message.response(data))

    def _refuse(self, message: Message, err: ScheduleError):
        LOG.warning(f"{message.msg_type} refused: {err.reason}")
        self._answer(message, {"ok": False, "error": err.code,
                               "reason": err.reason})

    # --- ownership --------------------------------------------------------

    def _authorized_owner(self, message: Message, owner,
                          wildcard: bool = False) -> str:
        """The owner a request may act as, or a refusal (§6.2).

        Where the bus carries an authenticated component identity, it must
        match the request's owner. Where it does not, the owner field still
        scopes the request, so that a component cannot reach another
        component's schedules by omission.
        """
        if not isinstance(owner, str) or not owner:
            raise ScheduleError("invalid_record", "owner is required")
        identity = message.context.get("skill_id")
        if owner == "*":
            return self._authorized_administrator(identity, wildcard)
        if identity and identity != owner:
            raise ScheduleError("not_owner",
                                f"{identity} may not act on schedules of {owner}")
        return owner

    def _authorized_administrator(self, identity, wildcard: bool) -> str:
        if not wildcard:
            raise ScheduleError("not_owner",
                                "* is not an owner a schedule can belong to")
        if not identity or identity not in self.admins:
            raise ScheduleError(
                "not_owner",
                f"{identity or 'an anonymous caller'} is not allowed to act "
                f"across every owner")
        return "*"

    # --- the schedules the scheduler holds --------------------------------

    def all_schedules(self) -> List[Schedule]:
        with self.lock:
            return list(self.schedules.values())

    def find_schedule(self, key: ScheduleKey) -> Optional[Schedule]:
        with self.lock:
            return self.schedules.get(key)

    def replace_schedule(self, record: dict) -> Schedule:
        """Put a validated record in place of whatever shares its identity."""
        with self.lock:
            previous = self.schedules.get((record["owner"], record["id"]))
            schedule = self._continuing(record, previous)
            self._put_schedule(schedule, previous)
            return schedule

    def _continuing(self, record: dict,
                    previous: Optional[Schedule]) -> Schedule:
        """A replacement, carrying what the schedule it replaces has done.

        A replacement is not a new schedule. §5.2 makes sending the same
        request twice the same as sending it once, which it is not if the
        replacement arrives with an empty history and fires the morning
        again.

        ``last_fired`` comes across whatever else changed: §5.1 and §9.9 say
        without exception that an occurrence at or before the most recent
        fire is never fired again, and a replacement is not a way to ask for
        one. ``consumed`` comes across too. Nothing in §4.3 or §5.2 grants a
        replacement a fresh ``count`` budget, and an owner that wants one
        cancels the schedule and creates it again (§5.4) — where the spec is
        silent, the reading that cannot ring an alarm twice is the one to
        take.

        The cursor and the missed list are positions in a series. They mean
        nothing once the timing describes a different one, so they come
        across only when the timing is unchanged; ``last_fired`` still floors
        everything the new series can produce.
        """
        if previous is None:
            return Schedule(record, anchored=self.clock_synced)
        same_series = timing_of(previous.record) == timing_of(record)
        return Schedule(record, anchored=self.clock_synced,
                        last_fired=previous.last_fired,
                        consumed=previous.consumed,
                        cursor=previous.cursor if same_series else None,
                        missed=previous.missed if same_series else None)

    def drop_schedule(self, key: ScheduleKey) -> bool:
        """Delete one schedule. Returns whether it existed."""
        with self.lock:
            return self._drop_matching(key[0], key[1])

    def replace_payload(self, key: ScheduleKey, data: dict):
        """Change the data a schedule's occurrences fire with."""
        with self.lock:
            schedule = self.schedules.get(key)
            if schedule is None:
                return
            schedule.record["data"] = data
            self._persist()

    def _put_schedule(self, schedule: Schedule, previous: Optional[Schedule]):
        """Store a schedule, rolling the memory back if the write fails.

        A request that could not be persisted did not happen: the running
        process must not disagree with the store it will restart from (§5.1).
        """
        key = schedule.key
        self.schedules[key] = schedule
        try:
            self._persist()
        except OSError:
            if previous is not None:
                self.schedules[key] = previous
            else:
                self.schedules.pop(key, None)
            raise

    def _drop_matching(self, owner: str, schedule_id: str) -> bool:
        """Remove the schedules an owner and id name, rolling back on a failed
        write. Under ``*`` that is the id under every owner."""
        if owner == "*":
            keys = [key for key in self.schedules if key[1] == schedule_id]
        else:
            keys = [(owner, schedule_id)] if (owner, schedule_id) in self.schedules else []
        if not keys:
            return False
        dropped = {key: self.schedules.pop(key) for key in keys}
        try:
            self._persist()
        except OSError:
            self.schedules.update(dropped)
            raise
        return True

    def _persist(self):
        with self.lock:
            self.schedule_store.save(self.schedules.values())

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
        """One pass: notice a clock step, then fire whatever is due."""
        step = self._clock_step()
        if not self.clock_synced:
            self._recheck_the_clock()
            return
        if step:
            LOG.info(f"wall clock stepped by {step:.0f}s, re-evaluating schedules")
        self._evaluate()

    def _recheck_the_clock(self):
        """While the clock is unsynchronized the scheduler accepts and stores
        requests but evaluates nothing. It starts evaluating when the wall
        clock passes the newest instant it recorded as past (§7.2)."""
        if self.newest_past_instant is None or self.now() >= self.newest_past_instant:
            LOG.info("wall clock reached a plausible reading, replaying")
            self.clock_synced = True
            self.replay()

    def replay(self):
        """Announce readiness, then apply the misfire policy to everything
        that came due while the scheduler was not evaluating.

        Readiness goes out first so that an owner subscribed to it still hears
        its own late fires (§5.4).
        """
        restored = len(self.schedules)
        batches = self._plan(replaying=True)
        self.bus.emit(Message(topics.SCHEDULER_READY, {
            "schedules": restored,
            "missed": len(batches),
            "clock": "synchronized" if self.clock_synced else "unsynchronized"}))
        self._run(batches)

    def _evaluate(self):
        self._run(self._plan())

    def _run(self, batches: List[DueBatch]):
        for batch in batches:
            if batch.to_report:
                # the report precedes any late fire of the same batch (§4.3)
                self._report_missed(batch)
            self._fire_batch(batch)

    def _plan(self, replaying: bool = False) -> List[DueBatch]:
        """Work out what every schedule owes the bus, consuming nothing."""
        if not self.clock_synced:
            return []
        now = self.now()
        batches = []
        with self.lock:
            for schedule in list(self.schedules.values()):
                schedule.anchor(now)
                schedule.retime(now)
                batch = self._batch_for(schedule, now, replaying)
                if batch is not None:
                    batches.append(batch)
        return batches

    def _batch_for(self, schedule: Schedule, now: datetime,
                   replaying: bool) -> Optional[DueBatch]:
        due = self._occurrences_through(schedule, now)
        if not due:
            return None
        grace = schedule.record["grace_s"]
        late = [when for when in due if (now - when).total_seconds() > grace]
        fired_late = _late_fires(schedule.record["misfire"], late)

        to_fire = []
        for position, occurrence in enumerate(due):
            if occurrence not in late or occurrence in fired_late:
                to_fire.append((position, occurrence))

        # after a restart an occurrence may have reached the bus and lost its
        # record to the crash window, so replay reports everything it
        # produces; owners deduplicate on (id, due) (§5.1)
        return DueBatch(schedule=schedule, due=due, to_fire=to_fire,
                        to_report=due if replaying else late,
                        fired_late=fired_late)

    @staticmethod
    def _occurrences_through(schedule: Schedule,
                             now: datetime) -> List[datetime]:
        """Every occurrence at or before ``now`` that has not been consumed,
        oldest first, up to one evaluation's worth."""
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
        return due

    def _report_missed(self, batch: DueBatch):
        """Name every missed occurrence of one schedule, once (§4.3)."""
        schedule = batch.schedule
        truncated = (len(batch.to_report) > MAX_REPORTED or
                     len(batch.fired_late) > MAX_REPORTED)
        upcoming = self._next_occurrence_after(batch)
        self.bus.emit(Message(topics.SCHEDULER_MISSED, {
            "id": schedule.record["id"],
            "owner": schedule.record["owner"],
            "missed": [format_instant(when)
                       for when in batch.to_report[:MAX_REPORTED]],
            "fired_late": [format_instant(when)
                           for when in batch.fired_late[:MAX_REPORTED]],
            "truncated": truncated,
            "next": format_instant(upcoming) if upcoming else None}))

    def _next_occurrence_after(self, batch: DueBatch) -> Optional[datetime]:
        """The next occurrence once this batch has been consumed.

        The report goes out before the fires it announces (§4.3), so the
        schedule still carries the tally from before the batch. Reporting
        ``next`` from that tally would name an occurrence a spent ``count``
        can no longer produce.
        """
        schedule = batch.schedule
        return schedule.next_after(max(schedule.cursor, batch.due[-1], self.now()),
                                   schedule.consumed + len(batch.due))

    def _fire_batch(self, batch: DueBatch):
        """Emit one occurrence, persist the consumption it represents, then
        the next. A crash anywhere in here repeats at most the occurrence
        that was in flight (§5.1)."""
        schedule = batch.schedule
        already_consumed = schedule.consumed
        for position, occurrence in batch.to_fire:
            schedule.cursor = occurrence
            schedule.consumed = already_consumed + position + 1
            self._fire(schedule, occurrence)
            self._persist()

        # occurrences the policy dropped are consumed all the same (§4.3)
        schedule.cursor = max(schedule.cursor, batch.due[-1])
        schedule.consumed = already_consumed + len(batch.due)
        schedule.remember_missed(
            batch.to_report,
            since=batch.to_fire[-1][1] if batch.to_fire else None)
        self._retire_if_spent(schedule)
        self._persist()

    def _fire(self, schedule: Schedule, due: datetime):
        """Emit one occurrence, with the context the schedule was made with.

        §4.2: the fired message's context is the stored context of §3.5,
        unchanged but for the added ``scheduler`` key. Every other key is
        left exactly as it arrived, and none is dropped or rewritten, so a
        request that carried no context fires with the ``scheduler`` key
        alone.

        That context is the routing identity of whoever asked — ``source``,
        ``destination`` and the session carrier — and replaying it verbatim
        is what makes an alarm asked for on a remote device ring on that
        device rather than wherever the scheduler runs.

        Whether a session snapshot inside it is still current is a
        consumer's question, answered under the session lifecycle
        (SESSION-2 §2.5, §4.1) and not here.
        """
        record = schedule.record
        context = dict(record.get("context") or {})
        context["scheduler"] = {"id": record["id"],
                                "owner": record["owner"],
                                "due": format_instant(due),
                                "fired": format_instant(self.now()),
                                "remaining": schedule.remaining()}
        LOG.debug(f"scheduled event fired: {record['id']}")
        self.bus.emit(Message(record["event"], dict(record["data"]), context))
        schedule.record_fire(due)

    def _retire_if_spent(self, schedule: Schedule):
        """A one-shot is deleted once its occurrence has fired or been
        reported missed, and a recurrence once it has none left (§4.3).

        Only the schedule that fired is retired, which is why this compares
        the stored schedule and not just its identity. A handler that arms
        the next occurrence from inside the event it just received — ring,
        then set tomorrow's — has already put a different schedule under this
        identity by the time the batch ends, and so has a replacement that
        arrived from another thread while it ran. Retiring by identity alone
        deletes that schedule silently, and the owner has no way to learn
        that the request it made was undone.
        """
        if not (schedule.is_one_shot or
                schedule.next_after(schedule.cursor) is None):
            return
        with self.lock:
            if self.schedules.get(schedule.key) is schedule:
                del self.schedules[schedule.key]

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
        # only this scheduler's own subscriptions go; an unrelated observer on
        # the same topic is none of our business
        for topic, handler in self._subscriptions:
            self.bus.remove(topic, handler)
        if self.is_alive():
            self.join(30)
        self._persist()
        self._running.clear()


def _late_fires(policy: str, late: List[datetime]) -> List[datetime]:
    """Which missed occurrences the misfire policy puts on the bus (§4.3)."""
    if policy == "all":
        return list(late)
    if policy == "late":
        return late[-1:]
    return []


def _required_id(message: Message) -> str:
    schedule_id = message.data.get("id")
    if not isinstance(schedule_id, str) or not schedule_id:
        raise ScheduleError("invalid_record", "id is required")
    return schedule_id


def _unwritable_store(err: OSError) -> ScheduleError:
    return ScheduleError("internal",
                         f"the schedule store could not be written: {err}")
