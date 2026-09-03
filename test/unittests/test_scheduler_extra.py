"""The legacy scheduling topics, reached over the bus."""
import os
import tempfile
import time
import unittest
from unittest import TestCase

from ovos_utils.fakebus import FakeBus

from ovos_bus_client.message import Message
from ovos_bus_client.util.scheduled_events import topics
from ovos_bus_client.util.scheduler import EventScheduler


class TestLegacyBusSurface(TestCase):
    def setUp(self):
        self.bus = FakeBus()
        handle, self.store = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        os.unlink(self.store)
        self.scheduler = EventScheduler(self.bus, schedule_file=self.store,
                                        autostart=False)

    def tearDown(self):
        self.scheduler.shutdown()
        for path in (self.store, f"{self.store}.tmp"):
            if os.path.isfile(path):
                os.unlink(path)

    def test_initial_state(self):
        self.assertEqual(self.scheduler.schedules, {})
        self.assertFalse(self.scheduler.is_running)

    def test_schedule_event_one_shot(self):
        self.scheduler.schedule_event("skill:ev", time.time() + 60, data={"x": 1})
        self.assertIn(("skill", "skill:ev"), self.scheduler.schedules)

    def test_a_request_made_while_the_clock_is_behind_is_kept(self):
        # a board that boots before it reaches a time source must not lose the
        # schedules made in that window
        self.scheduler.clock_synced = False
        self.scheduler.schedule_event("skill:ev", time.time() + 60)
        self.assertIn(("skill", "skill:ev"), self.scheduler.schedules)
        self.assertTrue(os.path.isfile(self.store))

    def test_scheduling_a_repeat_twice_keeps_one_record(self):
        future = time.time() + 60
        self.scheduler.schedule_event("skill:ev", future, repeat=10)
        self.scheduler.schedule_event("skill:ev", future, repeat=10)
        self.assertEqual(len(self.scheduler.schedules), 1)

    def test_schedule_event_via_a_bus_message(self):
        self.bus.emit(Message(topics.LEGACY_SCHEDULE,
                              {"event": "skill:fromBus",
                               "time": time.time() + 60, "data": {}}))
        self.assertIn(("skill", "skill:fromBus"), self.scheduler.schedules)

    def test_a_schedule_request_without_an_event_is_ignored(self):
        self.bus.emit(Message(topics.LEGACY_SCHEDULE,
                              {"time": time.time() + 60}))
        self.assertEqual(self.scheduler.schedules, {})

    def test_a_schedule_request_without_a_time_is_ignored(self):
        self.bus.emit(Message(topics.LEGACY_SCHEDULE, {"event": "skill:x"}))
        self.assertEqual(self.scheduler.schedules, {})

    def test_remove_event_present(self):
        self.scheduler.schedule_event("skill:ev", time.time() + 60)
        self.scheduler.remove_event("skill:ev")
        self.assertNotIn(("skill", "skill:ev"), self.scheduler.schedules)

    def test_removing_an_absent_event_does_nothing(self):
        self.scheduler.remove_event("skill:missing")

    def test_remove_event_via_a_bus_message(self):
        self.scheduler.schedule_event("skill:ev", time.time() + 60)
        self.bus.emit(Message(topics.LEGACY_REMOVE, {"event": "skill:ev"}))
        self.assertNotIn(("skill", "skill:ev"), self.scheduler.schedules)

    def test_updating_an_absent_event_does_nothing(self):
        self.scheduler.update_event("skill:missing", {"x": 1})

    def test_update_event_via_a_bus_message(self):
        self.scheduler.schedule_event("skill:ev", time.time() + 60, data={"x": 1})
        self.bus.emit(Message(topics.LEGACY_UPDATE,
                              {"event": "skill:ev", "data": {"x": 2}}))
        self.assertEqual(
            self.scheduler.schedules["skill", "skill:ev"].record["data"],
            {"x": 2})

    def test_get_event_for_an_unknown_name_answers_with_nothing(self):
        answers = []
        self.bus.on(f"{topics.LEGACY_GET_REPLY_PREFIX}never-scheduled",
                    answers.append)
        self.bus.emit(Message(topics.LEGACY_GET, {"name": "never-scheduled"}))
        self.assertIsNone(answers[-1].data["schedule"])

    def test_store_and_reload(self):
        self.scheduler.schedule_event("skill:ev", time.time() + 60)
        self.scheduler.store()
        revived = EventScheduler(self.bus, schedule_file=self.store,
                                 autostart=False)
        self.addCleanup(revived.shutdown)
        self.assertIn(("skill", "skill:ev"), revived.schedules)


if __name__ == "__main__":
    unittest.main()
