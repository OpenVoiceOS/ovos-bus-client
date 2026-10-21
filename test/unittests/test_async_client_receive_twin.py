"""Receive-side compat-twin parity between AsyncMessageBusClient and
MessageBusClient.

Confirmed defect: AsyncMessageBusClient._on_message() neither popped the
intent/namespace compat-twin markers nor ran the RULE 1 namespace
counterpart dispatch / RULE 2 intent modernization that MessageBusClient.
on_message() does, so a listener on an async client saw both the intent twin
and its canonical frame fire the 'message' firehose, and a namespace-mapped
topic never reached a listener bound to its counterpart spelling.
"""
import asyncio
import unittest
from unittest.mock import AsyncMock

from pyee.asyncio import AsyncIOEventEmitter

from ovos_bus_client.client.async_client import AsyncMessageBusClient
from ovos_bus_client.message import Message
from ovos_spec_tools import NamespaceTranslator

CANONICAL_INTENT = "skill-food.jarbas:food.order"
LEGACY_INTENT = "skill-food.jarbas:food.order.intent"
CANONICAL_NS = "ovos.utterance.speak"
LEGACY_NS = "speak"


def _async_client() -> AsyncMessageBusClient:
    bus = AsyncMessageBusClient.__new__(AsyncMessageBusClient)
    bus.emitter = AsyncIOEventEmitter()
    bus._ws = AsyncMock()
    bus._ws.send = AsyncMock()
    bus._translator = NamespaceTranslator(modernize=True, emit_legacy=True)
    bus._wire_legacy_twins = True
    bus.session_id = "default"
    return bus


def _deliver(bus, msg_type, data=None, context=None):
    asyncio.run(bus._on_message(
        Message(msg_type, data or {}, context or {}).serialize()))


class TestIntentTwinFirehoseDedup(unittest.TestCase):
    def test_a_canonical_emit_relayed_as_both_wire_frames_fires_the_firehose_once(self):
        bus = _async_client()
        firehose = []
        bus.emitter.on("message", lambda m: firehose.append(m))
        got_canonical = []
        bus.emitter.on(CANONICAL_INTENT, lambda m: got_canonical.append(m))
        got_legacy = []
        bus.emitter.on(LEGACY_INTENT, lambda m: got_legacy.append(m))

        _deliver(bus, CANONICAL_INTENT, data={"a": 1})
        _deliver(bus, LEGACY_INTENT, data={"a": 1},
                context={"_intent_compat_twin": True})

        self.assertEqual(len(firehose), 1)
        self.assertEqual(len(got_canonical), 1)
        self.assertEqual(len(got_legacy), 1)

    def test_the_twin_marker_does_not_survive_onto_the_dispatched_message(self):
        bus = _async_client()
        seen = []
        bus.emitter.on(LEGACY_INTENT, lambda m: seen.append(m))
        _deliver(bus, LEGACY_INTENT, data={"a": 1},
                context={"_intent_compat_twin": True})
        self.assertNotIn("_intent_compat_twin", seen[0].context)


class TestNamespaceTwinCounterpartDispatch(unittest.TestCase):
    def test_a_legacy_namespace_emit_is_locally_mirrored_to_the_counterpart(self):
        bus = _async_client()
        got_canonical = []
        bus.emitter.on(CANONICAL_NS, lambda m: got_canonical.append(m))
        _deliver(bus, LEGACY_NS, data={"utterance": "hi"})
        self.assertEqual(len(got_canonical), 1)

    def test_a_marked_namespace_twin_is_not_re_dispatched_or_re_mirrored(self):
        bus = _async_client()
        firehose = []
        bus.emitter.on("message", lambda m: firehose.append(m))
        got_legacy = []
        bus.emitter.on(LEGACY_NS, lambda m: got_legacy.append(m))
        _deliver(bus, CANONICAL_NS, data={"utterance": "hi"})
        _deliver(bus, LEGACY_NS, data={"utterance": "hi"},
                context={"_namespace_compat_twin": True})
        self.assertEqual(len(firehose), 1)
        # the counterpart dispatch off the canonical frame already delivered
        # the legacy spelling locally once; the twin's own arrival adds none
        self.assertEqual(len(got_legacy), 1)


if __name__ == "__main__":
    unittest.main()
