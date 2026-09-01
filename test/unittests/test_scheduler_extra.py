"""Bus-level coverage of the legacy scheduler surface."""
import os
import tempfile
import time
import unittest
from unittest import TestCase

from ovos_utils.fakebus import FakeBus

from ovos_bus_client.message import Message
from ovos_bus_client.util.scheduler import EventScheduler


class TestLegacyBusSurface(TestCase):
    def setUp(self):
        self.bus = FakeBus()
        handle, self.store = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        os.unlink(self.store)
        self.sched = EventScheduler(self.bus, schedule_file=self.store,
                                    autostart=False)

    def tearDown(self):
        self.sched.shutdown()
        for path in (self.store, f"{self.store}.tmp"):
            if os.path.isfile(path):
                os.unlink(path)

    def test_initial_state(self):
        self.assertEqual(self.sched.schedules, {})
        self.assertFalse(self.sched.is_running)

    def test_schedule_event_one_shot(self):
        self.sched.schedule_event("skill:ev", time.time() + 60, data={"x": 1})
        self.assertIn(("skill", "skill:ev"), self.sched.schedules)

    def test_a_request_made_while_the_clock_is_behind_is_kept(self):
        # a board that boots before its time source is reached must not
        # lose the schedules made in that window
        self.sched._clock_synced = False
        self.sched.schedule_event("skill:ev", time.time() + 60)
        self.assertIn(("skill", "skill:ev"), self.sched.schedules)
        self.assertTrue(os.path.isfile(self.store))

    def test_scheduling_a_repeat_twice_keeps_one_record(self):
        future = time.time() + 60
        self.sched.schedule_event("skill:ev", future, repeat=10)
        self.sched.schedule_event("skill:ev", future, repeat=10)
        self.assertEqual(len(self.sched.schedules), 1)

    def test_handle_schedule_event_via_bus_message(self):
        self.bus.emit(Message("mycroft.scheduler.schedule_event",
                              {"event": "skill:fromBus",
                               "time": time.time() + 60, "data": {}}))
        self.assertIn(("skill", "skill:fromBus"), self.sched.schedules)

    def test_handle_schedule_event_missing_event(self):
        self.bus.emit(Message("mycroft.scheduler.schedule_event",
                              {"time": time.time() + 60}))
        self.assertEqual(self.sched.schedules, {})

    def test_handle_schedule_event_missing_time(self):
        self.bus.emit(Message("mycroft.scheduler.schedule_event",
                              {"event": "skill:x"}))
        self.assertEqual(self.sched.schedules, {})

    def test_remove_event_present(self):
        self.sched.schedule_event("skill:ev", time.time() + 60)
        self.sched.remove_event("skill:ev")
        self.assertNotIn(("skill", "skill:ev"), self.sched.schedules)

    def test_remove_event_absent_noop(self):
        self.sched.remove_event("skill:missing")

    def test_handle_remove_event(self):
        self.sched.schedule_event("skill:ev", time.time() + 60)
        self.bus.emit(Message("mycroft.scheduler.remove_event",
                              {"event": "skill:ev"}))
        self.assertNotIn(("skill", "skill:ev"), self.sched.schedules)

    def test_update_event_absent_noop(self):
        self.sched.update_event("skill:missing", {"x": 1})

    def test_handle_update_event(self):
        self.sched.schedule_event("skill:ev", time.time() + 60, data={"x": 1})
        self.bus.emit(Message("mycroft.scheduler.update_event",
                              {"event": "skill:ev", "data": {"x": 2}}))
        self.assertEqual(
            self.sched.schedules["skill", "skill:ev"].record["data"], {"x": 2})

    def test_handle_get_event_for_an_unknown_name(self):
        answers = []
        self.bus.on("mycroft.event_status.callback.never-scheduled",
                    answers.append)
        self.bus.emit(Message("mycroft.scheduler.get_event",
                              {"name": "never-scheduled"}))
        self.assertIsNone(answers[-1].data["schedule"])

    def test_store_and_reload(self):
        self.sched.schedule_event("skill:ev", time.time() + 60)
        self.sched.store()
        revived = EventScheduler(self.bus, schedule_file=self.store,
                                 autostart=False)
        self.addCleanup(revived.shutdown)
        self.assertIn(("skill", "skill:ev"), revived.schedules)


if __name__ == "__main__":
    unittest.main()
