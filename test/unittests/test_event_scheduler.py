"""The scheduler under its historical name, ``EventScheduler``.

These exercise the epoch-float API and the ``mycroft.scheduler.*`` topics the
rest of the stack still speaks.
"""
import os
import re
import tempfile
import time
import unittest
from unittest.mock import patch

from ovos_utils.fakebus import FakeBus

from ovos_bus_client.message import Message
from ovos_bus_client.util.scheduled_events import topics
from ovos_bus_client.util.scheduler import EventScheduler, repeat_time


class TestRepeatTime(unittest.TestCase):
    def test_next_occurrence_is_in_the_future(self):
        past = time.time() - 100
        self.assertGreaterEqual(repeat_time(past, 30), time.time())

    def test_a_missed_period_keeps_the_schedule_phase(self):
        start = time.time() - 95
        self.assertAlmostEqual((repeat_time(start, 30) - start) % 30, 0, places=3)

    def test_a_negative_period_is_read_as_its_magnitude(self):
        self.assertGreaterEqual(repeat_time(time.time(), -30), time.time())


class TestEventScheduler(unittest.TestCase):
    def setUp(self):
        self.bus = FakeBus()
        self.emitted = []
        self.bus.on("message", self._record)
        handle, self.store = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        os.unlink(self.store)
        self.scheduler = EventScheduler(self.bus, self.store, autostart=False)

    def tearDown(self):
        self.scheduler.shutdown()
        for path in (self.store, f"{self.store}.tmp"):
            if os.path.isfile(path):
                os.unlink(path)

    def _record(self, message):
        if isinstance(message, str):
            message = Message.deserialize(message)
        self.emitted.append(message)

    def test_an_absolute_path_is_used_as_the_store(self):
        self.assertEqual(self.scheduler.schedule_file, self.store)
        self.assertFalse(self.scheduler.is_running)

    def test_add_and_remove(self):
        self.scheduler.schedule_event("skill:test", time.time() + 3600)
        self.scheduler.schedule_event("skill:test-2", time.time() + 3600)
        self.assertIn(("skill", "skill:test"), self.scheduler.schedules)

        self.scheduler.remove_event("skill:test")
        self.assertNotIn(("skill", "skill:test"), self.scheduler.schedules)
        self.assertIn(("skill", "skill:test-2"), self.scheduler.schedules)

    def test_a_due_event_is_emitted_with_its_data(self):
        self.scheduler.schedule_event("skill:test", time.time(), data={"a": 1})
        self.scheduler.check_state()
        fired = [m for m in self.emitted if m.msg_type == "skill:test"]
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0].data, {"a": 1})

    def test_a_one_shot_is_dropped_once_it_has_fired(self):
        self.scheduler.schedule_event("skill:test", time.time())
        self.scheduler.check_state()
        self.assertEqual(self.scheduler.schedules, {})

    def test_scheduling_the_same_name_twice_does_not_duplicate(self):
        self.scheduler.schedule_event("skill:test", time.time() + 3600)
        self.scheduler.schedule_event("skill:test", time.time() + 7200)
        self.assertEqual(len(self.scheduler.schedules), 1)

    def test_repeating_events_survive_a_restart(self):
        self.scheduler.schedule_event("skill:tick", time.time() + 3600, repeat=60)
        self.scheduler.shutdown()

        revived = EventScheduler(self.bus, self.store, autostart=False)
        self.addCleanup(revived.shutdown)
        self.assertIn(("skill", "skill:tick"), revived.schedules)

    def test_update_event_changes_the_data(self):
        self.scheduler.schedule_event("skill:test", time.time() + 3600,
                                      data={"a": 1})
        self.scheduler.update_event("skill:test", {"a": 2})
        self.assertEqual(
            self.scheduler.schedules["skill", "skill:test"].record["data"],
            {"a": 2})

    def test_list_events_answers_with_every_schedule(self):
        self.scheduler.schedule_event("skill:one", time.time() + 36000)
        self.scheduler.schedule_event("skill:two", time.time() + 7200, repeat=60)
        self.scheduler.legacy.handle_list(
            Message(topics.LEGACY_LIST, {},
                    {"source": ["a"], "destination": ["b"]}))
        answers = [m for m in self.emitted if "scheduled_events" in m.data]
        self.assertEqual(set(answers[-1].data["scheduled_events"]),
                         {"skill:one", "skill:two"})

    def test_the_store_is_written_on_every_mutation(self):
        self.scheduler.schedule_event("skill:test", time.time() + 3600)
        self.assertTrue(os.path.isfile(self.store))


