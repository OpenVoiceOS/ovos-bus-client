# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
"""Unit tests for AsyncMessageBusClient, AsyncMessageWaiter, AsyncMessageCollector."""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from ovos_bus_client.client.async_client import (
    AsyncMessageBusClient,
    AsyncMessageCollector,
    AsyncMessageWaiter,
)
from ovos_bus_client.message import Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bus() -> AsyncMessageBusClient:
    """Return a bus whose websocket connection is pre-mocked (no real network)."""
    with patch("ovos_bus_client.client.async_client.load_message_bus_config") as mock_cfg:
        mock_cfg.return_value = MagicMock(host="localhost", port=8181,
                                          route="/core", ssl=False)
        bus = AsyncMessageBusClient()
    # Simulate an already-connected state
    bus._connected.set()
    ws_mock = AsyncMock()
    ws_mock.send = AsyncMock()
    bus._ws = ws_mock
    return bus


# ---------------------------------------------------------------------------
# AsyncMessageBusClient tests
# ---------------------------------------------------------------------------

class TestAsyncMessageBusClientInit(unittest.TestCase):
    def test_build_url_ws(self):
        url = AsyncMessageBusClient.build_url("localhost", 8181, "/core", False)
        self.assertEqual(url, "ws://localhost:8181/core")

    def test_build_url_wss(self):
        url = AsyncMessageBusClient.build_url("example.com", 443, "/core", True)
        self.assertEqual(url, "wss://example.com:443/core")

    def test_init_creates_emitter(self):
        bus = _make_bus()
        self.assertIsNotNone(bus.emitter)

    def test_url_property(self):
        bus = _make_bus()
        self.assertTrue(bus.url.startswith("ws://"))

    def test_connected_property_reflects_connection_state(self):
        # same attribute name/semantics as MessageBusClient.connected_event
        # being set -- ovos-busmon's _bus_is_connected() reads
        # getattr(bus, "connected", None) against either client.
        bus = _make_bus()
        self.assertTrue(bus.connected)
        bus._connected.clear()
        self.assertFalse(bus.connected)


class TestAsyncMessageBusClientEmit(unittest.IsolatedAsyncioTestCase):

    async def test_emit_sends_serialized_message(self):
        bus = _make_bus()
        msg = Message("test.event", {"key": "value"})
        await bus.emit(msg)
        bus._ws.send.assert_awaited_once()
        sent_raw = bus._ws.send.call_args[0][0]
        parsed = json.loads(sent_raw)
        self.assertEqual(parsed["type"], "test.event")
        self.assertEqual(parsed["data"]["key"], "value")

    async def test_emit_injects_session(self):
        bus = _make_bus()
        msg = Message("test.session.inject")
        self.assertNotIn("session", msg.context)
        await bus.emit(msg)
        self.assertIn("session", msg.context)

    async def test_emit_does_not_override_existing_session(self):
        bus = _make_bus()
        existing = {"session_id": "custom-123"}
        msg = Message("test.session.keep", context={"session": existing})
        await bus.emit(msg)
        self.assertEqual(msg.context["session"], existing)

    async def test_emit_connection_timeout_raises(self):
        bus = _make_bus()
        bus._connected.clear()
        msg = Message("test.timeout")
        # asyncio.wait_for fires before the internal 10s wait, so we get TimeoutError
        with self.assertRaises((ValueError, TimeoutError, asyncio.TimeoutError)):
            await asyncio.wait_for(bus.emit(msg), timeout=0.05)


# ---------------------------------------------------------------------------
# AsyncMessageWaiter tests
# ---------------------------------------------------------------------------

