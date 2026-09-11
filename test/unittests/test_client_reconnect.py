"""Reconnect behaviour of MessageBusClient after the bus goes away.

The bus is a pod behind a Kubernetes Service in the deployments that hit
this: while the pod is being recreated, connect() fails with EPERM (Cilium's
socket load balancer rejects connects to a Service with no ready endpoint)
for minutes. Each failure used to (a) log a full traceback plus a spurious
'error' event and (b) reconnect by recursing into run_forever() from inside
the websocket-client callback, so the stack grew with every failed attempt,
never unwound while the bus stayed up, and once the recursion limit was
reached the receive thread died and the client never reconnected again.
"""
import base64
import errno
import hashlib
import inspect
import socket
import threading
import time
import unittest
from unittest import TestCase
from unittest.mock import MagicMock, patch

from pyee import EventEmitter

import ovos_bus_client.client.client as client_module
from ovos_bus_client.client.client import MessageBusClient
from websocket import WebSocketAddressException, WebSocketTimeoutException


def _is_transport_error(error):
    classify = getattr(client_module, "_is_transport_error", None)
    if classify is None:  # pre-fix code: nothing is a transport error
        return False
    return classify(error)


def _mocked_client():
    bus = MessageBusClient(emitter=EventEmitter())
    bus.client = MagicMock()
    bus.client.keep_running = False
    bus.connected_event.set()
    bus.retry = 0
    return bus


class TestTransportErrorClassification(TestCase):
    def test_socket_level_failures_are_transport_errors(self):
        for err in (PermissionError(errno.EPERM, "Operation not permitted"),
                    OSError(errno.EHOSTUNREACH, "No route to host"),
                    OSError(errno.ENETUNREACH, "Network is unreachable"),
                    OSError(errno.ETIMEDOUT, "Connection timed out"),
                    OSError(errno.EBADF, "Bad file descriptor"),
                    OSError(errno.ENOTCONN, "not connected"),
                    BrokenPipeError(errno.EPIPE, "Broken pipe"),
                    ConnectionAbortedError(),
                    socket.gaierror(-2, "Name or service not known"),
                    socket.timeout("timed out"),
                    WebSocketAddressException("unresolvable"),
                    WebSocketTimeoutException("timed out")):
            with self.subTest(err=err):
                self.assertTrue(_is_transport_error(err))

    def test_programming_errors_are_not(self):
        for err in (RuntimeError("boom"), ValueError("bad"),
                    OSError(errno.ENOSPC, "No space left on device"),
                    KeyError("x")):
            with self.subTest(err=err):
                self.assertFalse(_is_transport_error(err))


class TestOnErrorTransportErrors(TestCase):
    def test_eperm_on_connect_is_a_warning_not_a_traceback(self):
        # Regression: EPERM from connect() went down the "unexpected
        # exception" path -- LOG.exception with the full (ever-growing)
        # traceback and an 'error' event that, with no listener, pyee
        # re-raises, producing a second traceback per attempt.
        bus = _mocked_client()
        errors = []
        bus.emitter.on("error", lambda e: errors.append(e))
        with patch("ovos_bus_client.client.client.LOG") as log:
            bus.on_error(PermissionError(errno.EPERM, "Operation not permitted"))
        self.assertEqual(errors, [])
        log.exception.assert_not_called()
        self.assertTrue(any("reachable" in str(c) for c in log.warning.call_args_list))
        self.assertFalse(bus.connected_event.is_set())
        # ... and a reconnect is scheduled for run_forever()'s loop
        self.assertIsNotNone(bus._reconnect_delay)

    def test_unexpected_exception_keeps_traceback_and_error_event(self):
        bus = _mocked_client()
        errors = []
        bus.emitter.on("error", lambda e: errors.append(e))
        with patch("ovos_bus_client.client.client.LOG") as log:
            bus.on_error(RuntimeError("boom"))
        self.assertEqual(len(errors), 1)
        log.exception.assert_called()
        self.assertIsNotNone(bus._reconnect_delay)

    def test_no_reconnect_scheduled_when_closing(self):
        bus = _mocked_client()
        bus._closing = True
        bus.on_error(PermissionError(errno.EPERM, "Operation not permitted"))
        self.assertIsNone(bus._reconnect_delay)


