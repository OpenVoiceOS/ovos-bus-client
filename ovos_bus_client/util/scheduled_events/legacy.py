"""The pre-specification scheduling protocol, kept for one stable cycle.

Before SCHEDULER-1 a component scheduled work by emitting
``mycroft.scheduler.schedule_event`` with an epoch float and a name, and the
scheduler kept its store in the configuration directory. Both are still
served here, translated into records the specification understands, so that
components can move over one at a time.

Two behaviours differ from the protocol as it used to be implemented, because
the old ones cannot be expressed in a record:

* scheduling a name that already exists replaces it instead of appending a
  second entry, which is what stops a component that re-creates its schedules
  on every start from doubling them;
* ``mycroft.scheduler.get_event`` answers with an object rather than a bare
  list, because the wire refuses a list-shaped payload.

Everything else these owners relied on is kept, including the context they
scheduled with: the old scheduler stored it and emitted the fired event with
it, and components written against that still expect it. It is kept whole,
session included, which is what §3.5 asks of any schedule — so context is
the one thing this adapter does not have to translate.
"""
import json
import os
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from ovos_config.locations import get_xdg_config_save_path
from ovos_utils.log import LOG

from ovos_bus_client.message import Message
from ovos_bus_client.util.scheduled_events import topics
from ovos_bus_client.util.scheduled_events.records import (
    ScheduleError, format_instant)
from ovos_bus_client.util.scheduled_events.validation import validate_record
from ovos_bus_client.util.scheduled_events.schedules import Schedule
from ovos_bus_client.version import VERSION_MAJOR

#: the release these topics and their epoch-float API disappear in
LEGACY_REMOVAL_VERSION = f"{VERSION_MAJOR + 1}.0.0"

#: the owner of a legacy event name that carries no component id
UNNAMESPACED_OWNER = "legacy"


def legacy_owner(name: str) -> str:
    """The owner of a legacy event name.

    Legacy names are ``<skill_id>:<name>``; anything without the colon
    predates even that and belongs to no component in particular.
    """
    return name.split(":", 1)[0] if ":" in name else UNNAMESPACED_OWNER


def legacy_record(name: str, when: float, repeat: Optional[float],
                  data: Optional[dict],
                  context: Optional[dict] = None) -> dict:
    """Turn an epoch float and a name into a record.

    A repeat becomes a fixed-period recurrence anchored on the first
    occurrence. The event name is stored as it arrived: it predates the
    ``<owner>.<name>`` rule and rejecting it would break the components this
    adapter exists for.

    The context the request carried is stored with the record and replayed
    on every fire (§3.5), exactly as for a schedule made through the
    specified protocol.
    """
    instant = format_instant(datetime.fromtimestamp(when, timezone.utc))
    record = {"id": name, "owner": legacy_owner(name), "event": name,
              "data": data or {}}
    if repeat:
        record["every"] = {"seconds": repeat, "start": instant}
    else:
        record["at"] = instant
    record["context"] = context or {}
    return validate_record(record, namespaced=False)


def pending_migration_path() -> Optional[str]:
    """The configuration-directory store that has not been migrated yet."""
    path = os.path.join(get_xdg_config_save_path(), "schedule.json")
    if not os.path.isfile(path) or os.path.isfile(f"{path}.migrated"):
        return None
    return path


def read_migration_source(path: str) -> Dict[Tuple[str, str], Schedule]:
    """Read the configuration-directory store as schedules."""
    with open(path) as handle:
        content = json.load(handle)
    schedules = {}
    for name, entries in content.items():
        for entry in entries:
            payload = entry[2] if len(entry) > 2 else {}
            context = entry[3] if len(entry) > 3 else None
            record = legacy_record(name, entry[0], entry[1], payload, context)
            schedule = Schedule(record)
            schedules[schedule.key] = schedule
    return schedules


def mark_migrated(path: str):
    """Leave the original where it is, so a downgrade still finds it."""
    open(f"{path}.migrated", "w").close()


class LegacyAdapter:
    """Serves the ``mycroft.scheduler.*`` topics from the same schedules.

    It reaches into the scheduler for the store lock and the persistence it
    guards; everything it adds is translation.
    """

    def __init__(self, service):
        self.service = service
        self._warned_about = set()

    def subscriptions(self):
        """The topic and handler pairs the scheduler subscribes for us."""
        return ((topics.LEGACY_SCHEDULE, self.handle_schedule),
                (topics.LEGACY_REMOVE, self.handle_remove),
                (topics.LEGACY_UPDATE, self.handle_update),
                (topics.LEGACY_GET, self.handle_get),
                (topics.LEGACY_LIST, self.handle_list))

    def handle_schedule(self, message: Message):
        self._warn_once(message.msg_type)
        name = message.data.get("event")
        when = message.data.get("time")
        if not name or when is None:
            LOG.error("legacy schedule request is missing event or time")
            return
        try:
            record = legacy_record(name, when, message.data.get("repeat"),
                                   message.data.get("data"),
                                   message.context)
        except ScheduleError as err:
            LOG.error(f"legacy schedule request refused: {err.reason}")
            return
        self.service.replace_schedule(record)

    def handle_remove(self, message: Message):
        self._warn_once(message.msg_type)
        name = message.data.get("event")
        if name:
            self.service.drop_schedule(self._key(name))

    def handle_update(self, message: Message):
        self._warn_once(message.msg_type)
        name = message.data.get("event")
        if name:
            self.service.replace_payload(self._key(name),
                                         message.data.get("data") or {})

    def handle_get(self, message: Message):
        self._warn_once(message.msg_type)
        name = message.data.get("name")
        schedule = self.service.find_schedule(self._key(name or ""))
        entry = self._entry(schedule) if schedule else None
        self.service.bus.emit(message.reply(
            f"{topics.LEGACY_GET_REPLY_PREFIX}{name}",
            data={"event": name, "schedule": entry}))

    def handle_list(self, message: Message):
        self._warn_once(message.msg_type)
        listing = {schedule.record["id"]: [self._entry(schedule)]
                   for schedule in self.service.all_schedules()}
        self.service.bus.emit(
            message.response(data={"scheduled_events": listing}))

    def _key(self, name: str) -> Tuple[str, str]:
        return legacy_owner(name), name

    def _entry(self, schedule: Schedule) -> list:
        """One schedule in the shape the legacy protocol reads: next epoch
        time, repeat period, data, context."""
        upcoming = schedule.next_from_now(self.service.now())
        return [upcoming.timestamp() if upcoming else 0,
                schedule.record.get("every", {}).get("seconds"),
                schedule.record["data"], schedule.record.get("context") or {}]

    def _warn_once(self, topic: str):
        if topic not in self._warned_about:
            self._warned_about.add(topic)
            LOG.warning(f"{topic} is deprecated in favour of the "
                        f"{topics.SCHEDULER_SCHEDULE.rsplit('.', 1)[0]}.* "
                        f"topics and will be removed in ovos-bus-client "
                        f"{LEGACY_REMOVAL_VERSION}")
