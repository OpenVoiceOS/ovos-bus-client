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



class TestAModernOnlyReceiverStillSuppresses(unittest.TestCase):
    """`emit_legacy` says what THIS process emits, not what a peer emits.

    The witness lookup went through the receiver's own
    ``counterpart_topics``, which is the SEND side of the dual-emit and
    returns ``[]`` when the local ``emit_legacy`` is off. On such a receiver
    the book stayed empty, no marker was ever proven, and every real twin an
    older peer put on the wire was delivered.

    Measured live by the reviewer lane on three real clients over a real
    ``ovos-messagebus``: one ``ovos.fallback.ping`` emit gave a receiver with
    ``OVOS_BUS_EMIT_LEGACY=false`` three dispatches instead of one. A
    fallback poll counts two answers where there is one, and a
    ``<skill_id>:stop`` twin runs stop twice.
    """

    def _modern_only_client(self):
        client = _client()
        client._translator = NamespaceTranslator(modernize=True, emit_legacy=False)
        client._wire_legacy_twins = False
        return client

    def _dispatches(self, client):
        got = []
        for topic in (CANONICAL_PING, LEGACY_PING):
            client.on(topic, lambda message: got.append(message.msg_type))
        return got

    def test_the_local_flag_does_not_empty_the_witness_book(self):
        """The book is written even though this client emits nothing legacy."""
        client = self._modern_only_client()
        self.assertEqual(
            [], client._translator.counterpart_topics(CANONICAL_PING),
            "the premise moved: the send side no longer reports nothing here")

        _deliver(client, CANONICAL_PING, data={"utterances": ["hello"]})

        book = client._twin_witnesses
        self.assertEqual(1, len(book._seen),
                         "the witness book is empty on a modern-only receiver")

    def test_a_real_twin_is_suppressed_on_a_modern_only_receiver(self):
        """One logical event, one dispatch. It was three."""
        client = self._modern_only_client()
        got = self._dispatches(client)

        _deliver(client, CANONICAL_PING, data={"utterances": ["hello"]})
        _deliver(client, LEGACY_PING, data={"utterances": ["hello"]},
                 context={NAMESPACE_COMPAT_TWIN_KEY: True})

        self.assertEqual([CANONICAL_PING], got)

    def test_an_inherited_marker_still_gets_through(self):
        """The defect the PR exists to fix must survive the flag too."""
        client = self._modern_only_client()
        heard = _heard(client, LEGACY_PONG, CANONICAL_PONG)

        _deliver(client, LEGACY_PONG,
                 context={NAMESPACE_COMPAT_TWIN_KEY: True})

        self.assertTrue(heard, "the pong was suppressed on a modern-only receiver")


class TestAWitnessIsConsumed(unittest.TestCase):
    """One canonical frame proves ONE twin.

    The entry stayed in the book on a hit, so a single canonical frame proved
    every marked frame with the same fingerprint for the whole 5 s window. A
    peer that re-emits a received frame verbatim inside that window had its
    second copy -- a genuine new event -- dropped.
    """

    def test_a_second_marked_copy_is_delivered(self):
        client = _client()
        got = []
        client.on(LEGACY_PING, lambda message: got.append(message.msg_type))

        # this client emits legacy, so the canonical frame is mirrored onto
        # the legacy spelling for local listeners. That mirror is not a twin
        # delivery, so it is the baseline both counts are taken against.
        _deliver(client, CANONICAL_PING, data={"utterances": ["hello"]})
        mirrored = len(got)
        _deliver(client, LEGACY_PING, data={"utterances": ["hello"]},
                 context={NAMESPACE_COMPAT_TWIN_KEY: True})
        after_the_real_twin = len(got)
        _deliver(client, LEGACY_PING, data={"utterances": ["hello"]},
                 context={NAMESPACE_COMPAT_TWIN_KEY: True})

        self.assertEqual(1, mirrored, "the mirror baseline moved")
        self.assertEqual(mirrored, after_the_real_twin,
                         "the one real twin was not suppressed")
        self.assertEqual(mirrored + 1, len(got),
                         "one canonical frame proved a second marked frame")

    def test_the_book_does_not_keep_a_spent_witness(self):
        client = _client()
        _deliver(client, CANONICAL_PING, data={"utterances": ["hello"]})
        self.assertEqual(1, len(client._twin_witnesses._seen))
        _deliver(client, LEGACY_PING, data={"utterances": ["hello"]},
                 context={NAMESPACE_COMPAT_TWIN_KEY: True})
        self.assertEqual(0, len(client._twin_witnesses._seen))

    def test_two_canonical_frames_prove_two_twins(self):
        """Consuming must not cost the ordinary repeated-event case."""
        client = _client()
        got = []
        client.on(LEGACY_PING, lambda message: got.append(message.msg_type))

        for _ in range(2):
            _deliver(client, CANONICAL_PING, data={"utterances": ["hello"]})
            _deliver(client, LEGACY_PING, data={"utterances": ["hello"]},
                     context={NAMESPACE_COMPAT_TWIN_KEY: True})

        # two mirrors of the two canonical frames, and no twin delivery
        self.assertEqual(2, len(got),
                         "a twin behind its own canonical frame was delivered")


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

    def test_a_real_twin_is_suppressed_on_a_modern_only_receiver(self):
        """Both wire clients carried the emit_legacy defect, so both are
        asserted. The async client is a separate class with its own receive
        path, and a fix applied to one leaves the other broken."""
        bus = self._async_client()
        bus._translator = NamespaceTranslator(modernize=True, emit_legacy=False)
        bus._wire_legacy_twins = False
        got = []
        for topic in (CANONICAL_PING, LEGACY_PING):
            bus.emitter.on(topic, lambda message: got.append(message.msg_type))

        self._deliver(bus, CANONICAL_PING, data={"utterances": ["hello"]})
        self._deliver(bus, LEGACY_PING, data={"utterances": ["hello"]},
                      context={NAMESPACE_COMPAT_TWIN_KEY: True})

        self.assertEqual([CANONICAL_PING], got)

    def test_a_witness_is_consumed_on_the_async_client_too(self):
        bus = self._async_client()
        got = []
        bus.emitter.on(LEGACY_PING, lambda message: got.append(message.msg_type))

        # the canonical frame mirrors onto the legacy spelling: the baseline
        self._deliver(bus, CANONICAL_PING, data={"utterances": ["hello"]})
        mirrored = len(got)
        self._deliver(bus, LEGACY_PING, data={"utterances": ["hello"]},
                      context={NAMESPACE_COMPAT_TWIN_KEY: True})
        after_the_real_twin = len(got)
        self._deliver(bus, LEGACY_PING, data={"utterances": ["hello"]},
                      context={NAMESPACE_COMPAT_TWIN_KEY: True})

        self.assertEqual(1, mirrored, "the mirror baseline moved")
        self.assertEqual(mirrored, after_the_real_twin)
        self.assertEqual(mirrored + 1, len(got),
                         "one canonical frame proved a second marked frame")

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
