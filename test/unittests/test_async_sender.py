"""The optional single-writer outbound queue (``websocket.async_sender``).

Every emitter thread otherwise serializes on the websocket send lock;
measured ~23ms per emit under a 400-client load in a ~20-thread process vs
~1ms idle. With the async sender, emit() enqueues (microseconds) and one
daemon thread owns the socket: ordering preserved, errors logged per frame.
"""
import queue
import threading
import json
import time
import unittest
from collections import defaultdict
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
        sent_by_thread = defaultdict(list)
        lock = threading.Lock()

        def record(payload):
            data = json.loads(payload)["data"]
            with lock:
                sent_by_thread[data["thread"]].append(data["seq"])
        self.c.client.send.side_effect = record

        n_threads, n_msgs = 8, 50

        def worker(tid):
            for i in range(n_msgs):
                self.c.emit(Message("unit.test", {"thread": tid, "seq": i}))

        threads = [threading.Thread(target=worker, args=(tid,))
                   for tid in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(self.c.flush(5))
        deadline = time.monotonic() + 2
        while (sum(len(v) for v in sent_by_thread.values()) < n_threads * n_msgs
               and time.monotonic() < deadline):
            time.sleep(0.01)
        for tid in range(n_threads):
            self.assertEqual(sent_by_thread[tid], list(range(n_msgs)),
                             f"thread {tid}'s frames arrived out of order")

    def test_overflow_drops_without_blocking(self):
        """A full queue must drop, not block, and must count the drop."""
        # mutate maxsize in place -- the running sender thread holds a LOCAL
        # reference to this same queue object, so swapping it for a new
        # Queue() would leave the sender draining the old one forever
        self.c._sender_queue.maxsize = 2
        release = threading.Event()
        self.c.client.send.side_effect = lambda payload: release.wait(5)

        # occupies the sender thread inside the blocked send() above
        self.c.emit(Message("unit.test", {"seq": 0}))
        deadline = time.monotonic() + 2
        while self.c._sender_queue.qsize() > 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        # fill the now-empty two-slot queue
        self.c.emit(Message("unit.test", {"seq": 1}))
        self.c.emit(Message("unit.test", {"seq": 2}))

        with patch("ovos_bus_client.client.client.LOG.error") as mock_error:
            t0 = time.monotonic()
            self.c.emit(Message("unit.test", {"seq": 3}))
            elapsed_ms = (time.monotonic() - t0) * 1000
        self.assertLess(elapsed_ms, 100,
                         "emit() must return immediately on overflow, "
                         "not block waiting for room")
        mock_error.assert_called_once()
        self.assertEqual(self.c._sender_dropped, 1)
        release.set()

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

    def test_wait_for_response_survives_a_slow_write(self):
        """The response window must cover the time the request itself
        spends getting onto the wire, not just the caller's raw timeout.

        A frame that takes 0.8s to reach the socket, waited on with a 0.5s
        response timeout, must still be answered: wait_for_response has to
        flush the outbound queue before it starts counting down the wait,
        otherwise the timeout can expire while the request is still queued.
        """
        def slow_send(payload):
            time.sleep(0.8)
            if "probe.req" in payload:
                self.c.emitter.emit("probe.req.response",
                                    Message("probe.req.response", {"ok": True}))
        self.c.client.send.side_effect = slow_send

        result = self.c.wait_for_response(Message("probe.req"), timeout=0.5)
        self.assertIsNotNone(
            result, "wait_for_response must not report silence for a "
                    "request that had not yet reached the socket")
        self.assertTrue(result.data.get("ok"))

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
        q = self.c._sender_queue
        self.c.emit(Message("unit.test", {"seq": 0}))
        # wait for the sender to pick seq 0 up (and block inside slow_send
        # on it), so the queue is empty before close() adds its sentinel --
        # otherwise the size checks below race the sender's own scheduling
        deadline = time.monotonic() + 5
        while q.qsize() > 0 and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertEqual(q.qsize(), 0, "sender never dequeued seq 0")

        closer = threading.Thread(target=self.c.close, daemon=True)
        closer.start()
        # the queue reference is still live here: close() only clears it
        # AFTER sender.join() returns, and the sender is stuck on `release`
        deadline = time.monotonic() + 5
        while (not (self.c._sender_closing and q.qsize() >= 1)
               and time.monotonic() < deadline):
            time.sleep(0.001)
        self.assertTrue(self.c._sender_closing,
                        "close() never set the admission gate")
        size_before = q.qsize()
        self.c.emit(Message("unit.test", {"seq": 99}))
        size_after = q.qsize()
        self.assertEqual(size_after, size_before,
                         "emit() must not enqueue once close() has started, "
                         "not merely land behind the stop sentinel")

        release.set()
        closer.join(timeout=10)
        self.assertEqual(sent, [0])
