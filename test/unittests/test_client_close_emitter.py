"""Regression coverage for close() shutting the emitter down (as its LAST
step, after the receiver thread join) and joining the dispatch thread, plus
on_error()'s reconnect path respecting _closing before each of its emits.
Root cause: "cannot schedule new futures after interpreter shutdown" on a
clean stop, see client.py close()/on_error()."""
import threading
import time
import unittest
from unittest import TestCase
from unittest.mock import MagicMock, patch

from ovos_bus_client.client.client import MessageBusClient
from websocket import WebSocketException


def _mocked_client():
    bus = MessageBusClient()
    bus.client = MagicMock()
    bus.client.keep_running = False
    bus.connected_event.set()
    bus.retry = 0
    return bus


class TestCloseShutsEmitterDown(TestCase):
    def test_close_calls_emitter_shutdown(self):
        bus = _mocked_client()
        bus.emitter = MagicMock()
        bus.close()
        bus.emitter.shutdown.assert_called_once_with(wait=False)
        self.assertTrue(bus._closing)

    def test_close_tolerates_emitter_without_shutdown(self):
        bus = _mocked_client()
        bus.emitter = MagicMock(spec=[])  # no .shutdown attribute
        bus.close()  # must not raise
        self.assertTrue(bus._closing)

    def test_close_delivers_the_close_event_before_shutting_the_emitter_down(self):
        # Finding A of the review: shutting the emitter down BEFORE the real
        # on_close callback has a chance to fire silently dropped the
        # 'close' event any bus.on("close", ...) listener relies on to
        # detect a disconnect. websocket-client actually invokes on_close()
        # from inside the receive loop -- the SAME thread run_in_thread()
        # started and close() joins -- as that loop unwinds in response to
        # client.close(). A fake run_forever() that blocks until
        # client.close() is actually called, then invokes on_close() itself,
        # models that sequencing deterministically (no thread-scheduling
        # race): if the emitter were torn down before client.close() (as it
        # was pre-fix), on_close()'s emit would already be too late/guarded
        # off regardless of how the two threads happen to interleave.
        bus = _mocked_client()
        seen = []
        bus.emitter.on("close", lambda *_: seen.append(True))
        close_called = threading.Event()
        bus.client.close = MagicMock(side_effect=close_called.set)

        def fake_run_forever():
            close_called.wait(timeout=2)
            bus.on_close()

        bus.run_forever = fake_run_forever
        bus.run_in_thread()
        bus.close()
        self.assertEqual(seen, [True])

    def test_emit_from_background_thread_after_close_does_not_propagate(self):
        # Simulate the real failure: after close() the emitter refuses new
        # work. A background thread emitting anyway must not let the
        # RuntimeError escape past on_error()/on_close()/on_open().
        bus = _mocked_client()
        bus.close()
        bus.emitter.emit = MagicMock(
            side_effect=RuntimeError(
                "cannot schedule new futures after interpreter shutdown"))
        results = []

        def worker():
            try:
                bus.on_close()
                results.append("ok")
            except RuntimeError:
                results.append("raised")

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=2)
        self.assertEqual(results, ["ok"])


class TestOnErrorRespectsClosing(TestCase):
    @patch("ovos_bus_client.client.client.time.sleep", MagicMock())
    def test_on_error_skips_emit_when_closing(self):
        # Narrowed guard (review ruling): _closing no longer suppresses the
        # 'error' event itself -- a real connection error is delivered the
        # same way 'close' is -- it only suppresses the RECONNECT path
        # ('reconnecting' emit, retry sleep, recursive run_forever()).
        bus = _mocked_client()
        bus._closing = True
        bus.emitter = MagicMock()
        bus.on_error(RuntimeError("boom"))
        bus.emitter.emit.assert_called_once_with('error', unittest.mock.ANY)

    @patch("ovos_bus_client.client.client.time.sleep", MagicMock())
    def test_on_error_skips_reconnecting_when_closing(self):
        bus = _mocked_client()
        bus.client.keep_running = True
        bus.retry = 0
        bus.create_client = MagicMock(return_value=MagicMock())
        bus.run_forever = MagicMock(side_effect=WebSocketException())

        real_emit = bus.emitter.emit
        calls = []

        def tracking_emit(*args, **kwargs):
            calls.append(args[0] if args else None)
            if args and args[0] == 'error':
                bus._closing = True
            return real_emit(*args, **kwargs)

        bus.emitter.emit = MagicMock(side_effect=tracking_emit)
        bus.on_error(RuntimeError("boom"))
        self.assertIn('error', calls)
        self.assertNotIn('reconnecting', calls)

    @patch("ovos_bus_client.client.client.time.sleep", MagicMock())
    def test_on_error_swallows_runtimeerror_from_emitter(self):
        bus = _mocked_client()
        bus.emitter.emit = MagicMock(
            side_effect=RuntimeError(
                "cannot schedule new futures after interpreter shutdown"))
        bus.on_error(RuntimeError("boom"))  # must not raise


class TestRunInThreadJoin(TestCase):
    def test_close_joins_run_in_thread(self):
        bus = MessageBusClient()
        bus.client = MagicMock()
        bus.client.keep_running = False

        def fake_run_forever():
            time.sleep(0.05)

        bus.run_forever = fake_run_forever
        t = bus.run_in_thread()
        self.assertIs(bus._run_thread, t)
        bus.close()
        self.assertFalse(t.is_alive())
        self.assertIsNone(bus._run_thread)


if __name__ == "__main__":
    unittest.main()