class TestBackoff(TestCase):
    def test_doubles_to_a_bound_with_jitter_and_resets_on_open(self):
        bus = _mocked_client()
        bus.retry = bus.RECONNECT_INITIAL_S
        expected = [5, 10, 20, 40, 60, 60, 60]
        for base in expected:
            delay = bus._next_reconnect_delay()
            self.assertGreaterEqual(delay, base * (1 - bus.RECONNECT_JITTER))
            self.assertLessEqual(delay, base * (1 + bus.RECONNECT_JITTER))
        self.assertEqual(bus.retry, bus.RECONNECT_MAX_S)
        bus.client.send = MagicMock()
        bus.on_open()
        self.assertEqual(bus.retry, bus.RECONNECT_INITIAL_S)

    def test_zero_retry_means_no_wait(self):
        bus = _mocked_client()
        bus.retry = 0
        self.assertEqual(bus._next_reconnect_delay(), 0.0)


class _FailingApp:
    """Stands in for websocket.WebSocketApp: run_forever() reports one
    failure through on_error, exactly like websocket-client's
    handleDisconnect() does from inside run_forever(), then returns. The
    depth of the stack at that moment is recorded. After ``failures``
    attempts the app 'connects' and blocks until closed."""
    attempts = 0
    depths = []
    failures = 3
    connected = threading.Event()
    keep_running = True
    url = "ws://bus"

    def __init__(self, bus):
        self.bus = bus
        self.closed = threading.Event()

    def close(self):
        self.keep_running = False
        self.closed.set()

    def run_forever(self):
        type(self).attempts += 1
        type(self).depths.append(len(inspect.stack()))
        if type(self).attempts <= type(self).failures:
            self.bus.on_error(self, PermissionError(errno.EPERM, "nope"))
            return
        self.bus.on_open()
        type(self).connected.set()
        self.closed.wait(5)


class TestReconnectLoopDoesNotNest(TestCase):
    def setUp(self):
        _FailingApp.attempts = 0
        _FailingApp.depths = []
        _FailingApp.connected.clear()

    def test_stack_depth_is_constant_across_failed_attempts(self):
        # Regression: reconnecting from inside on_error() nested every new
        # run_forever() inside the failed one, so the depth grew by a fixed
        # number of frames per failed attempt and never came back down.
        bus = _mocked_client()
        bus.retry = 0
        bus.create_client = lambda: _FailingApp(bus)
        bus.client = bus.create_client()
        bus.client.send = MagicMock()
        t = bus.run_in_thread()
        self.assertTrue(_FailingApp.connected.wait(5))
        self.assertEqual(_FailingApp.attempts, _FailingApp.failures + 1)
        self.assertEqual(len(set(_FailingApp.depths)), 1,
                         f"stack grew across attempts: {_FailingApp.depths}")
        bus.close()
        t.join(2)
        self.assertFalse(t.is_alive())

    def test_survives_more_failed_attempts_than_the_recursion_limit_allows(self):
        # The old recursion consumed several frames per failed attempt; with
        # a limit this low it died within a handful of attempts and never
        # reconnected. The loop must not care how many attempts it takes.
        _FailingApp.failures = 40
        bus = _mocked_client()
        bus.retry = 0
        bus.create_client = lambda: _FailingApp(bus)
        bus.client = bus.create_client()
        bus.client.send = MagicMock()
        limit = 120
        result = {}

        def run():
            import sys
            old = sys.getrecursionlimit()
            sys.setrecursionlimit(limit)
            try:
                bus.run_forever()
            except RecursionError:
                result["died"] = True
            finally:
                sys.setrecursionlimit(old)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        try:
            self.assertTrue(_FailingApp.connected.wait(10),
                            "client never reconnected")
            self.assertNotIn("died", result)
        finally:
            _FailingApp.failures = 3
            bus.close()
            t.join(2)

    def test_close_interrupts_the_backoff_wait(self):
        bus = _mocked_client()
        bus.retry = 30
        bus.create_client = lambda: _FailingApp(bus)
        bus.client = bus.create_client()
        t = bus.run_in_thread()
        deadline = time.monotonic() + 5
        while bus._reconnect_delay is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNotNone(bus._reconnect_delay)
        started = time.monotonic()
        bus.close()
        t.join(2)
        self.assertFalse(t.is_alive())
        self.assertLess(time.monotonic() - started, 2)

    def test_run_forever_returns_when_the_app_exits_without_an_error(self):
        # No on_error() report (e.g. close() from the outside): the loop
        # must return, not spin up another websocket.
        bus = _mocked_client()
        bus.client = MagicMock()
        bus.create_client = MagicMock()
        bus.run_forever()
        bus.client.run_forever.assert_called_once()
        bus.create_client.assert_not_called()