class TestAsyncMessageWaiter(unittest.IsolatedAsyncioTestCase):

    async def test_wait_receives_message(self):
        bus = _make_bus()
        waiter = AsyncMessageWaiter(bus, "my.event")
        msg = Message("my.event", {"x": 1})
        # Simulate message arrival
        bus.emitter.emit("my.event", msg)
        result = await waiter.wait(timeout=1.0)
        self.assertIsNotNone(result)
        self.assertEqual(result.msg_type, "my.event")

    async def test_wait_returns_none_on_timeout(self):
        bus = _make_bus()
        waiter = AsyncMessageWaiter(bus, "never.arrives")
        result = await waiter.wait(timeout=0.05)
        self.assertIsNone(result)

    async def test_wait_multiple_types(self):
        bus = _make_bus()
        waiter = AsyncMessageWaiter(bus, ["type.a", "type.b"])
        msg = Message("type.b")
        bus.emitter.emit("type.b", msg)
        result = await waiter.wait(timeout=1.0)
        self.assertEqual(result.msg_type, "type.b")

    async def test_one_shot_handler_not_called_twice(self):
        bus = _make_bus()
        waiter = AsyncMessageWaiter(bus, "one.shot")
        msg1 = Message("one.shot", {"n": 1})
        msg2 = Message("one.shot", {"n": 2})
        bus.emitter.emit("one.shot", msg1)
        bus.emitter.emit("one.shot", msg2)
        result = await waiter.wait(timeout=0.1)
        # Should only have captured the first
        self.assertEqual(result.data["n"], 1)


# ---------------------------------------------------------------------------
# on_message pipeline tests
# ---------------------------------------------------------------------------

class TestOnMessage(unittest.IsolatedAsyncioTestCase):

    async def test_on_message_emits_msg_type(self):
        bus = _make_bus()
        received = []
        bus.on("hello.world", lambda m: received.append(m))
        raw = json.dumps({"type": "hello.world", "data": {}, "context": {}})
        await bus._on_message(raw)
        await asyncio.sleep(0)  # allow emitter callbacks to fire
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].msg_type, "hello.world")

    async def test_on_message_emits_raw_message_event(self):
        bus = _make_bus()
        raw_received = []
        bus.on("message", lambda r: raw_received.append(r))
        raw = json.dumps({"type": "raw.test", "data": {}, "context": {}})
        await bus._on_message(raw)
        await asyncio.sleep(0)
        self.assertEqual(len(raw_received), 1)

    async def test_on_message_leaves_the_default_store_alone(self):
        # OVOS-SESSION-2 §5.1's arrival fold is orchestrator-intake-only (see
        # ovos_bus_client.client.client.MessageBusClient._take_inbound_session);
        # this is the async twin of that assertion.
        from ovos_bus_client.session import SessionManager
        SessionManager.reset_default_session()
        self.addCleanup(SessionManager.reset_default_session)
        stored = SessionManager.get_default_session()
        stored.lang = "pt-PT"
        stored.site_id = "kitchen"

        bus = _make_bus()
        # an omitted-field default carrier must not reset a field the store
        # has already moved past
        raw = json.dumps({"type": "recognizer_loop:utterance",
                          "data": {"utterances": ["hi"]},
                          "context": {"session": {"session_id": "default"}}})
        await bus._on_message(raw)
        self.assertIs(SessionManager.get_default_session(), stored)
        self.assertEqual(stored.lang, "pt-PT")
        self.assertEqual(stored.site_id, "kitchen")

        # nor does a fully-populated observed carrier overwrite it
        raw2 = json.dumps({"type": "recognizer_loop:utterance",
                           "data": {"utterances": ["hi"]},
                           "context": {"session": {"session_id": "default",
                                                    "site_id": "bedroom"}}})
        await bus._on_message(raw2)
        self.assertEqual(stored.site_id, "kitchen")

    async def test_on_message_still_updates_a_named_session(self):
        # a named-session carrier still goes through SessionManager.update()
        # exactly once (§2.2/#313 held-session handling), unlike the default
        # session above.
        from ovos_bus_client.session import SessionManager
        SessionManager.reset_default_session()
        self.addCleanup(SessionManager.reset_default_session)
        self.addCleanup(SessionManager.sessions.pop, "kitchen-sat", None)

        bus = _make_bus()
        with patch.object(SessionManager, "update",
                          wraps=SessionManager.update) as mock_update:
            raw = json.dumps({"type": "recognizer_loop:utterance",
                              "data": {"utterances": ["hi"]},
                              "context": {"session": {"session_id": "kitchen-sat",
                                                       "site_id": "bedroom"}}})
            await bus._on_message(raw)
            mock_update.assert_called_once()
            called_sess = mock_update.call_args[0][0]
            self.assertEqual(called_sess.session_id, "kitchen-sat")

    async def test_on_message_discards_malformed_session(self):
        """SESSION-1 §2.5: a non-object session carrier is dropped, not
        raised -- letting it escape would reach the recv loop's exception
        handler, which tears the connection down."""
        bus = _make_bus()
        received = []
        bus.on("ovos.test", lambda m: received.append(m))
        raw = json.dumps({"type": "ovos.test", "data": {},
                          "context": {"session": "oops"}})
        # must not raise
        await bus._on_message(raw)
        await asyncio.sleep(0)
        self.assertEqual(received, [])


