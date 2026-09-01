"""The SCHEDULER-1 client methods on ``EventSchedulerInterface``."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import TestCase

from ovos_utils.fakebus import FakeBus

from ovos_bus_client.apis.events import EventSchedulerInterface, SchedulerError
from ovos_bus_client.message import Message
from ovos_bus_client.util.scheduled_events import ScheduledEventService


class TestSchedulerClient(TestCase):
    """The client talks to a real service over an in-memory bus."""

    def setUp(self):
        self.bus = FakeBus()
        handle, self.store = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        os.unlink(self.store)
        self.service = ScheduledEventService(self.bus, store_path=self.store,
                                             autostart=False)
        self.api = EventSchedulerInterface(bus=self.bus, skill_id="skill.a")

    def tearDown(self):
        self.service.shutdown()
        for path in (self.store, f"{self.store}.tmp"):
            if os.path.isfile(path):
                os.unlink(path)

    def test_schedule_returns_an_id_and_get_reads_it_back(self):
        when = datetime.now(timezone.utc) + timedelta(hours=1)
        schedule_id = self.api.schedule("ring", at=when, data={"k": 1})
        record = self.api.get(schedule_id)["record"]
        self.assertEqual(record["event"], "skill.a.ring")
        self.assertEqual(record["data"], {"k": 1})

    def test_the_default_id_comes_from_the_event_name_alone(self):
        now = datetime.now(timezone.utc)
        ids = {self.api.schedule("ring", at=now + timedelta(hours=n))
               for n in range(1, 6)}
        # five calls for one event leave one schedule, whatever the timing
        self.assertEqual(ids, {"ring"})
        self.assertEqual(len(self.api.list()), 1)

    def test_a_changed_recurrence_replaces_rather_than_orphans(self):
        self.api.schedule("sync", every={"seconds": 600})
        self.api.schedule("sync", every={"seconds": 60})
        records = [s["record"] for s in self.api.list()]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["every"]["seconds"], 60)

    def test_the_default_id_does_not_depend_on_the_handler(self):
        when = datetime.now(timezone.utc) + timedelta(hours=1)
        first = self.api.schedule("ring", handler=lambda m: None, at=when)
        second = self.api.schedule("ring", handler=lambda m: None, at=when)
        self.assertEqual(first, second)

    def test_an_explicit_id_keeps_several_schedules_for_one_event(self):
        now = datetime.now(timezone.utc)
        self.api.schedule("ring", at=now + timedelta(hours=1), schedule_id="a")
        self.api.schedule("ring", at=now + timedelta(hours=2), schedule_id="b")
        self.assertEqual({s["record"]["id"] for s in self.api.list()}, {"a", "b"})

    def test_a_naive_datetime_is_refused_without_a_zone(self):
        with self.assertRaises(ValueError):
            self.api.schedule("ring", at=datetime.now() + timedelta(hours=1))

    def test_a_naive_datetime_is_accepted_with_an_explicit_zone(self):
        schedule_id = self.api.schedule("ring", zone="Europe/Lisbon",
                                        at=datetime.now() + timedelta(hours=1))
        self.assertIsNotNone(self.api.get(schedule_id))

    def test_a_refusal_reaches_the_caller_as_an_error(self):
        with self.assertRaises(SchedulerError) as caught:
            self.api.schedule("ring", at=datetime.now(timezone.utc),
                              data={"blob": "x" * 20000})
        self.assertEqual(caught.exception.error, "payload_too_large")

    def test_the_handler_is_registered_before_the_request_goes_out(self):
        seen = []
        self.api.schedule("ring", handler=lambda m: seen.append(m),
                          at=datetime.now(timezone.utc) - timedelta(seconds=1))
        self.service._evaluate()
        self.assertEqual(len(seen), 1)

    def test_cancel_reports_whether_the_schedule_existed(self):
        schedule_id = self.api.schedule(
            "ring", at=datetime.now(timezone.utc) + timedelta(hours=1))
        self.assertTrue(self.api.cancel(schedule_id))
        self.assertFalse(self.api.cancel(schedule_id))

    def test_list_shows_only_this_components_schedules(self):
        when = datetime.now(timezone.utc) + timedelta(hours=1)
        self.api.schedule("ring", at=when)
        other = EventSchedulerInterface(bus=self.bus, skill_id="skill.b")
        other.schedule("ring", at=when)
        self.assertEqual(
            [s["record"]["owner"] for s in self.api.list()], ["skill.a"])

    def test_a_recurrence_can_be_created_from_a_local_rule(self):
        schedule_id = self.api.schedule(
            "wake", local={"time": "07:30", "zone": "Europe/Lisbon",
                           "days": ["mon", "fri"]})
        state = self.api.get(schedule_id)["state"]
        self.assertIsNotNone(state["next"])

    def test_a_relative_delay_is_accepted(self):
        schedule_id = self.api.schedule("ring", in_seconds=300)
        self.assertEqual(self.api.get(schedule_id)["record"]["in"],
                         {"seconds": 300})

    def test_a_timeout_is_surfaced_as_an_error(self):
        self.service.shutdown()
        with self.assertRaises(SchedulerError):
            self.api._request("scheduler.list", {}, timeout=0.2)


class TestLegacyClientMethods(TestCase):
    """Today's usage patterns keep working, with a deprecation notice."""

    def setUp(self):
        self.bus = FakeBus()
        handle, self.store = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        os.unlink(self.store)
        self.service = ScheduledEventService(self.bus, store_path=self.store,
                                             autostart=False)
        self.api = EventSchedulerInterface(bus=self.bus, skill_id="skill.a")

    def tearDown(self):
        self.service.shutdown()
        for path in (self.store, f"{self.store}.tmp"):
            if os.path.isfile(path):
                os.unlink(path)

    def test_schedule_event_with_a_delta_in_seconds(self):
        with self.assertWarns(DeprecationWarning):
            self.api.schedule_event(lambda m: None, when=3600, name="t")
        self.assertIn(("skill.a", "skill.a:t"), self.service.schedules)

    def test_schedule_event_with_a_datetime(self):
        when = datetime.now(timezone.utc) + timedelta(hours=1)
        with self.assertWarns(DeprecationWarning):
            self.api.schedule_event(lambda m: None, when=when, name="t")
        self.assertIn(("skill.a", "skill.a:t"), self.service.schedules)

    def test_a_repeating_event_becomes_a_recurrence(self):
        with self.assertWarns(DeprecationWarning):
            self.api.schedule_repeating_event(lambda m: None, when=None,
                                              interval=60, name="tick")
        record = self.service.schedules["skill.a", "skill.a:tick"].record
        self.assertEqual(record["every"]["seconds"], 60)

    def test_cancel_scheduled_event_removes_the_schedule(self):
        with self.assertWarns(DeprecationWarning):
            self.api.schedule_event(lambda m: None, when=3600, name="t")
            self.api.cancel_scheduled_event("t")
        self.assertEqual(self.service.schedules, {})

    def test_get_scheduled_event_status_returns_the_seconds_left(self):
        with self.assertWarns(DeprecationWarning):
            self.api.schedule_event(lambda m: None, when=3600, name="t")
        left = self.api.get_scheduled_event_status("t")
        self.assertGreater(left, 3500)

    def test_a_scheduled_handler_still_runs_on_its_event(self):
        seen = []
        with self.assertWarns(DeprecationWarning):
            self.api.schedule_event(seen.append, when=0, name="t")
        self.service._evaluate()
        self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
