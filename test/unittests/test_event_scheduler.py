"""
Test cases regarding the event scheduler.
"""

import unittest
import time

try:
    from pyee import ExecutorEventEmitter
except (ImportError, ModuleNotFoundError):
    from pyee.executor import ExecutorEventEmitter


from unittest.mock import MagicMock, patch
from ovos_utils.messagebus import FakeBus
from ovos_bus_client.util.scheduler import EventScheduler


class TestEventScheduler(unittest.TestCase):
    @patch("threading.Thread")
    @patch("json.load")
    @patch("json.dump")
    @patch("builtins.open")
    def test_create(self, mock_open, mock_json_dump, mock_load, mock_thread):
        """
        Test creating and shutting down event_scheduler.
        """
        mock_load.return_value = ""
        mock_open.return_value = MagicMock()
        emitter = MagicMock()
        es = EventScheduler(emitter)
        es.shutdown()
        self.assertEqual(mock_json_dump.call_args[0][0], {})

    @patch("threading.Thread")
    @patch("json.load")
    @patch("json.dump")
    @patch("builtins.open")
    def test_add_remove(self, mock_open, mock_json_dump, mock_load, mock_thread):
        """
        Test add an event and then remove it.
        """
        # Thread start is mocked so will not actually run the thread loop
        mock_load.return_value = ""
        mock_open.return_value = MagicMock()
        emitter = MagicMock()
        es = EventScheduler(emitter)

        # 900000000000 should be in the future for a long time
        es.schedule_event("test", 90000000000, None)
        es.schedule_event("test-2", 90000000000, None)

        es.check_state()  # run one cycle
        self.assertTrue("test" in es.events)
        self.assertTrue("test-2" in es.events)

        es.remove_event("test")
        es.check_state()  # run one cycle
        self.assertTrue("test" not in es.events)
        self.assertTrue("test-2" in es.events)
        es.shutdown()

    @patch("threading.Thread")
    @patch("json.load")
    @patch("json.dump")
    @patch("builtins.open")
    def test_save(self, mock_open, mock_dump, mock_load, mock_thread):
        """
        Test save functionality.
        """
        mock_load.return_value = ""
        mock_open.return_value = MagicMock()
        emitter = MagicMock()
        es = EventScheduler(emitter)

        # 900000000000 should be in the future for a long time
        es.schedule_event("test", 900000000000, None)
        es.schedule_event("test-repeat", 910000000000, 60)
        es.check_state()

        es.shutdown()

        # Make sure the dump method wasn't called with test-repeat
        self.assertEqual(mock_dump.call_args[0][0], {"test": [(900000000000, None, {}, None)]})

    @patch("threading.Thread")
    @patch("json.load")
    @patch("json.dump")
    @patch("builtins.open")
    def test_send_event(self, mock_open, mock_dump, mock_load, mock_thread):
        """
        Test save functionality.
        """
        mock_load.return_value = ""
        mock_open.return_value = MagicMock()
        emitter = MagicMock()
        es = EventScheduler(emitter)

        # 0 should be in the future for a long time
        es.schedule_event("test", time.time(), None)

        es.check_state()
        self.assertEqual(emitter.emit.call_args[0][0].msg_type, "test")
        self.assertEqual(emitter.emit.call_args[0][0].data, {})
        es.shutdown()

    @patch("threading.Thread")
    @patch("json.load")
    @patch("json.dump")
    @patch("builtins.open")
    def test_list_events_handler(self, mock_open, mock_dump, mock_load, mock_thread):
        """
        Test list_events_handler returns all scheduled events.
        """
        mock_load.return_value = ""
        mock_open.return_value = MagicMock()
        emitter = MagicMock()
        es = EventScheduler(emitter)

        # Schedule a couple of events
        es.schedule_event("test-event-1", 900000000000, None, {"data": "test1"})
        es.schedule_event("test-event-2", 910000000000, 60, {"data": "test2"})

        # Create a mock message
        mock_message = MagicMock()
        mock_message.context = {"source": "test"}

        # Call the handler
        es.list_events_handler(mock_message)

        # Verify message.reply was called with correct msg_type and data
        mock_message.reply.assert_called_once()
        call_args = mock_message.reply.call_args
        self.assertEqual(call_args[0][0], "mycroft.scheduler.list_events.response")
        self.assertIn("scheduled_events", call_args[1]["data"])

        # Verify emitter.emit was called with the reply message
        emitter.emit.assert_called()

        # Verify the scheduled events contain our test events
        scheduled_events = call_args[1]["data"]["scheduled_events"]
        self.assertIn("test-event-1", scheduled_events)
        self.assertIn("test-event-2", scheduled_events)

        es.shutdown()
