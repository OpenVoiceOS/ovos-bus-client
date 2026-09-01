# Scheduled events

The scheduler fires a named event on the bus at a wall-clock instant, once
or on a recurrence, on behalf of a component. It is the clock that alarms,
timers, reminders and housekeeping jobs run on; the component that owns the
schedule owns the meaning of the event, and the scheduler owns only the
timing.

The service is `ovos_bus_client.util.scheduled_events.ScheduledEventService`,
also importable as `EventScheduler` from `ovos_bus_client.util.scheduler`,
which is the name `ovos-core` starts it by. The component-facing client is
`EventSchedulerInterface` in `ovos_bus_client.apis.events`. Both implement
SCHEDULER-1; this page is the practical guide, the specification is the
contract.

## Scheduling from a component

```python
from datetime import datetime, timedelta, timezone
from ovos_bus_client.apis.events import EventSchedulerInterface

events = EventSchedulerInterface(bus=bus, skill_id="my.skill")

schedule_id = events.schedule(
    "wakeup",
    handler=self.handle_wakeup,
    at=datetime.now(timezone.utc) + timedelta(minutes=5),
    data={"alarm": "kitchen"},
)
```

`schedule` namespaces the event to the component, so the message that
eventually reaches the bus is `my.skill.wakeup`. It registers the handler
before it sends the request, so an occurrence that is due immediately is
not lost, and it waits for the scheduler's answer: a refusal is raised as
`SchedulerError` carrying the wire error code, never swallowed.

`at` must be a time-zone-aware datetime. A naive one is refused unless you
pass `zone="Europe/Lisbon"` to say which zone to read it in — the process's
local zone is never assumed on your behalf, because that is how an alarm
ends up an hour out after a daylight-saving change.

The other three timing forms are a fixed period, a wall-clock recurrence,
and a relative delay:

```python
events.schedule("sync", every={"seconds": 3600})
events.schedule("wake", local={"time": "07:30", "zone": "Europe/Lisbon",
                               "days": ["mon", "tue", "wed", "thu", "fri"]})
events.schedule("timer", in_seconds=300)
```

A fixed period is anchored on the schedule, not on the last fire, so a late
fire does not make the whole series drift. A `local` recurrence is evaluated
in the zone you named: when a spring-forward gap means the wall clock never
reads your time, the occurrence is the first instant after the gap; when a
fall-back overlap means it reads it twice, the occurrence is the first of
the two. A relative delay runs off the monotonic clock while the service is
up, so a clock correction cannot make it fire early or late.

Bound a recurrence with `until` (an instant) or `count` (a number of
occurrences). Mark a schedule `ephemeral=True` when it is meaningless
outside your process; anything else outlives your restarts and the
scheduler's.

## Identity and replacement

A schedule is identified by the pair (owner, id). Scheduling the same
identity again replaces it atomically — there is no update request, and
there are no duplicates. If you do not pass `schedule_id`, the client
derives one from the event name alone. That means a component keeps one
schedule per event: calling `schedule` again for the same event replaces it
whatever the new timing is, so re-creating your schedules on start is the
intended pattern and costs nothing. When one event genuinely needs several
schedules — three alarms all firing `my.skill.alarm` — give each an explicit
`schedule_id`, or the second will replace the first.

An administrative component can read or cancel across every owner by sending
`owner: "*"`, but only when its component id appears in the `scheduler.admins`
allowlist in the configuration, which is empty by default. No schedule can be
created under `*`. The allowlist is a misconfiguration guard, not a security
boundary: on an unauthenticated bus any process can claim any component id, so
it stops an honest component reaching across owners by accident and nothing
more.

Read them back with `events.get(schedule_id)` and `events.list()`. Both
return the stored record alongside computed state: the next occurrence, the
due instant of the last fire, the dues missed since that fire, and how many
occurrences are left. Cancel with `events.cancel(schedule_id)`, which tells
you whether the schedule existed.

## The fired event

The message the scheduler emits is a new message originating at the
scheduler. Its `data` is the record's `data`, and its context carries only

```python
message.context["scheduler"]  # {"id", "owner", "due", "fired", "remaining"}
```

Nothing of the request that created the schedule is replayed — in
particular no session identifier, which would be long dead by the time an
alarm rings. Put whatever you need at fire time into `data`.

## What happens when an occurrence is missed

An occurrence is missed when it was due while the scheduler was down, or
when the fire lands later than `grace_s` after the due instant. The
record's `misfire` field decides what the scheduler does with it: `late`
(the default) fires the most recent missed occurrence and drops the older
ones, `skip` fires none, and `all` fires every one of them oldest first.

Either way the scheduler emits `scheduler.missed` for the schedule, listing
the dues that were missed and the ones it fired late. Treat it as the
signal that something did not happen on time and decide what to tell the
user. After a restart the scheduler reports every occurrence it produces,
including one that may already have reached the bus in the instant before a
crash, so deduplicate on the pair (id, due).

When replay finishes the scheduler emits `scheduler.ready` with the number
of schedules it restored, the number that had missed occurrences, and
whether the clock is trusted yet. A device without a battery-backed clock
starts with a wall clock behind the newest instant its store recorded as
already past; in that state the scheduler still accepts and persists
requests but does not evaluate them, and it replays once the clock catches
up or `system.clock.synced` arrives.

## The store

Schedules live in `schedule.json` in the XDG state directory. Every accepted
change is written with an atomic replace before the response goes out, and
the due of each fired occurrence is written immediately after that event and
before the next one leaves, so a crash leaves either the old state or the new
one. Delivery is therefore at least once: a kill in the window between an
event reaching the bus and its due reaching the store repeats that one
occurrence on the next start, and nothing earlier. Handlers for a schedule
that matters should be idempotent on the pair (id, due).

A store left in the configuration directory by an older release is copied to
the state directory on first start. The original is left where it is, so a
downgrade still finds it; the copy is marked so it is not repeated.

## The legacy protocol

The `mycroft.scheduler.*` topics and the `schedule_event`,
`schedule_repeating_event`, `update_scheduled_event`,
`cancel_scheduled_event` and `get_scheduled_event_status` client methods
still work and are answered by the same service, which maps an epoch float
to an instant, `repeat` to a fixed period, and the `skill_id:` prefix of the
event name to an owner. They emit a deprecation notice naming the release
that drops them. New code uses the methods above.
