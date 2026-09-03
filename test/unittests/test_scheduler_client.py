"""The scheduler client, talking to a real scheduler over an in-memory bus."""
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import TestCase

from ovos_utils.fakebus import FakeBus

from ovos_bus_client.apis.events import EventSchedulerInterface
from ovos_bus_client.message import Message
from ovos_bus_client.apis.scheduler import SchedulerClient, SchedulerError
from ovos_bus_client.util.scheduled_events import ScheduledEventService, topics


def _fired(event: str) -> Message:
    """A message shaped like one the scheduler fires."""
    return Message(event, {}, {"scheduler": {"id": "x"}})


class ClientTestCase(TestCase):
    def setUp(self):
        self.bus = FakeBus()
        handle, self.store = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        os.unlink(self.store)
        self.service = ScheduledEventService(self.bus, store_path=self.store,
                                             autostart=False)
        self.client = self.make_client("skill.a")

    def tearDown(self):
        self.service.shutdown()
        for path in (self.store, f"{self.store}.tmp"):
            if os.path.isfile(path):
                os.unlink(path)

    def make_client(self, skill_id):
        return SchedulerClient(bus=self.bus, skill_id=skill_id)

    def in_an_hour(self):
        return datetime.now(timezone.utc) + timedelta(hours=1)

    def just_now(self):
        """An instant already due, so the next evaluation fires it."""
        return datetime.now(timezone.utc) - timedelta(seconds=1)


class TestSchedulingAndReading(ClientTestCase):
    def test_schedule_returns_an_id_and_get_reads_it_back(self):
        schedule_id = self.client.schedule("ring", at=self.in_an_hour(),
                                           data={"k": 1})
        record = self.client.get(schedule_id)["record"]
        self.assertEqual(record["event"], "skill.a.ring")
        self.assertEqual(record["data"], {"k": 1})

    def test_get_returns_nothing_for_an_unknown_id(self):
        self.assertIsNone(self.client.get("never-scheduled"))

    def test_the_default_id_comes_from_the_event_name_alone(self):
        now = datetime.now(timezone.utc)
        ids = {self.client.schedule("ring", at=now + timedelta(hours=n))
               for n in range(1, 6)}
        # five calls for one event leave one schedule, whatever the timing
        self.assertEqual(ids, {"ring"})
        self.assertEqual(len(self.client.list()), 1)

    def test_the_default_id_does_not_depend_on_the_handler(self):
        when = self.in_an_hour()
        first = self.client.schedule("ring", handler=lambda m: None, at=when)
        second = self.client.schedule("ring", handler=lambda m: None, at=when)
        self.assertEqual(first, second)

    def test_an_explicit_id_keeps_several_schedules_for_one_event(self):
        now = datetime.now(timezone.utc)
        self.client.schedule("ring", at=now + timedelta(hours=1), schedule_id="a")
        self.client.schedule("ring", at=now + timedelta(hours=2), schedule_id="b")
        self.assertEqual({s["record"]["id"] for s in self.client.list()},
                         {"a", "b"})

    def test_a_changed_recurrence_replaces_rather_than_orphans(self):
        self.client.schedule("sync", every={"seconds": 600})
        self.client.schedule("sync", every={"seconds": 60})
        records = [s["record"] for s in self.client.list()]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["every"]["seconds"], 60)

    def test_a_wall_clock_rule_is_accepted(self):
        schedule_id = self.client.schedule(
            "wake", local={"time": "07:30", "zone": "Europe/Lisbon",
                           "days": ["mon", "fri"]})
        self.assertIsNotNone(self.client.get(schedule_id)["state"]["next"])

    def test_a_relative_delay_is_accepted(self):
        schedule_id = self.client.schedule("ring", in_seconds=300)
        self.assertEqual(self.client.get(schedule_id)["record"]["in"],
                         {"seconds": 300})

    def test_list_shows_only_this_components_schedules(self):
        when = self.in_an_hour()
        self.client.schedule("ring", at=when)
        self.make_client("skill.b").schedule("ring", at=when)
        self.assertEqual([s["record"]["owner"] for s in self.client.list()],
                         ["skill.a"])


