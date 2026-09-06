"""The optional single-writer outbound queue (``websocket.async_sender``).

Every emitter thread otherwise serializes on the websocket send lock;
measured ~23ms per emit under a 400-client load in a ~20-thread process vs
~1ms idle. With the async sender, emit() enqueues (microseconds) and one
daemon thread owns the socket: ordering preserved, errors logged per frame.
"""
import threading
import json
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
        # the frame is DEQUEUED but still inside client.send(): flush must
        # NOT report success on queue emptiness alone
        self.assertFalse(self.c.flush(0.3),
                         "flush must wait for the socket write, not just "
                         "queue emptiness")
        gate.set()
        self.assertTrue(self.c.flush(5))
        self.assertEqual(len(sent), 1,
                         "flush success implies the write completed")

    def test_close_survives_blocked_sender(self):
        """close() must not crash the drain loop nor hang when the sender
        is stuck inside a socket write."""
        gate = threading.Event()
        sent = []

        def stuck_send(payload):
            gate.wait(10)
            sent.append(payload)
        self.c.client.send.side_effect = stuck_send
        self.c.emit(Message("unit.test", {"n": 1}))
        sender = self.c._sender_thread
        t0 = time.monotonic()
        self.c.close()   # flush times out, join times out -- returns anyway
        self.assertLess(time.monotonic() - t0, 10, "close() must not hang")
        gate.set()
        sender.join(timeout=5)
        self.assertFalse(sender.is_alive())
        self.assertEqual(len(sent), 1,
                         "the in-flight frame still completes after close")

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
        # a retry of the failed {"n": 1} frame followed by dropping {"n": 2}
        # would also leave exactly one frame here, so name which one landed
        self.assertEqual(json.loads(sent[0])["data"]["n"], 2)

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


class TestAsyncSenderCloseOrdering(TestCase):
    """close() must not close the socket out from under the sender."""

    def setUp(self):
        self.c = _client(True)

    def tearDown(self):
        try:
            self.c.close()
        except Exception:
            pass

    def test_socket_closes_only_after_the_sender_drains(self):
        order = []
        first_write = threading.Event()
        release = threading.Event()

        def slow_send(payload):
            if not first_write.is_set():
                first_write.set()
                # the first write blocks; the second frame is queued behind it
                release.wait(5)
            order.append(("send", json.loads(payload)["data"]["seq"]))

        self.c.client.send.side_effect = slow_send
        self.c.client.close.side_effect = lambda *a, **k: order.append(("close", None))

        self.c.emit(Message("unit.test", {"seq": 0}))
        self.assertTrue(first_write.wait(5), "sender never started writing")
        self.c.emit(Message("unit.test", {"seq": 1}))

        closer = threading.Thread(target=self.c.close, daemon=True)
        closer.start()
        # close() is now waiting on the blocked sender; the socket must NOT
        # have been closed yet
        time.sleep(0.2)
        self.assertNotIn(("close", None), order,
                         "socket closed while the sender still had frames")

        release.set()
        closer.join(timeout=10)
        self.assertFalse(closer.is_alive())

        # both frames went out, and only then did the socket close
        self.assertEqual(order, [("send", 0), ("send", 1), ("close", None)])

    def test_emit_after_close_starts_is_refused(self):
        release = threading.Event()
        sent = []

        def slow_send(payload):
            release.wait(5)
            sent.append(json.loads(payload)["data"]["seq"])

        self.c.client.send.side_effect = slow_send
        self.c.emit(Message("unit.test", {"seq": 0}))

        closer = threading.Thread(target=self.c.close, daemon=True)
        closer.start()
        time.sleep(0.2)
        # nothing may join the queue behind the stop sentinel
        self.c.emit(Message("unit.test", {"seq": 99}))

        release.set()
        closer.join(timeout=10)
        self.assertEqual(sent, [0])