class TestPreSpecPublicSurface(unittest.TestCase):
    """PR #311 dropped these nine public methods and two attributes when
    the epoch-float scheduler became this class. They are restored as
    deprecated delegates onto the SCHEDULER-1 implementation for one stable
    cycle.
    """

    def setUp(self):
        self.bus = FakeBus()
        self.emitted = []
        self.bus.on("message", self._record)
        handle, self.store = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        os.unlink(self.store)
        self.scheduler = EventScheduler(self.bus, self.store, autostart=False)

    def tearDown(self):
        self.scheduler.shutdown()
        for path in (self.store, f"{self.store}.tmp"):
            if os.path.isfile(path):
                os.unlink(path)

    def _record(self, message):
        if isinstance(message, str):
            message = Message.deserialize(message)
        self.emitted.append(message)

    def _assert_deprecation_names_a_removal_version(self, mock_warn):
        mock_warn.assert_called()
        version = mock_warn.call_args[0][1]
        self.assertRegex(version, r"^\d+\.0\.0$")

    def test_handle_schedule_event_schedules(self):
        with patch("ovos_bus_client.util.scheduler.log_deprecation") as warn:
            self.scheduler.handle_schedule_event(
                Message(topics.LEGACY_SCHEDULE,
                        {"event": "skill:test", "time": time.time() + 3600}))
        self.assertIn(("skill", "skill:test"), self.scheduler.schedules)
        self._assert_deprecation_names_a_removal_version(warn)

    def test_handle_remove_event_removes(self):
        self.scheduler.schedule_event("skill:test", time.time() + 3600)
        with patch("ovos_bus_client.util.scheduler.log_deprecation") as warn:
            self.scheduler.handle_remove_event(
                Message(topics.LEGACY_REMOVE, {"event": "skill:test"}))
        self.assertNotIn(("skill", "skill:test"), self.scheduler.schedules)
        self._assert_deprecation_names_a_removal_version(warn)

    def test_handle_update_event_updates_data(self):
        self.scheduler.schedule_event("skill:test", time.time() + 3600,
                                      data={"a": 1})
        with patch("ovos_bus_client.util.scheduler.log_deprecation") as warn:
            self.scheduler.handle_update_event(
                Message(topics.LEGACY_UPDATE,
                        {"event": "skill:test", "data": {"a": 2}}))
        self.assertEqual(
            self.scheduler.schedules["skill", "skill:test"].record["data"],
            {"a": 2})
        self._assert_deprecation_names_a_removal_version(warn)

    def test_handle_get_event_answers_with_the_object_shape(self):
        self.scheduler.schedule_event("skill:test", time.time() + 3600)
        with patch("ovos_bus_client.util.scheduler.log_deprecation") as warn:
            self.scheduler.handle_get_event(
                Message(topics.LEGACY_GET, {"name": "skill:test"}))
        answers = [m for m in self.emitted
                  if m.msg_type.startswith(topics.LEGACY_GET_REPLY_PREFIX)]
        self.assertEqual(answers[-1].data["event"], "skill:test")
        self.assertIsNotNone(answers[-1].data["schedule"])
        self._assert_deprecation_names_a_removal_version(warn)

    def test_handle_list_events_answers_with_every_schedule(self):
        self.scheduler.schedule_event("skill:one", time.time() + 3600)
        with patch("ovos_bus_client.util.scheduler.log_deprecation") as warn:
            self.scheduler.handle_list_events(
                Message(topics.LEGACY_LIST, {},
                        {"source": ["a"], "destination": ["b"]}))
        answers = [m for m in self.emitted if "scheduled_events" in m.data]
        self.assertIn("skill:one", answers[-1].data["scheduled_events"])
        self._assert_deprecation_names_a_removal_version(warn)

    def test_handle_system_clock_sync_delegates(self):
        with patch("ovos_bus_client.util.scheduler.log_deprecation") as warn:
            self.scheduler.handle_system_clock_sync(
                Message(topics.CLOCK_SYNCED, {}))
        self._assert_deprecation_names_a_removal_version(warn)

    def test_clear_repeating_drops_only_repeating_schedules(self):
        self.scheduler.schedule_event("skill:once", time.time() + 3600)
        self.scheduler.schedule_event("skill:tick", time.time() + 3600, repeat=60)
        with patch("ovos_bus_client.util.scheduler.log_deprecation") as warn:
            self.scheduler.clear_repeating()
        self.assertIn(("skill", "skill:once"), self.scheduler.schedules)
        self.assertNotIn(("skill", "skill:tick"), self.scheduler.schedules)
        self._assert_deprecation_names_a_removal_version(warn)

    def test_clear_empty_is_a_documented_no_op(self):
        self.scheduler.schedule_event("skill:test", time.time() + 3600)
        with patch("ovos_bus_client.util.scheduler.log_deprecation") as warn:
            self.scheduler.clear_empty()
        self.assertIn(("skill", "skill:test"), self.scheduler.schedules)
        self._assert_deprecation_names_a_removal_version(warn)

    def test_load_reads_the_store_into_memory(self):
        self.scheduler.schedule_event("skill:test", time.time() + 3600)
        self.scheduler.schedules.clear()
        with patch("ovos_bus_client.util.scheduler.log_deprecation") as warn:
            self.scheduler.load()
        self.assertIn(("skill", "skill:test"), self.scheduler.schedules)
        self._assert_deprecation_names_a_removal_version(warn)

    def test_events_attribute_snapshots_the_schedules(self):
        self.scheduler.schedule_event("skill:test", time.time() + 3600,
                                      data={"a": 1})
        with patch("ovos_bus_client.util.scheduler.log_deprecation") as warn:
            snapshot = self.scheduler.events
        self.assertIn("skill:test", snapshot)
        self.assertEqual(snapshot["skill:test"][0][2], {"a": 1})
        self._assert_deprecation_names_a_removal_version(warn)

    def test_event_lock_is_the_schedules_lock(self):
        with patch("ovos_bus_client.util.scheduler.log_deprecation") as warn:
            self.assertIs(self.scheduler.event_lock, self.scheduler.lock)
        self._assert_deprecation_names_a_removal_version(warn)


if __name__ == "__main__":
    unittest.main()