class TestNamedOptions(ClientTestCase):
    """misfire, grace_s and ephemeral are parameters, not loose keywords."""

    def test_the_policies_reach_the_record(self):
        schedule_id = self.client.schedule("ring", at=self.in_an_hour(),
                                           misfire="skip", grace_s=5,
                                           ephemeral=True)
        record = self.client.get(schedule_id)["record"]
        self.assertEqual(record["misfire"], "skip")
        self.assertEqual(record["grace_s"], 5)
        self.assertTrue(record["ephemeral"])

    def test_the_defaults_are_the_specifications_own(self):
        record = self.client.get(
            self.client.schedule("ring", at=self.in_an_hour()))["record"]
        self.assertEqual(record["misfire"], "late")
        self.assertEqual(record["grace_s"], 60)
        self.assertFalse(record["ephemeral"])

    def test_a_recurrence_can_be_bounded_by_count_and_until(self):
        schedule_id = self.client.schedule("tick", every={"seconds": 60},
                                           count=3, until=self.in_an_hour())
        record = self.client.get(schedule_id)["record"]
        self.assertEqual(record["count"], 3)
        self.assertIsNotNone(record["until"])

    def test_an_unknown_option_is_a_type_error_rather_than_a_wire_field(self):
        with self.assertRaises(TypeError):
            self.client.schedule("ring", at=self.in_an_hour(), nonsense=True)


class TestTimeZones(ClientTestCase):
    def test_a_naive_datetime_is_refused_without_a_zone(self):
        with self.assertRaises(ValueError):
            self.client.schedule("ring", at=datetime.now() + timedelta(hours=1))

    def test_a_naive_datetime_is_accepted_with_an_explicit_zone(self):
        schedule_id = self.client.schedule(
            "ring", zone="Europe/Lisbon", at=datetime.now() + timedelta(hours=1))
        self.assertIsNotNone(self.client.get(schedule_id))

    def test_something_that_is_not_a_datetime_is_a_type_error(self):
        with self.assertRaises(TypeError):
            self.client.schedule("ring", at="in an hour")

    def test_exactly_one_timing_is_required(self):
        with self.assertRaises(ValueError):
            self.client.schedule("ring")
        with self.assertRaises(ValueError):
            self.client.schedule("ring", at=self.in_an_hour(), in_seconds=5)


class TestHandlerOwnership(ClientTestCase):
    """The client owns the subscription it makes for a schedule (§8)."""

    def test_the_handler_is_registered_before_the_request_goes_out(self):
        seen = []
        self.client.schedule("ring", handler=seen.append,
                             at=datetime.now(timezone.utc) - timedelta(seconds=1))
        self.service._evaluate()
        self.assertEqual(len(seen), 1)

    def test_rescheduling_replaces_the_handler_instead_of_stacking_one(self):
        first, second = [], []
        self.client.schedule("tick", handler=first.append,
                             every={"seconds": 10})
        self.client.schedule("tick", handler=second.append,
                             every={"seconds": 10})
        self.bus.emit(_fired("skill.a.tick"))
        self.assertEqual(len(first), 0)
        self.assertEqual(len(second), 1)

    def test_cancel_removes_the_handler_schedule_registered(self):
        seen = []
        schedule_id = self.client.schedule("ring", handler=seen.append,
                                           at=self.in_an_hour())
        self.client.cancel(schedule_id)
        self.bus.emit(_fired("skill.a.ring"))
        self.assertEqual(seen, [])

    def test_cancel_reports_whether_the_schedule_existed(self):
        schedule_id = self.client.schedule("ring", at=self.in_an_hour())
        self.assertTrue(self.client.cancel(schedule_id))
        self.assertFalse(self.client.cancel(schedule_id))

    def test_a_schedule_without_a_handler_leaves_the_previous_one_alone(self):
        seen = []
        self.client.schedule("tick", handler=seen.append, every={"seconds": 10})
        self.client.schedule("tick", every={"seconds": 10})
        self.bus.emit(_fired("skill.a.tick"))
        self.assertEqual(len(seen), 1)


class TestRequestContext(ClientTestCase):
    def test_the_callers_message_is_not_stamped_in_place(self):
        # skill_id is what the scheduler reads as the caller's identity, so
        # leaving ours on the handler's own message would hand it to every
        # message that handler forwards afterwards
        contexts = []

        def handler(message):
            self.client.schedule("ring", at=self.in_an_hour())
            contexts.append(dict(message.context))

        self.bus.on("some.request", handler)
        self.bus.emit(Message("some.request", {}, {"source": ["cli"]}))
        self.assertNotIn("skill_id", contexts[-1])

    def test_the_request_still_carries_the_owners_identity(self):
        seen = []
        self.bus.on(topics.SCHEDULER_SCHEDULE, seen.append)
        self.client.schedule("ring", at=self.in_an_hour())
        self.assertEqual(seen[-1].context["skill_id"], "skill.a")


