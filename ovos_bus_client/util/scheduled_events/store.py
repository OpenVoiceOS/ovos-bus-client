"""The schedule store: the file schedules survive a restart in (§5).

The store lives in the assistant's state directory, not its configuration
directory. Its format is not part of the wire contract, but it carries the
records of §3.1 unchanged so that a replacement scheduler can read them.
"""
import json
import os
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from ovos_utils.log import LOG
from ovos_utils.xdg_utils import xdg_state_home
from ovos_config.meta import get_xdg_base

from ovos_bus_client.util.scheduled_events.records import (
    ScheduleError, format_instant, parse_instant)
from ovos_bus_client.util.scheduled_events.schedules import Schedule

#: bumped when the on-disk shape changes in a way a reader must know about.
#: A file written by a newer scheduler is not read: this one cannot know what
#: its entries mean. An older one is read, because every version so far only
#: added a field an older record simply does not have.
STORE_VERSION = 2

#: the oldest version whose records this scheduler still understands
OLDEST_READABLE_VERSION = 1


def default_store_path(filename: str = "schedule.json") -> str:
    """Where the store lives unless a deployment says otherwise."""
    return os.path.join(xdg_state_home(), get_xdg_base(), filename)


class ScheduleStore:
    """Reads and writes the schedule file.

    Every write is an atomic replace, so a crash at any point leaves either
    the previous file or the new one and never a partial write (§5.1).
    """

    def __init__(self, path: str):
        self.path = path
        #: the wall-clock time of the most recent write, which is one half of
        #: the newest-already-past instant of §7.2
        self.written_at: Optional[datetime] = None

    def load(self) -> Dict[Tuple[str, str], Schedule]:
        """Every schedule the file holds, keyed by identity.

        An unreadable entry is reported and skipped: a scheduler that refuses
        to start because one record went bad is worse than one that starts
        without it. A whole file this scheduler cannot use — damaged, or
        written by a version it does not read — is moved aside instead, so
        that starting without it does not destroy it.
        """
        content = self._read()
        if content is None:
            return {}
        version = content.get("version")
        if not self._readable(version):
            self._set_aside(version)
            return {}
        self.written_at = self._stored_write_time(content.get("written_at"))
        schedules = {}
        for entry in content.get("schedules", []):
            try:
                schedule = Schedule.from_stored(entry)
            except (ScheduleError, KeyError, TypeError) as err:
                LOG.error(f"dropping unreadable schedule: {err}")
                continue
            schedules[schedule.key] = schedule
        return schedules

    def save(self, schedules: Iterable[Schedule]):
        """Write every non-ephemeral schedule, atomically.

        Raises OSError when the store could not be written; the caller is
        expected to roll back rather than run on state the file disagrees
        with (§5.1).
        """
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        written_at = datetime.now(timezone.utc)
        content = {"version": STORE_VERSION,
                   "written_at": format_instant(written_at),
                   "schedules": self._persistable(schedules)}
        temporary = f"{self.path}.tmp"
        with open(temporary, "w") as handle:
            json.dump(content, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)
        self.written_at = written_at

    @staticmethod
    def _persistable(schedules: Iterable[Schedule]) -> List[dict]:
        """Ephemeral schedules are held in memory only (§5.3)."""
        entries = []
        for schedule in schedules:
            if not schedule.record["ephemeral"]:
                entries.append(schedule.as_stored())
        return entries

    def _read(self) -> Optional[dict]:
        if not os.path.isfile(self.path):
            return None
        try:
            with open(self.path) as handle:
                return json.load(handle)
        except Exception as err:
            self._quarantine(err)
            return None

    def _quarantine(self, err: Exception):
        """Move a store that will not parse out of the way.

        Starting empty means the next write replaces the file, and the
        damaged bytes are the only account of what the scheduler was holding.
        They are kept beside it under a name of their own, so that whoever
        comes to ask why the alarms are gone has something to read.
        """
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        quarantined = f"{self.path}.corrupt.{stamp}.bak"
        try:
            os.replace(self.path, quarantined)
        except OSError as unmovable:
            LOG.error(f"unreadable schedule store {self.path}: {err}; it could "
                      f"not be set aside either ({unmovable}), so the next "
                      f"write will replace it")
            return
        LOG.error(f"unreadable schedule store {self.path}: {err}; it was moved "
                  f"to {quarantined} and this scheduler starts with no "
                  f"schedules")

    @staticmethod
    def _readable(version) -> bool:
        """Whether a store of this version can be read.

        Older stores are read as they are. Every version so far has only
        added a field, so a record from an older one is a record with that
        field missing, and the next write brings the file up to date.
        """
        return (isinstance(version, int) and not isinstance(version, bool) and
                OLDEST_READABLE_VERSION <= version <= STORE_VERSION)

    def _set_aside(self, version):
        """Move a store this scheduler cannot read out of the way.

        Starting empty means the next write replaces the file, so a store from
        a version we do not understand is preserved beside it first: a
        downgrade must not cost the schedules the newer scheduler held.
        """
        backup = f"{self.path}.v{version}.bak"
        os.replace(self.path, backup)
        LOG.warning(f"schedule store {self.path} is version {version} and this "
                    f"scheduler reads versions {OLDEST_READABLE_VERSION} to "
                    f"{STORE_VERSION}; it was moved to {backup} and this "
                    f"scheduler starts with no schedules")

    @staticmethod
    def _stored_write_time(value) -> Optional[datetime]:
        if not value:
            return None
        try:
            return parse_instant(value, "written_at")
        except ScheduleError as err:
            LOG.warning(f"ignoring unreadable store timestamp: {err}")
            return None
