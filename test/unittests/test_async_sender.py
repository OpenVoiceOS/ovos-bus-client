"""The optional single-writer outbound queue (``websocket.async_sender``).

Every emitter thread otherwise serializes on the websocket send lock;
measured ~23ms per emit under a 400-client load in a ~20-thread process vs
~1ms idle. With the async sender, emit() enqueues (microseconds) and one
daemon thread owns the socket: ordering preserved, errors logged per frame.
"""
import threading
import time
import unittest
from unittest import TestCase
from unittest.mock import MagicMock, patch

from ovos_bus_client.client.client import MessageBusClient
from ovos_bus_client.message import Message


def _client(async_sender: bool) -> MessageBusClient:
    env = {"OVOS_BUS_ASYNC_SENDER": "1" if async_sender else "0"}
    with patch.dict("os.environ", env):
        c = MessageBusClient()
    c.client = MagicMock()
    c.started_running = True
    c.connected_event.set()
    return c


class TestAsyncSenderOff(TestCase):
    def test_default_is_synchronous(self):
        c = _client(False)
        self.assertIsNone(c._sender_queue)
        self.assertIsNone(c._sender_thread)
        c.emit(Message("unit.test", {"n": 1}))
        c.client.send.assert_called()  # sent inline, no thread involved

    def test_flush_is_noop_true(self):
        c = _client(False)
        self.assertTrue(c.flush())


class TestAsyncSenderOn(TestCase):
    def setUp(self):
        self.c = _client(True)

    def tearDown(self):
        self.c.close()

    def test_emit_returns_before_send_and_delivers(self):
        gate = threading.Event()
        sent = []

        def slow_send(payload):
            gate.wait(5)
            sent.append(payload)
        self.c.client.send.side_effect = slow_send

        t0 = time.monotonic()
        self.c.emit(Message("unit.test", {"n": 1}))
        emit_ms = (time.monotonic() - t0) * 1000
        self.assertLess(emit_ms, 100, "emit must not block on the socket")
        self.assertEqual(sent, [])
        gate.set()
        self.assertTrue(self.c.flush(5))
        deadline = time.monotonic() + 2
        while not sent and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(sent), 1)

    def test_order_preserved_across_threads(self):
        sent = []
        self.c.client.send.side_effect = lambda p: sent.append(p)
        for i in range(50):
            self.c.emit(Message("unit.test", {"seq": i}))
        self.assertTrue(self.c.flush(5))
        deadline = time.monotonic() + 2
        while len(sent) < 50 and time.monotonic() < deadline:
            time.sleep(0.01)
        seqs = [__import__("json").loads(p)["data"]["seq"] for p in sent]
        self.assertEqual(seqs, list(range(50)), "FIFO order must hold")

    def test_send_error_does_not_kill_the_sender(self):
        calls = {"n": 0}
        sent = []

        def flaky(payload):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            sent.append(payload)
        self.c.client.send.side_effect = flaky
        self.c.emit(Message("unit.test", {"n": 1}))   # errors in sender thread
        self.c.emit(Message("unit.test", {"n": 2}))   # must still deliver
        self.assertTrue(self.c.flush(5))
        deadline = time.monotonic() + 2
        while not sent and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(sent), 1)

    def test_close_drains_pending_frames(self):
        sent = []
        self.c.client.send.side_effect = lambda p: sent.append(p)
        for i in range(20):
            self.c.emit(Message("unit.test", {"seq": i}))
        self.c.close()
        self.assertEqual(len(sent), 20, "close() must drain the queue first")
        self.assertIsNone(self.c._sender_thread)


if __name__ == "__main__":
    unittest.main()