class TestPresence(ClientTestCase):
    """Asking whether there is a scheduler before talking to one."""

    def test_a_running_scheduler_is_found(self):
        self.assertTrue(self.client.is_available())

    def test_no_scheduler_on_the_bus_is_reported_quickly(self):
        self.service.shutdown()
        lonely = SchedulerClient(bus=FakeBus(), skill_id="skill.a")
        started = time.monotonic()
        self.assertFalse(lonely.is_available(timeout=0.2))
        self.assertLess(time.monotonic() - started, 2.0)

    def test_the_answer_is_asked_for_once_and_remembered(self):
        asked = []
        self.bus.on(topics.SCHEDULER_LIST, lambda m: asked.append(m))
        self.client.is_available()
        self.client.is_available()
        self.assertEqual(len(asked), 1)


class TestRescheduling(ClientTestCase):
    """Changing part of a schedule and leaving the rest of it alone."""

    def test_changing_the_payload_keeps_the_time(self):
        when = self.in_an_hour()
        self.client.schedule("ring", at=when, data={"k": 1})
        self.client.reschedule("ring", data={"k": 2})
        record = self.client.get("ring")["record"]
        self.assertEqual(record["data"], {"k": 2})
        self.assertEqual(record["at"], when.isoformat())

    def test_changing_the_payload_of_a_period_keeps_its_anchor(self):
        self.client.schedule("tick", every={"seconds": 300}, data={"k": 1})
        anchor = self.client.get("tick")["record"]["every"]["start"]
        self.client.reschedule("tick", data={"k": 2})
        record = self.client.get("tick")["record"]
        self.assertEqual(record["every"], {"seconds": 300, "start": anchor})
        self.assertEqual(record["data"], {"k": 2})

    def test_a_delay_keeps_the_instant_it_was_counting_down_to(self):
        self.client.schedule("ring", in_seconds=3600)
        upcoming = self.client.get("ring")["state"]["next"]
        self.client.reschedule("ring", data={"k": 1})
        # the delay does not start over: what was due in an hour still is
        self.assertEqual(self.client.get("ring")["record"]["at"], upcoming)

    def test_the_handler_stays_subscribed(self):
        seen = []
        self.client.schedule("ring", seen.append, at=self.just_now())
        self.client.reschedule("ring", data={"k": 1})
        self.service._evaluate()
        self.assertEqual([message.data for message in seen], [{"k": 1}])

    def test_a_new_handler_replaces_the_old_one(self):
        seen = []
        self.client.schedule("ring", lambda m: seen.append("first"),
                             at=self.just_now())
        self.client.reschedule("ring", handler=lambda m: seen.append("second"))
        self.service._evaluate()
        self.assertEqual(seen, ["second"])

    def test_a_new_timing_replaces_the_old_one(self):
        self.client.schedule("ring", at=self.in_an_hour())
        self.client.reschedule("ring", every={"seconds": 300})
        record = self.client.get("ring")["record"]
        self.assertNotIn("at", record)
        self.assertEqual(record["every"]["seconds"], 300)

    def test_the_bounds_and_policies_are_carried_over(self):
        self.client.schedule("tick", every={"seconds": 300}, count=5,
                             until=self.in_an_hour(), misfire="skip",
                             grace_s=12, ephemeral=True)
        self.client.reschedule("tick", data={"k": 1})
        record = self.client.get("tick")["record"]
        self.assertEqual(record["count"], 5)
        self.assertEqual(record["misfire"], "skip")
        self.assertEqual(record["grace_s"], 12)
        self.assertTrue(record["ephemeral"])
        self.assertIn("until", record)

    def test_the_event_name_is_carried_over(self):
        self.client.schedule("ring", at=self.in_an_hour())
        self.client.reschedule("ring", data={"k": 1})
        self.assertEqual(self.client.get("ring")["record"]["event"],
                         "skill.a.ring")

    def test_rescheduling_something_that_is_not_there_is_an_error(self):
        with self.assertRaises(SchedulerError) as raised:
            self.client.reschedule("never-scheduled", data={"k": 1})
        self.assertEqual(raised.exception.error, "not_found")

    def test_another_component_cannot_reschedule_this_one(self):
        self.client.schedule("ring", at=self.in_an_hour(), data={"k": 1})
        with self.assertRaises(SchedulerError):
            self.make_client("skill.b").reschedule("ring", data={"k": 2})
        self.assertEqual(self.client.get("ring")["record"]["data"], {"k": 1})


