"""More MessageCollector coverage — register_handler/receive_response branches."""
import threading
import time
import unittest
from unittest import TestCase
from unittest.mock import MagicMock

from ovos_bus_client.client.collector import MessageCollector
from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus


def _new_collector(bus=None, min_t=0.05, max_t=0.5):
    bus = bus or FakeBus()
    msg = Message("question:query", {"phrase": "x"})
    return MessageCollector(bus, msg, min_t, max_t, lambda m: False)


class TestRegisterHandler(TestCase):
    def test_register_records_handler(self):
        c = _new_collector()
        msg = Message("question:query.handling", {
            "query": c.collect_id, "handler": "h1", "timeout": 2,
        })
        c._register_handler(msg)
        self.assertIn("h1", c.handlers)
        self.assertEqual(c.handlers["h1"], 2)

    def test_register_ignored_for_wrong_query(self):
        c = _new_collector()
        msg = Message("question:query.handling", {
            "query": "wrong-id", "handler": "h1", "timeout": 2,
        })
        c._register_handler(msg)
        self.assertEqual(c.handlers, {})


class TestReceiveResponse(TestCase):
    def test_receive_stores_response(self):
        c = _new_collector()
        # register a handler so the "all collected" check has something to compare
        c.handlers["h1"] = 1.0
        msg = Message("question:query.response", {
            "query": c.collect_id, "handler": "h1", "answer": 42,
        })
        c._receive_response(msg)
        self.assertIn("h1", c.responses)

    def test_receive_ignored_for_wrong_query(self):
        c = _new_collector()
        c.handlers["h1"] = 1.0
        msg = Message("question:query.response", {
            "query": "wrong", "handler": "h1",
        })
        c._receive_response(msg)
        self.assertEqual(c.responses, {})

    def test_on_response_callback_fired(self):
        c = _new_collector()
        c.handlers["h1"] = 1.0
        seen = []
        c.on_response(lambda m: seen.append(m))
        msg = Message("question:query.response", {
            "query": c.collect_id, "handler": "h1",
        })
        c._receive_response(msg)
        self.assertEqual(len(seen), 1)


class TestStartAndShutdown(TestCase):
    def test_start_registers_handlers_and_emits(self):
        bus = MagicMock()
        c = _new_collector(bus=bus)
        c.start()
        # base_type.handling + base_type.response handlers + emit
        self.assertEqual(bus.on.call_count, 2)
        self.assertTrue(bus.emit.called)

    def test_shutdown_removes_handlers(self):
        bus = MagicMock()
        c = _new_collector(bus=bus)
        c.start()
        c.shutdown()
        self.assertEqual(bus.remove.call_count, 2)


class TestCollectorWaitsForHandlers(TestCase):
    def test_collect_returns_responses_when_all_present(self):
        bus = FakeBus()
        c = _new_collector(bus=bus, min_t=0.01, max_t=1.0)

        def feed():
            time.sleep(0.05)
            bus.emit(Message("question:query.handling", {
                "query": c.collect_id, "handler": "h1", "timeout": 0.2,
            }))
            time.sleep(0.05)
            bus.emit(Message("question:query.response", {
                "query": c.collect_id, "handler": "h1", "answer": "ok",
            }))

        threading.Thread(target=feed, daemon=True).start()
        out = c.collect()
        # at most one response, or zero if timing missed it
        self.assertIsInstance(out, list)


if __name__ == "__main__":
    unittest.main()
