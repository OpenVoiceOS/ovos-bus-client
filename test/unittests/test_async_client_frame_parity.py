"""Frame-parity test: AsyncMessageBusClient.emit() must put the exact same
wire frames as MessageBusClient.emit() -- including the legacy intent/
namespace compat twins -- for the same Message.

Confirmed defect (see docs/prerelease-quirks.md 2.8.5a2): the async client's
emit() originally only sent the canonical frame, silently dropping the
V0-compat dual-emit twins that the sync client's emit() sends via
_send_legacy_intent_twin/_send_legacy_namespace_twin. This asserts frame-for-
frame equality (as parsed JSON, so key ordering doesn't matter) between the
two clients' wire output for both an intent topic and a MIGRATION_MAP
namespace topic.
"""
import asyncio
import json
import unittest
from threading import Event
from unittest.mock import AsyncMock, MagicMock

from pyee import EventEmitter

from ovos_bus_client.client.async_client import AsyncMessageBusClient
from ovos_bus_client.client.client import MessageBusClient
from ovos_bus_client.message import Message


def _sync_client() -> MessageBusClient:
    c = MessageBusClient.__new__(MessageBusClient)
    c.emitter = EventEmitter()
    c.client = MagicMock()
    from ovos_spec_tools import NamespaceTranslator
    c._translator = NamespaceTranslator(modernize=True, emit_legacy=True)
    c._wire_legacy_twins = True
    c._handler_guards = {}
    c._intent_pair_guards = {}
    c._dedup_registrations = {}
    c.wrapped_funcs = {}
    c.connected_event = Event()
    c.connected_event.set()
    c.started_running = True
    c.session_id = "default"
    return c


def _async_client() -> AsyncMessageBusClient:
    bus = AsyncMessageBusClient.__new__(AsyncMessageBusClient)
    bus.emitter = None
    bus._ws = AsyncMock()
    bus._ws.send = AsyncMock()
    bus._connected = asyncio.Event()
    bus._connected.set()
    from ovos_spec_tools import NamespaceTranslator
    bus._translator = NamespaceTranslator(modernize=True, emit_legacy=True)
    bus._wire_legacy_twins = True
    bus.session_id = "default"
    return bus


def _sync_frames(client, message):
    client.emit(message)
    return [json.loads(call.args[0]) for call in client.client.send.call_args_list]


def _async_frames(bus, message):
    async def _run():
        await bus.emit(message)
    asyncio.run(_run())
    return [json.loads(call.args[0]) for call in bus._ws.send.call_args_list]


class TestAsyncSyncFrameParity(unittest.TestCase):
    def _assert_parity(self, message_factory):
        sync = _sync_client()
        sync_frames = _sync_frames(sync, message_factory())

        aclient = _async_client()
        async_frames = _async_frames(aclient, message_factory())

        self.assertEqual(len(sync_frames), len(async_frames))
        self.assertGreater(len(sync_frames), 0)
        for s, a in zip(sync_frames, async_frames):
            self.assertEqual(s, a)

    def test_intent_topic_twin_parity(self):
        self._assert_parity(
            lambda: Message("skill:food.order", {"utterance": "pizza"},
                            {"session": {"session_id": "default"}}))

    def test_namespace_migration_topic_twin_parity(self):
        self._assert_parity(
            lambda: Message("ovos.utterance.speak", {"utterance": "hi"},
                            {"session": {"session_id": "default"}}))

    def test_plain_topic_no_twin_parity(self):
        self._assert_parity(
            lambda: Message("some.unrelated.topic", {"foo": "bar"},
                            {"session": {"session_id": "default"}}))


if __name__ == "__main__":
    unittest.main()
