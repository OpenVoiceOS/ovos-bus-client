"""A stored record plus the state the scheduler keeps alongside it."""
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from ovos_bus_client.util.scheduled_events.records import (
    format_instant, parse_instant)
from ovos_bus_client.util.scheduled_events.timing import next_occurrence

#: instants reported in one missed message, and the length of the missed
#: list a schedule carries in its state (§4.3)
MAX_REPORTED = 100


class Schedule:
    """One schedule as the scheduler holds it.

    ``cursor`` is the instant up to and including which occurrences have been
    consumed, and ``last_fired`` the due instant of the most recent occurrence
    that reached the bus. Together they are the whole of the no-double-fire
    guarantee of §5.1: an occurrence is only ever produced strictly after the
    cursor, and never at or before ``last_fired``.

    ``consumed`` counts occurrences as they are produced or dropped, which is
    what ``count`` bounds; a misfire the policy discards is consumed all the
    same (§4.3).
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
        #: a relative delay runs off the monotonic ``deadline`` while the
        #: service is up; ``estimate`` is the wall-clock projection that
        #: survives a restart (§3.4.3)
        self.estimate = estimate
        self.deadline = deadline
        if "in" in record and self.estimate is None:
            seconds = record["in"]["seconds"]
            self.estimate = datetime.now(timezone.utc) + timedelta(seconds=seconds)
            self.deadline = time.monotonic() + seconds
        self.cursor = cursor if cursor is not None else self._first_cursor()

    @property
    def key(self) -> Tuple[str, str]:
        """The (owner, id) pair a schedule is identified by (§3.3)."""
        return self.record["owner"], self.record["id"]

    @property
    def is_one_shot(self) -> bool:
        return "at" in self.record or "in" in self.record

    def _first_cursor(self) -> datetime:
        """Where a brand new schedule starts looking.

        One second before its first possible occurrence, so that an
        occurrence due at that very instant is still ahead of the cursor.
        """
        margin = timedelta(seconds=1)
        if "at" in self.record:
            return parse_instant(self.record["at"], "at") - margin
        if "in" in self.record:
            return datetime.now(timezone.utc) - margin
        if "every" in self.record:
            return parse_instant(self.record["every"]["start"], "every.start") - margin
        return datetime.now(timezone.utc)

    def anchor(self, now: datetime):
        """Pin a recurrence that was created while the clock was unset.

        A period created before the device knew the time was anchored on a
        meaningless instant; once the clock is trustworthy the anchor is the
        moment of the first evaluation instead (§7.2).
        """
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
        """Project a relative delay onto the wall clock.

        A step of the wall clock moves the estimate, never the delay the
        owner asked for (§3.4.3).
        """
        if self.deadline is None:
            return
        left = self.deadline - time.monotonic()
        self.estimate = now + timedelta(seconds=max(left, 0.0))

    def next_after(self, after: datetime,
                   consumed: Optional[int] = None) -> Optional[datetime]:
        """The next occurrence strictly after ``after``, or None.

        ``consumed`` overrides the schedule's own tally, so that a caller can
        walk a backlog of occurrences without spending them.
        """
        if consumed is None:
            consumed = self.consumed
        if "count" in self.record and consumed >= self.record["count"]:
            return None
        if self.last_fired is not None and after < self.last_fired:
            after = self.last_fired
        occurrence = next_occurrence(self.record, after, self.estimate)
        if occurrence is None:
            return None
        if "until" in self.record and \
                occurrence > parse_instant(self.record["until"], "until"):
            return None
        return occurrence

    def next_from_now(self, now: datetime) -> Optional[datetime]:
        """The next occurrence a reader of this schedule should expect."""
        return self.next_after(max(self.cursor, now))

    def remaining(self) -> Optional[int]:
        """Occurrences left when the schedule is bounded, else None."""
        if self.is_one_shot:
            return 0
        if "count" in self.record:
            return max(0, self.record["count"] - self.consumed)
        return None

    def record_fire(self, due: datetime):
        """Note that the occurrence ``due`` reached the bus."""
        if self.last_fired is None or due > self.last_fired:
            self.last_fired = due

    def remember_missed(self, dues: List[datetime], since: Optional[datetime]):
        """Keep the missed instants a reader still needs to know about.

        ``since`` is the due instant of the fire that just happened, if there
        was one: everything it covers is no longer outstanding (§4.1).
        """
        if since is None:
            self.missed.extend(format_instant(due) for due in dues)
        else:
            self.missed = [format_instant(due) for due in dues if due > since]
        del self.missed[:-MAX_REPORTED]

    def state(self, now: datetime) -> dict:
        """The computed state a get or list answer carries (§4.1)."""
        upcoming = self.next_from_now(now)
        return {"next": format_instant(upcoming) if upcoming else None,
                "last_fired": format_instant(self.last_fired) if self.last_fired else None,
                "missed": list(self.missed),
                "remaining": self.remaining()}

    def as_stored(self) -> dict:
        """This schedule as one entry of the store file."""
        return {"record": self.record,
                "cursor": format_instant(self.cursor),
                "consumed": self.consumed,
                "anchored": self.anchored,
                "missed": list(self.missed),
                "estimate": format_instant(self.estimate) if self.estimate else None,
                "last_fired": format_instant(self.last_fired) if self.last_fired else None}

    @classmethod
    def from_stored(cls, entry: dict) -> "Schedule":
        """Rebuild a schedule from one entry of the store file.

        A relative delay comes back without its monotonic deadline: across a
        restart it is an ``at`` on the estimate that was written (§3.4.3).
        """
        last_fired = entry.get("last_fired")
        estimate = entry.get("estimate")
        return cls(entry["record"],
                   cursor=parse_instant(entry["cursor"], "cursor"),
                   consumed=entry.get("consumed", 0),
                   anchored=entry.get("anchored", True),
                   last_fired=parse_instant(last_fired, "last_fired") if last_fired else None,
                   missed=entry.get("missed"),
                   estimate=parse_instant(estimate, "estimate") if estimate else None)