# ---------------------------------------------------------------------------
# Event registration tests
# ---------------------------------------------------------------------------

class TestEventRegistration(unittest.TestCase):

    def test_on_and_remove(self):
        bus = _make_bus()
        calls = []
        handler = lambda m: calls.append(m)
        bus.on("test.on.remove", handler)
        bus.emitter.emit("test.on.remove", Message("test.on.remove"))
        self.assertEqual(len(calls), 1)
        bus.remove("test.on.remove", handler)
        bus.emitter.emit("test.on.remove", Message("test.on.remove"))
        self.assertEqual(len(calls), 1)  # still 1 — handler was removed

    def test_once_fires_once(self):
        bus = _make_bus()
        calls = []
        bus.once("test.once", lambda m: calls.append(m))
        bus.emitter.emit("test.once", Message("test.once"))
        bus.emitter.emit("test.once", Message("test.once"))
        self.assertEqual(len(calls), 1)

    def test_remove_all_listeners(self):
        bus = _make_bus()
        calls = []
        bus.on("ev", lambda m: calls.append(m))
        bus.on("ev", lambda m: calls.append(m))
        bus.remove_all_listeners("ev")
        bus.emitter.emit("ev", Message("ev"))
        self.assertEqual(calls, [])


# ---------------------------------------------------------------------------
# AsyncMessageCollector tests
# ---------------------------------------------------------------------------

class TestAsyncMessageCollector(unittest.IsolatedAsyncioTestCase):

    async def test_collect_returns_empty_without_handlers(self):
        bus = _make_bus()
        msg = Message("query.no.handlers")
        collector = AsyncMessageCollector(bus, msg,
                                          min_timeout=0.01, max_timeout=0.1)
        result = await collector.collect()
        self.assertEqual(result, [])

    async def test_collect_returns_responses(self):
        """Collector receives one handler's ack+response fired from a background task."""
        bus = _make_bus()
        query = Message("query.with.handler")
        collector = AsyncMessageCollector(bus, query,
                                          min_timeout=0.05, max_timeout=2.0)

        # Inject ack + response after a short delay (simulates a remote handler)
        async def inject():
            await asyncio.sleep(0.02)
            cid = query.context["__collect_id__"]
            handler_id = "h1"
            ack = Message("query.with.handler.handling",
                          data={"query": cid, "handler": handler_id, "timeout": 1.0})
            bus.emitter.emit("query.with.handler.handling", ack)
            resp = Message("query.with.handler.response",
                           data={"query": cid, "handler": handler_id, "result": "ok"})
            bus.emitter.emit("query.with.handler.response", resp)

        asyncio.ensure_future(inject())
        result = await asyncio.wait_for(collector.collect(), timeout=3.0)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].data["result"], "ok")

    async def test_wait_for_response_integration(self):
        """wait_for_response resolves when the response event is fired externally."""
        bus = _make_bus()
        request = Message("ping")

        # Simulate a remote handler that fires the response after emit completes
        async def inject_response():
            await asyncio.sleep(0.02)
            bus.emitter.emit("ping.response",
                              Message("ping.response", {"pong": True}))

        asyncio.ensure_future(inject_response())
        response = await bus.wait_for_response(request, timeout=2.0)
        self.assertIsNotNone(response)
        self.assertTrue(response.data["pong"])


if __name__ == "__main__":
    unittest.main()
