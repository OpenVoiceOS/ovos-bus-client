"""Coverage tests for ovos_bus_client.apis.events — EventSchedulerInterface."""
import unittest
from datetime import datetime, timedelta
from unittest import TestCase
from unittest.mock import MagicMock

from ovos_bus_client.apis.events import EventSchedulerInterface
from ovos_bus_client.message import Message


def _emitted_types(bus):
    return [c.args[0].msg_type for c in bus.emit.call_args_list]


class TestEventSchedulerSetup(TestCase):
    def test_default_skill_id_from_class_name(self):
        api = EventSchedulerInterface()
        self.assertEqual(api.skill_id, "eventschedulerinterface")

    def test_explicit_skill_id(self):
        api = EventSchedulerInterface(skill_id="my.skill")
        self.assertEqual(api.skill_id, "my.skill")

    def test_set_bus_and_id(self):
        api = EventSchedulerInterface()
        bus = MagicMock()
        api.set_bus(bus)
        self.assertIs(api.bus, bus)
        api.set_id("other")
        self.assertEqual(api.skill_id, "other")

    def test_create_unique_name(self):
        api = EventSchedulerInterface(skill_id="my.skill")
        self.assertEqual(api._create_unique_name("timer"), "my.skill:timer")

    def test_create_unique_name_empty(self):
        api = EventSchedulerInterface(skill_id="x")
        self.assertEqual(api._create_unique_name(""), "x:")
        self.assertEqual(api._create_unique_name(None), "x:")


class TestScheduleEvent(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.api = EventSchedulerInterface(bus=self.bus, skill_id="my.skill")

    def test_schedule_with_seconds_offset(self):
        self.api.schedule_event(lambda m: None, when=10, name="t1")
        emitted = self.bus.emit.call_args[0][0]
        self.assertEqual(emitted.msg_type, "mycroft.scheduler.schedule_event")
        self.assertEqual(emitted.data["event"], "my.skill:t1")
        self.assertIn("time", emitted.data)
        self.assertIsNone(emitted.data["repeat"])

    def test_schedule_with_datetime(self):
        when = datetime.now() + timedelta(seconds=30)
        self.api.schedule_event(lambda m: None, when=when, name="t2")
        emitted = self.bus.emit.call_args[0][0]
        self.assertEqual(emitted.data["event"], "my.skill:t2")

    def test_schedule_with_data(self):
        self.api.schedule_event(lambda m: None, when=5, data={"k": "v"}, name="t")
        emitted = self.bus.emit.call_args[0][0]
        self.assertEqual(emitted.data["data"], {"k": "v"})

    def test_schedule_rejects_negative_offset(self):
        with self.assertRaises(ValueError):
            self.api.schedule_event(lambda m: None, when=-5)

    def test_schedule_rejects_non_datetime(self):
        with self.assertRaises(TypeError):
            self.api.schedule_event(lambda m: None, when="not a time")

    def test_schedule_event_anonymous_uses_handler_name(self):
        def my_handler(message):
            pass
        self.api.schedule_event(my_handler, when=1)
        emitted = self.bus.emit.call_args[0][0]
        self.assertIn("my_handler", emitted.data["event"])


class TestRepeatingEvent(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.api = EventSchedulerInterface(bus=self.bus, skill_id="my.skill")

    def test_repeating_event_records_in_repeats(self):
        self.api.schedule_repeating_event(
            lambda m: None, when=None, interval=60, name="heartbeat",
        )
        self.assertIn("heartbeat", self.api.scheduled_repeats)
        emitted = self.bus.emit.call_args[0][0]
        self.assertEqual(emitted.data["repeat"], 60)

    def test_repeating_event_skipped_if_already_scheduled(self):
        self.api.schedule_repeating_event(
            lambda m: None, when=None, interval=60, name="dup",
        )
        self.bus.reset_mock()
        # second call should be a no-op
        self.api.schedule_repeating_event(
            lambda m: None, when=None, interval=60, name="dup",
        )
        self.assertFalse(self.bus.emit.called)


class TestUpdateAndCancel(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.api = EventSchedulerInterface(bus=self.bus, skill_id="my.skill")

    def test_update_scheduled_event(self):
        self.api.update_scheduled_event("t1", data={"new": True})
        # #222's fix (mycroft.scheduler.update_event) is emitted, plus the
        # misspelled mycroft.schedule.update_event for one stable cycle in
        # case anything still listens on the typo directly
        emitted = [c.args[0] for c in self.bus.emit.call_args_list]
        self.assertEqual([m.msg_type for m in emitted],
                         ["mycroft.scheduler.update_event",
                          "mycroft.schedule.update_event"])
        for message in emitted:
            self.assertEqual(message.data["event"], "my.skill:t1")
            self.assertEqual(message.data["data"], {"new": True})

    def test_cancel_existing_repeating_event(self):
        self.api.schedule_repeating_event(
            lambda m: None, when=None, interval=10, name="r1",
        )
        self.api.cancel_scheduled_event("r1")
        self.assertNotIn("r1", self.api.scheduled_repeats)
        self.assertIn("mycroft.scheduler.remove_event", _emitted_types(self.bus))

    def test_cancel_all_repeating_events(self):
        self.api.schedule_repeating_event(lambda m: None, when=None,
                                          interval=10, name="r1")
        self.api.schedule_repeating_event(lambda m: None, when=None,
                                          interval=10, name="r2")
        self.api.cancel_all_repeating_events()
        self.assertEqual(self.api.scheduled_repeats, [])

    def test_get_scheduled_event_status_returns_seconds_left(self):
        future = int((datetime.now() + timedelta(seconds=90)).timestamp())
        self.bus.wait_for_response.return_value = Message(
            "callback", {"event": "my.skill:t", "schedule": [future, None, {}, {}]},
        )
        left = self.api.get_scheduled_event_status("t")
        self.assertGreater(left, 60)

    def test_get_scheduled_event_status_timeout_raises(self):
        self.bus.wait_for_response.return_value = None
        with self.assertRaises(Exception):
            self.api.get_scheduled_event_status("t")


class TestShutdown(TestCase):
    def test_shutdown_cancels_and_clears(self):
        bus = MagicMock()
        api = EventSchedulerInterface(bus=bus, skill_id="my.skill")
        api.schedule_repeating_event(lambda m: None, when=None,
                                     interval=5, name="r1")
        api.shutdown()
        self.assertEqual(api.scheduled_repeats, [])


if __name__ == "__main__":
    unittest.main()