# --- end to end over a real socket -----------------------------------------

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class _TinyWsServer:
    """Accepts websocket upgrades on 127.0.0.1 and holds the connections."""

    def __init__(self, port):
        self.conns = []
        self.lock = threading.Lock()
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", port))
        self.srv.listen(8)
        self.port = self.srv.getsockname()[1]
        self.t = threading.Thread(target=self._accept_loop, daemon=True)
        self.t.start()

    def _accept_loop(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(c,), daemon=True).start()

    def _serve(self, c):
        with self.lock:
            self.conns.append(c)
        try:
            req = b""
            while b"\r\n\r\n" not in req:
                chunk = c.recv(4096)
                if not chunk:
                    return
                req += chunk
            key = b""
            for line in req.split(b"\r\n"):
                if line.lower().startswith(b"sec-websocket-key:"):
                    key = line.split(b":", 1)[1].strip()
            accept = base64.b64encode(
                hashlib.sha1(key + _GUID.encode()).digest()).decode()
            c.sendall(("HTTP/1.1 101 Switching Protocols\r\n"
                       "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                       f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
            while c.recv(65536):
                pass
        except OSError:
            return

    def kill(self):
        for s in (self.srv,):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()
        self.t.join(2)
        with self.lock:
            for c in self.conns:
                try:
                    c.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                c.close()
            self.conns.clear()


class TestReconnectOverARealSocket(TestCase):
    """The production sequence: the bus pod goes away, connect() answers
    EPERM while the Service has no endpoint, the pod comes back on the same
    address, the client must be back on the bus at the same stack depth."""

    def test_recovers_after_eperm_outages_without_growing_the_stack(self):
        server = _TinyWsServer(0)
        port = server.port
        gate = {"armed": False, "attempts": 0}
        real_connect = socket.socket.connect

        def connect(sock, addr):
            if gate["armed"] and addr[1] == port:
                gate["attempts"] += 1
                raise PermissionError(errno.EPERM, "Operation not permitted")
            return real_connect(sock, addr)

        depths = []
        opened = threading.Event()
        real_on_open = MessageBusClient.on_open

        def on_open(self, *a):
            depths.append(len(inspect.stack()))
            real_on_open(self, *a)
            self.retry = 0.1
            opened.set()

        with patch.object(socket.socket, "connect", connect), \
                patch.object(MessageBusClient, "on_open", on_open), \
                patch.object(MessageBusClient, "RECONNECT_MAX_S", 0.2):
            bus = MessageBusClient(host="127.0.0.1", port=port,
                                   emitter=EventEmitter())
            bus.retry = 0.1
            t = bus.run_in_thread()
            self.assertTrue(opened.wait(5), "initial connect failed")
            try:
                for _ in range(3):
                    opened.clear()
                    gate["armed"] = True
                    gate["attempts"] = 0
                    server.kill()
                    time.sleep(0.6)
                    server = _TinyWsServer(port)
                    gate["armed"] = False
                    self.assertTrue(opened.wait(5), "client did not reconnect")
                    self.assertGreater(gate["attempts"], 0)
                    self.assertTrue(t.is_alive())
            finally:
                bus.close()
                server.kill()
        self.assertEqual(len(set(depths)), 1,
                         f"stack grew across reconnects: {depths}")


class TestCloseDuringClientReplacement(TestCase):
    """close() landing while create_client() is still building the next one.

    The websocket close() reaches is the one the loop was holding, not the
    replacement, so without serialising the two the loop installs the new
    client and starts it after close() has already returned.
    """

    def test_a_client_created_during_close_never_runs(self):
        bus = _mocked_client()
        building = threading.Event()
        release = threading.Event()
        replacement = MagicMock()
        replacement.keep_running = False
        started = []

        def create_client():
            building.set()
            release.wait(5)
            return replacement

        def run_forever():
            # first pass: report a disconnect so the loop reconnects once
            bus._reconnect_delay = 0
            return None

        bus.create_client = create_client
        bus.client.run_forever = run_forever
        replacement.run_forever = lambda: started.append(True)

        loop = threading.Thread(target=bus.run_forever, daemon=True)
        loop.start()
        self.assertTrue(building.wait(5), "loop never reached create_client()")

        closing = threading.Thread(target=bus.close, daemon=True)
        closing.start()
        # close() must not be waiting on the replacement to exist
        time.sleep(0.1)
        release.set()
        closing.join(5)
        loop.join(5)

        self.assertFalse(loop.is_alive(), "reconnect loop outlived close()")
        self.assertEqual(started, [], "replacement client ran after close()")
        replacement.close.assert_called_once()
        # close() closed the websocket the loop was holding, not the one it
        # had not published yet
        bus.client.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