class TestContextRoundTrip(ClientTestCase):
    """What a component schedules with is what its handler is called with."""

    def handling(self, context: dict):
        """A message in flight, as a handler would have one."""
        return Message("some.request", {}, dict(context))

    def test_the_handler_is_called_with_the_context_of_the_request(self):
        seen = []
        given = {"session": {"session_id": "abc", "lang": "pt-pt"},
                 "source": "audio:0", "destination": ["skill.a"]}

        def schedule_from_a_handler(message):
            self.client.schedule("ring", seen.append, at=self.just_now())

        schedule_from_a_handler(self.handling(given))
        self.service._evaluate()
        for field, value in given.items():
            self.assertEqual(seen[0].context[field], value)
        self.assertEqual(seen[0].context["skill_id"], "skill.a")
        self.assertEqual(seen[0].context["scheduler"]["id"], "ring")

    def test_a_schedule_made_outside_a_handler_carries_only_its_owner(self):
        seen = []
        self.client.schedule("ring", seen.append, at=self.just_now())
        self.service._evaluate()
        self.assertEqual(set(seen[0].context) - {"session"},
                         {"skill_id", "scheduler"})


class TestGivenContext(ClientTestCase):
    """A component that says which context an occurrence belongs to."""

    def test_a_given_context_is_what_fires(self):
        seen = []

        def schedule_from_a_handler(message):
            self.client.schedule("ring", seen.append, at=self.just_now(),
                                 context={"mine": True})

        schedule_from_a_handler(Message("some.request", {},
                                        {"theirs": True}))
        self.service._evaluate()
        self.assertTrue(seen[0].context["mine"])
        self.assertNotIn("theirs", seen[0].context)

    def test_a_given_context_survives_a_restart(self):
        self.client.schedule("ring", at=self.in_an_hour(),
                             context={"mine": True})
        self.assertTrue(
            self.client.get("ring")["record"]["context"]["mine"])

    def test_rescheduling_keeps_the_context_it_was_created_with(self):
        self.client.schedule("ring", at=self.in_an_hour(),
                             context={"mine": True})
        self.client.reschedule("ring", data={"k": 1})
        self.assertTrue(
            self.client.get("ring")["record"]["context"]["mine"])


class TestRefusals(ClientTestCase):
    def test_a_refusal_reaches_the_caller_as_an_error(self):
        with self.assertRaises(SchedulerError) as caught:
            self.client.schedule("ring", at=datetime.now(timezone.utc),
                                 data={"blob": "x" * 20000})
        self.assertEqual(caught.exception.error, "payload_too_large")

    def test_a_missing_answer_is_surfaced_as_a_timeout(self):
        self.service.shutdown()
        with self.assertRaises(SchedulerError) as caught:
            self.client._request(topics.SCHEDULER_LIST, {}, timeout=0.2)
        self.assertEqual(caught.exception.error, "timeout")


class TestEventSchedulerInterface(ClientTestCase):
    """The skill-facing interface is the client plus the legacy methods."""

    def make_client(self, skill_id):
        return EventSchedulerInterface(bus=self.bus, skill_id=skill_id)

    def test_it_speaks_the_specification(self):
        schedule_id = self.client.schedule("ring", at=self.in_an_hour())
        self.assertEqual(self.client.get(schedule_id)["record"]["event"],
                         "skill.a.ring")

    def test_schedule_event_with_a_delta_in_seconds(self):
        with self.assertWarns(DeprecationWarning):
            self.client.schedule_event(lambda m: None, when=3600, name="t")
        self.assertIn(("skill.a", "skill.a:t"), self.service.schedules)

    def test_schedule_event_with_a_datetime(self):
        with self.assertWarns(DeprecationWarning):
            self.client.schedule_event(lambda m: None, when=self.in_an_hour(),
                                       name="t")
        self.assertIn(("skill.a", "skill.a:t"), self.service.schedules)

    def test_a_repeating_event_becomes_a_recurrence(self):
        with self.assertWarns(DeprecationWarning):
            self.client.schedule_repeating_event(lambda m: None, when=None,
                                                 interval=60, name="tick")
        record = self.service.schedules["skill.a", "skill.a:tick"].record
        self.assertEqual(record["every"]["seconds"], 60)

    def test_cancel_scheduled_event_removes_the_schedule(self):
        with self.assertWarns(DeprecationWarning):
            self.client.schedule_event(lambda m: None, when=3600, name="t")
            self.client.cancel_scheduled_event("t")
        self.assertEqual(self.service.schedules, {})

    def test_get_scheduled_event_status_returns_the_seconds_left(self):
        with self.assertWarns(DeprecationWarning):
            self.client.schedule_event(lambda m: None, when=3600, name="t")
            self.assertGreater(self.client.get_scheduled_event_status("t"), 3500)

    def test_a_scheduled_handler_still_runs_on_its_event(self):
        seen = []
        with self.assertWarns(DeprecationWarning):
            self.client.schedule_event(seen.append, when=0, name="t")
        self.service._evaluate()
        self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
