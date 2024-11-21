import time
import unittest

from os.path import isfile
from ovos_utils.messagebus import FakeBus


class TestScheduler(unittest.TestCase):
    def test_repeat_time(self):
        from ovos_bus_client.util.scheduler import repeat_time
        next_time = repeat_time(time.time(), -30)
        self.assertGreaterEqual(next_time, time.time())

        next_time = repeat_time(time.time() - 5, 10)
        self.assertGreaterEqual(next_time, time.time())


class TestEventScheduler(unittest.TestCase):
    scheduler = None
    bus = FakeBus()
    test_schedule_name = f"test_{time.time()}.json"

    @classmethod
    def setUpClass(cls) -> None:
        from ovos_bus_client.util.scheduler import EventScheduler
        cls.scheduler = EventScheduler(cls.bus, cls.test_schedule_name, False)

    def test_00_init(self):
        self.assertEqual(self.scheduler.events, dict())
        self.assertIsNotNone(self.scheduler.event_lock)
        self.assertEqual(self.scheduler.bus, self.bus)
        self.assertFalse(isfile(self.scheduler.schedule_file))
        self.assertEqual(
            len(self.bus.ee.listeners("mycroft.scheduler.schedule_event")), 1)
        self.assertEqual(
            len(self.bus.ee.listeners("mycroft.scheduler.remove_event")), 1)
        self.assertEqual(
            len(self.bus.ee.listeners("mycroft.scheduler.update_event")), 1)
        self.assertEqual(
            len(self.bus.ee.listeners("mycroft.scheduler.get_event")), 1)
        self.assertFalse(self.scheduler.is_running)

        self.scheduler.start()
        timeout = time.time() + 2
        while not self.scheduler.is_running and time.time() < timeout:
            time.sleep(0.2)
        self.assertTrue(self.scheduler.is_running)

        self.scheduler._stopping.set()
        self.assertFalse(self.scheduler.is_running)

    def test_scheduler_init(self):
        from ovos_bus_client.util.scheduler import EventScheduler
        scheduler = EventScheduler(self.bus, self.test_schedule_name)
        self.assertTrue(scheduler.is_running)
        self.assertEqual(scheduler.bus, self.bus)
        self.assertNotEqual(scheduler, self.scheduler)
        scheduler.shutdown()

