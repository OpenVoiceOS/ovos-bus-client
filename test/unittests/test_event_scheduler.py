"""The scheduler under its historical name, ``EventScheduler``.

These exercise the epoch-float API and the ``mycroft.scheduler.*`` topics the
rest of the stack still speaks.
"""
import os
import tempfile
import time
import unittest

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


if __name__ == "__main__":
    unittest.main()
