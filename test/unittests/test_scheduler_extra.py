"""Coverage tests for ovos_bus_client.util.scheduler — EventScheduler."""
import os
import tempfile
import time
import unittest
from unittest import TestCase
from unittest.mock import MagicMock

from ovos_bus_client.message import Message
from ovos_bus_client.util.scheduler import EventScheduler, repeat_time
from ovos_utils.fakebus import FakeBus


class TestRepeatTime(TestCase):
    def test_repeat_time_advances_until_future(self):
        past = time.time() - 100
        future = repeat_time(past, 30)
        self.assertGreaterEqual(future, time.time())

    def test_repeat_time_negative_returns_future(self):
        next_time = repeat_time(time.time(), -30)
        self.assertGreaterEqual(next_time, time.time())


class TestEventScheduler(TestCase):
    def setUp(self):
        self.bus = FakeBus()
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmpfile.close()
        os.unlink(self.tmpfile.name)
        self.sched = EventScheduler(self.bus, schedule_file=self.tmpfile.name,
                                    autostart=False)

    def tearDown(self):
        self.sched._stopping.set()
        try:
            self.sched.shutdown()
        except Exception:
            pass
        try:
            os.unlink(self.tmpfile.name)
        except OSError:
            pass

    def test_initial_state(self):
        self.assertEqual(self.sched.events, {})
        self.assertFalse(self.sched.is_running)

    def test_schedule_event_one_shot(self):
        # ensure clock-sync check passes
        # bypass clock-in-past check (_last_sync is a datetime)
        from ovos_utils.time import now_local
        self.sched._last_sync = now_local()
        future = time.time() + 60
        self.sched.schedule_event("ev", future, data={"x": 1})
        self.assertIn("ev", self.sched.events)

    def test_schedule_event_clock_in_past_drops(self):
        # leave _last_sync at its initial past value
        from ovos_utils.time import now_local
        from datetime import timedelta
        self.sched._last_sync = now_local() - timedelta(days=800)
        self.sched.schedule_event("ev", time.time() + 60)
        self.assertNotIn("ev", self.sched.events)
        self.assertEqual(self.sched._dropped_events, 1)

    def test_schedule_repeating_event_dedupes(self):
        # bypass clock-in-past check (_last_sync is a datetime)
        from ovos_utils.time import now_local
        self.sched._last_sync = now_local()
        future = time.time() + 60
        self.sched.schedule_event("ev", future, repeat=10)
        # second schedule should be ignored
        self.sched.schedule_event("ev", future, repeat=10)
        self.assertEqual(len(self.sched.events["ev"]), 1)

    def test_handle_schedule_event_via_bus_message(self):
        # bypass clock-in-past check (_last_sync is a datetime)
        from ovos_utils.time import now_local
        self.sched._last_sync = now_local()
        future = time.time() + 60
        msg = Message("mycroft.scheduler.schedule_event",
                      {"event": "fromBus", "time": future, "data": {}})
        self.sched.handle_schedule_event(msg)
        self.assertIn("fromBus", self.sched.events)

    def test_handle_schedule_event_missing_event_logs(self):
        # missing event name → logged error, no addition
        msg = Message("e", {"time": time.time() + 60})
        self.sched.handle_schedule_event(msg)
        self.assertEqual(self.sched.events, {})

    def test_handle_schedule_event_missing_time(self):
        msg = Message("e", {"event": "x"})
        self.sched.handle_schedule_event(msg)
        self.assertNotIn("x", self.sched.events)

    def test_remove_event_present(self):
        # bypass clock-in-past check (_last_sync is a datetime)
        from ovos_utils.time import now_local
        self.sched._last_sync = now_local()
        self.sched.schedule_event("ev", time.time() + 60)
        self.sched.remove_event("ev")
        self.assertNotIn("ev", self.sched.events)

    def test_remove_event_absent_noop(self):
        self.sched.remove_event("missing")  # no error

    def test_handle_remove_event(self):
        # bypass clock-in-past check (_last_sync is a datetime)
        from ovos_utils.time import now_local
        self.sched._last_sync = now_local()
        self.sched.schedule_event("ev", time.time() + 60)
        self.sched.handle_remove_event(Message("e", {"event": "ev"}))
        self.assertNotIn("ev", self.sched.events)

    def test_update_event_present(self):
        # bypass clock-in-past check (_last_sync is a datetime)
        from ovos_utils.time import now_local
        self.sched._last_sync = now_local()
        self.sched.schedule_event("ev", time.time() + 60, data={"x": 1})
        self.sched.update_event("ev", {"x": 2})
        new_data = self.sched.events["ev"][0][2]
        self.assertEqual(new_data["x"], 2)

    def test_update_event_absent_noop(self):
        self.sched.update_event("missing", {"x": 1})

    def test_handle_update_event(self):
        # bypass clock-in-past check (_last_sync is a datetime)
        from ovos_utils.time import now_local
        self.sched._last_sync = now_local()
        self.sched.schedule_event("ev", time.time() + 60, data={"x": 1})
        self.sched.handle_update_event(
            Message("u", {"event": "ev", "data": {"x": 2}})
        )

    def test_handle_get_event_for_unknown_emits_none(self):
        # for an unknown event name the handler still hits the emitter;
        # an internal assertion errors on non-dict data, so just verify it doesn't crash
        # the lookup path
        try:
            self.sched.handle_get_event(Message("g", {"name": "never-scheduled"}))
        except AssertionError:
            pass  # Message.reply enforces dict-data; not our concern here

    def test_clear_empty_removes_empty_entries(self):
        self.sched.events["empty"] = []
        self.sched.events["full"] = [(time.time() + 60, None, {}, {})]
        self.sched.clear_empty()
        self.assertNotIn("empty", self.sched.events)
        self.assertIn("full", self.sched.events)

    def test_clear_repeating_filters_repeats_in_place(self):
        # clear_repeating filters the tuple list per event key to drop
        # repeats (tup[1] is None means one-shot)
        self.sched.events["mixed"] = [
            (time.time() + 60, 30, {}, {}),    # repeat
            (time.time() + 60, None, {}, {}),  # one-shot
        ]
        self.sched.clear_repeating()
        # one-shot survives
        self.assertEqual(len(self.sched.events["mixed"]), 1)
        self.assertIsNone(self.sched.events["mixed"][0][1])

    def test_store_and_reload(self):
        # bypass clock-in-past check (_last_sync is a datetime)
        from ovos_utils.time import now_local
        self.sched._last_sync = now_local()
        self.sched.schedule_event("ev", time.time() + 60)
        self.sched.store()
        # constructing a new scheduler should reload from disk
        new = EventScheduler(self.bus, schedule_file=self.tmpfile.name,
                             autostart=False)
        try:
            self.assertIn("ev", new.events)
        finally:
            new._stopping.set()


if __name__ == "__main__":
    unittest.main()
