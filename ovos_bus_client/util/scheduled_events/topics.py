"""Bus topics of the scheduler protocol (SCHEDULER-1 §4).

These names mirror the ``SCHEDULER_*`` constants of
``ovos_spec_tools.messages.SpecMessage``, which is where the platform keeps
its message registry. They are defined here only until that registry ships
them; when it does, this module becomes a re-export and no call site has to
change, because nothing outside this module writes a topic literal.
"""

SCHEDULER_SCHEDULE = "ovos.scheduler.schedule"
SCHEDULER_SCHEDULE_RESPONSE = "ovos.scheduler.schedule.response"
SCHEDULER_CANCEL = "ovos.scheduler.cancel"
SCHEDULER_CANCEL_RESPONSE = "ovos.scheduler.cancel.response"
SCHEDULER_GET = "ovos.scheduler.get"
SCHEDULER_GET_RESPONSE = "ovos.scheduler.get.response"
SCHEDULER_LIST = "ovos.scheduler.list"
SCHEDULER_LIST_RESPONSE = "ovos.scheduler.list.response"
SCHEDULER_READY = "ovos.scheduler.ready"
SCHEDULER_MISSED = "ovos.scheduler.missed"

#: emitted by the platform when the device reaches a time source
CLOCK_SYNCED = "system.clock.synced"

#: the pre-specification protocol, kept for one stable cycle
LEGACY_SCHEDULE = "mycroft.scheduler.schedule_event"
LEGACY_REMOVE = "mycroft.scheduler.remove_event"
LEGACY_UPDATE = "mycroft.scheduler.update_event"
LEGACY_GET = "mycroft.scheduler.get_event"
LEGACY_LIST = "mycroft.scheduler.list_events"
LEGACY_GET_REPLY_PREFIX = "mycroft.event_status.callback."
