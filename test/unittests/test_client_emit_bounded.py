"""Regression coverage for OVOS-MSG-1 issue #304: emit() calling _send()
blocked indefinitely once the connection was gone, because close() only
cleared connected_event -- a thread already parked in wait() only unblocks
on set(), never on clear(). Confirmed on dev against a real messagebus:
emit blocked 8s, close() returned in 2.00s, and the same emit was still
blocked 10s after close() returned.

OVOS-MSG-1 Non-goals put delivery guarantees and retry behaviour out of
scope, so bounding the wait and dropping the frame (rather than raising,
which emit() has never done on this path) is spec-compatible."""
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from ovos_bus_client.client import client as client_module
from ovos_bus_client.client.client import MessageBusClient
from ovos_bus_client.message import Message

BOUND_S = 25  # 10s first wait + 10s second wait + margin


def _disconnected_client():
    bus = MessageBusClient()
    bus.client = MagicMock()
    bus.client.keep_running = False
    bus.started_running = True
    bus._sender_queue = None
    # connected_event is left UNSET: this models the connection being gone.
    return bus


class TestEmitBoundedOnDisconnect(unittest.TestCase):
    def test_emit_returns_within_bound_and_warns_once(self):
        bus = _disconnected_client()
        msg = Message("some.message.type")

        with patch.object(client_module.LOG, "warning") as mock_warning:
            start = time.monotonic()
            bus.emit(msg)
            elapsed = time.monotonic() - start

        self.assertLess(
            elapsed, BOUND_S,
            f"emit() blocked for {elapsed:.2f}s instead of returning "
            f"within the {BOUND_S}s bound")

        warnings = [c for c in mock_warning.call_args_list
                    if "some.message.type" in c.args[0]]
        self.assertEqual(
            len(warnings), 1,
            f"expected exactly one warning naming the dropped message "
            f"type, got: {mock_warning.call_args_list}")

    def test_close_unblocks_an_emit_already_in_flight(self):
        bus = _disconnected_client()
        bus.emitter = MagicMock()
        bus._run_thread = None
        msg = Message("blocked.message.type")

        emit_done = threading.Event()
        emit_elapsed = {}

        def _do_emit():
            start = time.monotonic()
            bus.emit(msg)
            emit_elapsed["value"] = time.monotonic() - start
            emit_done.set()

        t = threading.Thread(target=_do_emit, daemon=True)
        t.start()
        time.sleep(0.5)  # let emit() get parked in the wait

        close_start = time.monotonic()
        bus.close()
        close_elapsed = time.monotonic() - close_start

        self.assertLess(
            close_elapsed, 5,
            f"close() itself blocked for {close_elapsed:.2f}s")

        unblocked = emit_done.wait(5)
        self.assertTrue(
            unblocked,
            "emit() did not return within 5s of close() returning")
        self.assertLess(emit_elapsed["value"], BOUND_S)


if __name__ == "__main__":
    unittest.main()
