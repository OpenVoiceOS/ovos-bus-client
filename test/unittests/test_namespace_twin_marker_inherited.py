"""A twin marker is only believed when a canonical frame was actually seen.

Confirmed defect, measured on two vintages by the harness lane
(knowledge/wiki/audits/gate-ledger/live-hivemind-fallback-two-vintages.md,
cell ovos-test-harness#84): a fallback skill on ovos-workshop 7.0.6 /
ovos-bus-client 1.5.0 is reached by a canonical ``ovos.fallback.ping``
through its legacy wire twin, and answers ``ovos.skills.fallback.pong``.

The old client builds that answer with ``reply()``, which OVOS-MSG-1 §5.2
requires to preserve "All other context keys ... unchanged". So the
``_namespace_compat_twin`` marker the emitter stamped on the PING is copied
onto the PONG. The pong is a new logical message and nothing canonical
carries it, but the modern receiver read the marker as "the canonical frame
already came" and suppressed it. The poll saw no answer at all.

The old client cannot be fixed: it is shipped, and it has no reason to strip
a key that is not its own. The receive rule is what has to change.
"""
import unittest
from threading import Event
from unittest.mock import MagicMock

from pyee import EventEmitter

from ovos_bus_client.client.client import (NAMESPACE_COMPAT_TWIN_KEY,
                                           MessageBusClient)
from ovos_bus_client.message import Message
from ovos_spec_tools import NamespaceTranslator

CANONICAL_PING = "ovos.fallback.ping"
LEGACY_PING = "ovos.skills.fallback.ping"
LEGACY_PONG = "ovos.skills.fallback.pong"
CANONICAL_PONG = "ovos.fallback.pong"


def _client():
    c = MessageBusClient.__new__(MessageBusClient)
    c.emitter = EventEmitter()
    c.client = MagicMock()
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


def _deliver(client, msg_type, data=None, context=None):
    client.on_message(Message(msg_type, data or {}, context or {}).serialize())


def _heard(client, *topics):
    got = []
    for topic in topics:
        client.on(topic, lambda message: got.append(message.msg_type))
    return got


class TestInheritedMarkerIsNotATwin(unittest.TestCase):

    def test_a_marked_pong_with_no_canonical_pong_is_delivered(self):
        """The defect. The legacy subscriber's answer must reach a listener."""
        client = _client()
        heard = _heard(client, LEGACY_PONG, CANONICAL_PONG)

        # exactly what the old subscriber puts on the wire: its reply carries
        # the marker it copied from the ping it answered
        _deliver(client, LEGACY_PONG,
                 context={NAMESPACE_COMPAT_TWIN_KEY: True})

        self.assertTrue(heard, "the pong was suppressed; the poll hears nothing")

    def test_the_marker_is_not_dispatched_onward(self):
        """Delivering it must not re-export the marker to handlers."""
        client = _client()
        seen = []
        client.on(LEGACY_PONG, lambda message: seen.append(message.context))

        _deliver(client, LEGACY_PONG,
                 context={NAMESPACE_COMPAT_TWIN_KEY: True})

        self.assertTrue(seen)
        for context in seen:
            self.assertNotIn(NAMESPACE_COMPAT_TWIN_KEY, context)


class TestARealTwinIsStillSuppressed(unittest.TestCase):
    """The control. The fix must not undo the deduplication it sits in."""

    def test_a_twin_behind_its_canonical_frame_is_still_suppressed(self):
        client = _client()
        heard = _heard(client, LEGACY_PING, CANONICAL_PING)

        # the wire order a modern emitter produces: canonical, then the twin
        _deliver(client, CANONICAL_PING, data={"utterances": ["hello"]})
        before = len(heard)
        _deliver(client, LEGACY_PING, data={"utterances": ["hello"]},
                 context={NAMESPACE_COMPAT_TWIN_KEY: True})

        self.assertEqual(
            len(heard), before,
            "the real twin was delivered a second time; the dedup is broken")

    def test_the_canonical_frame_alone_still_reaches_both_spellings(self):
        """Unchanged behaviour, asserted so the fix cannot silently cost it."""
        client = _client()
        heard = _heard(client, LEGACY_PING, CANONICAL_PING)

        _deliver(client, CANONICAL_PING, data={"utterances": ["hello"]})

        self.assertIn(CANONICAL_PING, heard)
        self.assertIn(LEGACY_PING, heard)

    def test_a_witness_does_not_serve_a_different_payload(self):
        """A twin is proven by the frame, not by the topic alone."""
        client = _client()
        heard = _heard(client, LEGACY_PING)

        _deliver(client, CANONICAL_PING, data={"utterances": ["hello"]})
        before = len(heard)
        # same topic, different payload: not the twin of what we saw
        _deliver(client, LEGACY_PING, data={"utterances": ["goodbye"]},
                 context={NAMESPACE_COMPAT_TWIN_KEY: True})

        self.assertGreater(
            len(heard), before,
            "an unrelated marked frame was suppressed on a stale witness")



class TestAsyncClientAgrees(unittest.TestCase):
    """The async client is a separate class with its own receive path, and
    this repository keeps the two in parity on purpose. A fix applied to one
    and not the other leaves the same silence on half the fleet."""

    def _async_client(self):
        import asyncio  # noqa: F401  (imported for the runner below)
        from pyee.asyncio import AsyncIOEventEmitter
        from unittest.mock import AsyncMock
        from ovos_bus_client.client.async_client import AsyncMessageBusClient
        bus = AsyncMessageBusClient.__new__(AsyncMessageBusClient)
        bus.emitter = AsyncIOEventEmitter()
        bus._ws = AsyncMock()
        bus._ws.send = AsyncMock()
        bus._translator = NamespaceTranslator(modernize=True, emit_legacy=True)
        bus._wire_legacy_twins = True
        bus.session_id = "default"
        return bus

    def _deliver(self, bus, msg_type, data=None, context=None):
        import asyncio
        asyncio.run(bus._on_message(
            Message(msg_type, data or {}, context or {}).serialize()))

    def test_a_marked_pong_with_no_canonical_pong_is_delivered(self):
        bus = self._async_client()
        got = []
        bus.emitter.on(LEGACY_PONG, lambda message: got.append(message))

        self._deliver(bus, LEGACY_PONG,
                      context={NAMESPACE_COMPAT_TWIN_KEY: True})

        self.assertTrue(got, "the async client still suppresses the reply")

    def test_a_real_twin_is_still_suppressed(self):
        bus = self._async_client()
        got = []
        bus.emitter.on(LEGACY_PING, lambda message: got.append(message))

        self._deliver(bus, CANONICAL_PING, data={"utterances": ["hello"]})
        before = len(got)
        self._deliver(bus, LEGACY_PING, data={"utterances": ["hello"]},
                      context={NAMESPACE_COMPAT_TWIN_KEY: True})

        self.assertEqual(len(got), before,
                         "the async client delivered a real twin twice")


if __name__ == "__main__":
    unittest.main()
