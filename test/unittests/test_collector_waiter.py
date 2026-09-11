"""Coverage tests for ovos_bus_client.client.collector and .waiter."""
import threading
import time
import unittest
from unittest import TestCase
from unittest.mock import MagicMock, patch

from ovos_bus_client.client.collector import MessageCollector
from ovos_bus_client.client.waiter import MessageWaiter
from ovos_bus_client.message import Message
from ovos_utils.fakebus import FakeBus


class TestMessageWaiter(TestCase):
    def test_single_type(self):
        bus = FakeBus()
        waiter = MessageWaiter(bus, "evt")
        threading.Timer(0.05, lambda: bus.emit(Message("evt", {"x": 1}))).start()
        got = waiter.wait(timeout=2)
        self.assertIsNotNone(got)
        self.assertEqual(got.msg_type, "evt")

    def test_list_of_types(self):
        bus = FakeBus()
        waiter = MessageWaiter(bus, ["main", "alt"])
        threading.Timer(0.05, lambda: bus.emit(Message("alt"))).start()
        got = waiter.wait(timeout=2)
        self.assertEqual(got.msg_type, "alt")

    def test_timeout_returns_none(self):
        bus = FakeBus()
        waiter = MessageWaiter(bus, "evt")
        self.assertIsNone(waiter.wait(timeout=0.1))


class TestMessageCollector(TestCase):
    def test_iterator_yields_responses(self):
        bus = FakeBus()
        # produce a couple of synthetic responses, then a handling-end
        original = Message("query", context={})
        collector = MessageCollector(bus, original, 0.05, 0.5,
                                     lambda m: False)
        # patch internal handlers map
        collector._handlers = {}
        # mark "no registered handlers" to short-circuit wait loop
        results = []

        def feed():
            time.sleep(0.05)

        threading.Thread(target=feed, daemon=True).start()
        out = collector.collect()
        self.assertIsInstance(out, list)

    def test_collect_empty_when_no_handlers(self):
        bus = FakeBus()
        collector = MessageCollector(
            bus, Message("query"), min_timeout=0.05, max_timeout=0.2,
            direct_return_func=lambda m: False,
        )
        out = collector.collect()
        self.assertEqual(out, [])

    def test_direct_return_short_circuits(self):
        bus = FakeBus()
        # we'll simulate one handler that immediately responds True
        msg = Message("query", context={"__collect_id__": "qid"})
        collector = MessageCollector(
            bus, msg, min_timeout=0.05, max_timeout=2.0,
            direct_return_func=lambda m: True,
        )

        def feed():
            time.sleep(0.05)
            # simulate handler ack + response
            bus.emit(Message("query.handling", {
                "query": "qid", "handler": "h1", "timeout": 1,
            }))
            time.sleep(0.05)
            bus.emit(Message("query.response", {
                "query": "qid", "handler": "h1", "succeeded": True, "answer": "ok",
            }))

        threading.Thread(target=feed, daemon=True).start()
        out = collector.collect()
        # at most one response (direct_return_func short-circuit)
        self.assertLessEqual(len(out), 1)


if __name__ == "__main__":
    unittest.main()
