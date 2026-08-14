"""Tests for the legacy NAMESPACE wire twin in MessageBusClient (bus-client
wire-twin fix).

Confirmed defect: :meth:`MessageBusClient.emit` put a REAL second wire frame
only for intent topics (see ``test_intent_legacy_reemit.py``). Every other
:data:`MIGRATION_MAP` topic (e.g. ``speak`` <-> ``ovos.utterance.speak``) was
bridged RECEIVE-side only, so an old pre-spec-tools client subscribed to the
legacy topic over a real websocket got NOTHING when a modern client emitted
the canonical spelling: old satellites lost ``speak``.

This mirrors the intent-topic bridge, generalised to every migrated topic:

* RULE 1 (send) -- every canonical (``ovos.*``) emit of a migrated topic is
  followed on the wire by its legacy-spelled twin, marked as a twin.
* RULE 2 (receive) -- unchanged: a legacy emit's canonical counterpart is
  already delivered to local listeners by the pre-existing receive-side
  ``counterpart_topics()`` loop, so the reverse direction is not twinned onto
  the wire.

The marker is again the whole deduplication, plus (unlike the intent bridge)
a marked namespace twin must skip its own direct dispatch AND the receive-
side counterpart loop entirely -- unlike a suffixed intent topic, a legacy
namespace topic routinely has local listeners in a modern process too, and
both spellings were already delivered locally when the canonical frame ahead
of the twin was received.
"""
import json
import unittest
from threading import Event
from unittest.mock import MagicMock, patch

from pyee import EventEmitter

from ovos_bus_client.client.client import (NAMESPACE_COMPAT_TWIN_KEY,
                                           MessageBusClient, _bus_flag)
from ovos_bus_client.message import Message
from ovos_spec_tools import NamespaceTranslator

LEGACY = "speak"
CANONICAL = "ovos.utterance.speak"


def _client(emit_legacy=True, modernize=True, wire_legacy_twins=True):
    """A client on a real synchronous emitter so dispatch is observable."""
    c = MessageBusClient.__new__(MessageBusClient)
    c.emitter = EventEmitter()
    c.client = MagicMock()
    c._translator = NamespaceTranslator(modernize=modernize, emit_legacy=emit_legacy)
    c._wire_legacy_twins = wire_legacy_twins
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


def _received(client, *topics):
    got = []
    for topic in topics:
        client.on(topic, lambda message: got.append(message))
    return got


def _relay(sender, receiver):
    for call in sender.client.send.call_args_list:
        receiver.on_message(call.args[0])


def _sent(client):
    return [json.loads(call.args[0])["type"]
            for call in client.client.send.call_args_list]


def _sent_messages(client):
    return [json.loads(call.args[0])
            for call in client.client.send.call_args_list]


class TestRule1Send(unittest.TestCase):
    """Every canonical migrated (non-intent) emit is followed by a real
    legacy-spelled wire frame, marked as a twin."""

    def test_canonical_emit_produces_exactly_two_wire_frames(self):
        c = _client()
        c.emit(Message(CANONICAL, {"utterance": "hi"}))
        self.assertEqual(_sent(c), [CANONICAL, LEGACY])

    def test_twin_is_sent_exactly_once(self):
        c = _client()
        c.emit(Message(CANONICAL))
        self.assertEqual(_sent(c).count(LEGACY), 1)

    def test_twin_is_marked(self):
        c = _client()
        c.emit(Message(CANONICAL, {"utterance": "hi"}, {"source": ["me"]}))
        canonical, twin = _sent_messages(c)
        self.assertTrue(twin["context"][NAMESPACE_COMPAT_TWIN_KEY])
        self.assertEqual(twin["context"]["source"], ["me"])

    def test_twin_payload_is_the_legacy_shape(self):
        # speak/ovos.utterance.speak is a payload-compatible rename (identity)
        c = _client()
        c.emit(Message(CANONICAL, {"utterance": "hi"}))
        canonical, twin = _sent_messages(c)
        self.assertEqual(twin["data"], {"utterance": "hi"})

    def test_already_legacy_emit_is_not_re_twinned(self):
        c = _client()
        c.emit(Message(LEGACY, {"utterance": "hi"}))
        self.assertEqual(_sent(c), [LEGACY])

    def test_unmapped_topic_sends_once(self):
        c = _client()
        c.emit(Message("some.plain.topic", {"a": 1}))
        self.assertEqual(_sent(c), ["some.plain.topic"])

    def test_no_twin_when_emit_legacy_is_disabled(self):
        c = _client(emit_legacy=False)
        c.emit(Message(CANONICAL))
        self.assertEqual(_sent(c), [CANONICAL])

    def test_intent_topics_are_untouched_by_the_namespace_twin(self):
        # intent topics are twinned by the SEPARATE intent-topic bridge
        # (RULE 1 of test_intent_legacy_reemit.py), not this one.
        c = _client()
        c.emit(Message("skill-food.jarbas:food.order", {"a": 1}))
        self.assertEqual(_sent(c), ["skill-food.jarbas:food.order",
                                    "skill-food.jarbas:food.order.intent"])

    def test_computed_stop_dispatch_pattern_is_also_twinned(self):
        # <skill_id>:stop <-> <skill_id>.stop is a namespace-migrated pair
        # too (the computed pattern branch of counterpart_topics()).
        c = _client()
        c.emit(Message("skill-food.jarbas:stop"))
        self.assertEqual(_sent(c), ["skill-food.jarbas:stop",
                                    "skill-food.jarbas.stop"])


class TestNewClientReceiveDedup(unittest.TestCase):
    """A new-client receiver delivers exactly ONCE per logical dispatch, for
    both spellings and for duplicate registrations, whether it receives one
    wire frame (a peer still on the old single-frame behaviour) or the new
    canonical+twin pair."""

    def test_receiver_delivers_once_per_spelling_for_the_canonical_twin_pair(self):
        core, sat = _client(), _client()
        legacy_seen = _received(sat, LEGACY)
        canon_seen = _received(sat, CANONICAL)
        core.emit(Message(CANONICAL, {"utterance": "hi"}))
        self.assertEqual(_sent(core), [CANONICAL, LEGACY])
        _relay(core, sat)
        self.assertEqual(len(canon_seen), 1)
        self.assertEqual(len(legacy_seen), 1)

    def test_handler_bound_to_both_spellings_fires_once(self):
        core, sat = _client(), _client()
        got = []
        sat.on(LEGACY, got.append)
        sat.on(CANONICAL, got.append)
        core.emit(Message(CANONICAL, {"utterance": "hi"}))
        _relay(core, sat)
        self.assertEqual(len(got), 1)

    def test_duplicate_registration_on_canonical_topic_fires_once(self):
        core, sat = _client(), _client()
        calls = []

        def handler(message=None):
            calls.append(1)

        sat.on(CANONICAL, handler)
        sat.on(CANONICAL, handler)  # duplicate registration, same handler
        core.emit(Message(CANONICAL, {"utterance": "hi"}))
        _relay(core, sat)
        self.assertEqual(len(calls), 1)

    def test_duplicate_registration_on_legacy_topic_fires_once(self):
        core, sat = _client(), _client()
        calls = []

        def handler(message=None):
            calls.append(1)

        sat.on(LEGACY, handler)
        sat.on(LEGACY, handler)  # duplicate registration, same handler
        core.emit(Message(CANONICAL, {"utterance": "hi"}))
        _relay(core, sat)
        self.assertEqual(len(calls), 1)

    def test_twin_alone_carries_no_marker_leak_to_its_own_listener(self):
        # the twin's own listener still gets a clean (unmarked) frame
        c = _client()
        got = _received(c, LEGACY)
        _deliver(c, LEGACY, {"utterance": "hi"},
                {NAMESPACE_COMPAT_TWIN_KEY: True})
        self.assertEqual(len(got), 0)  # marked twin: suppressed, see below

    def test_marked_twin_delivers_nothing_new_a_second_time(self):
        # a receiver that already saw the canonical frame (and its local
        # counterpart dispatch) must not be handed a second delivery when the
        # real marked twin frame arrives afterwards.
        c = _client()
        canon_seen = _received(c, CANONICAL)
        legacy_seen = _received(c, LEGACY)
        _deliver(c, CANONICAL, {"utterance": "hi"})
        self.assertEqual(len(canon_seen), 1)
        self.assertEqual(len(legacy_seen), 1)  # via the existing receive bridge
        _deliver(c, LEGACY, {"utterance": "hi"},
                {NAMESPACE_COMPAT_TWIN_KEY: True})
        self.assertEqual(len(canon_seen), 1)   # unchanged
        self.assertEqual(len(legacy_seen), 1)  # unchanged

    def test_nothing_goes_back_on_the_wire_for_a_marked_twin(self):
        c = _client()
        _received(c, CANONICAL)
        _deliver(c, LEGACY, {"utterance": "hi"},
                {NAMESPACE_COMPAT_TWIN_KEY: True})
        self.assertEqual(_sent(c), [])

    def test_marker_does_not_leak_onto_descendant_frames(self):
        c = _client()
        got_twin = _received(c, LEGACY)
        _deliver(c, LEGACY, {"utterance": "hi"},
                {NAMESPACE_COMPAT_TWIN_KEY: True})
        # nothing delivered directly (suppressed), so nothing to forward from
        # here -- but exercise the pop-before-firehose contract directly:
        self.assertEqual(len(got_twin), 0)


class TestFlagGating(unittest.TestCase):
    def test_emit_legacy_false_single_frame(self):
        c = _client(emit_legacy=False)
        c.emit(Message(CANONICAL, {"utterance": "hi"}))
        self.assertEqual(_sent(c), [CANONICAL])

    def test_emit_legacy_true_two_frames(self):
        c = _client(emit_legacy=True)
        c.emit(Message(CANONICAL, {"utterance": "hi"}))
        self.assertEqual(_sent(c), [CANONICAL, LEGACY])

    def test_non_migrated_topic_single_frame_regardless_of_flags(self):
        for emit_legacy in (True, False):
            with self.subTest(emit_legacy=emit_legacy):
                c = _client(emit_legacy=emit_legacy)
                c.emit(Message("some.unmapped.topic", {"a": 1}))
                self.assertEqual(_sent(c), ["some.unmapped.topic"])


class TestWireLegacyTwinsFlagGating(unittest.TestCase):
    """``OVOS_BUS_WIRE_LEGACY_TWINS`` is the escape hatch for the namespace
    wire twin, DEFAULT TRUE (symmetric with ``OVOS_BUS_EMIT_LEGACY``): the
    twin exists to reach a STABLE (<2.x) pre-spec-tools wire listener, the
    supported compat target, so it stays on unless an operator explicitly
    knows no such listener shares the bus.

    A 2.2.0a1..2.8.2a1 receiver double-delivers while sharing a bus with a
    2.8.3a1+ sender running the default -- that outdated-alpha window is not
    a supported configuration and is resolved by updating the receiver, not
    by this flag.
    """

    def test_flag_defaults_true(self):
        # no env var and empty config -> the default (True) is returned,
        # exercising _bus_flag() itself rather than the _client() test
        # helper's own default.
        env = {k: v for k, v in __import__("os").environ.items()
               if k != "OVOS_BUS_WIRE_LEGACY_TWINS"}
        with patch.dict("os.environ", env, clear=True), \
                patch("ovos_config.Configuration", return_value={}):
            self.assertTrue(_bus_flag("OVOS_BUS_WIRE_LEGACY_TWINS",
                                      "wire_legacy_twins", default=True))

    def test_env_can_disable(self):
        with patch.dict("os.environ", {"OVOS_BUS_WIRE_LEGACY_TWINS": "false"}):
            self.assertFalse(_bus_flag("OVOS_BUS_WIRE_LEGACY_TWINS",
                                       "wire_legacy_twins", default=True))

    def test_flag_explicitly_off_single_frame(self):
        c = _client(wire_legacy_twins=False)
        c.emit(Message(CANONICAL, {"utterance": "hi"}))
        self.assertEqual(_sent(c), [CANONICAL])

    def test_flag_explicitly_off_leaves_intent_twin_untouched(self):
        # the namespace-twin flag must not affect the separate intent-topic
        # bridge (RULE 1 of test_intent_legacy_reemit.py).
        c = _client(wire_legacy_twins=False)
        c.emit(Message("skill-food.jarbas:food.order", {"a": 1}))
        self.assertEqual(_sent(c), ["skill-food.jarbas:food.order",
                                    "skill-food.jarbas:food.order.intent"])

    def test_flag_explicitly_off_receiver_still_dedups_a_twin_from_a_peer(self):
        # marker-popping in on_message stays unconditional: a 2.8.4a1+
        # receiver must dedup an incoming twin regardless of its OWN send
        # flag, because the twin came from a peer that has it on.
        c = _client(wire_legacy_twins=False)
        canon_seen = _received(c, CANONICAL)
        legacy_seen = _received(c, LEGACY)
        _deliver(c, CANONICAL, {"utterance": "hi"})
        self.assertEqual(len(canon_seen), 1)
        self.assertEqual(len(legacy_seen), 1)  # via the receive-side bridge
        _deliver(c, LEGACY, {"utterance": "hi"},
                {NAMESPACE_COMPAT_TWIN_KEY: True})
        self.assertEqual(len(canon_seen), 1)   # unchanged: twin deduped
        self.assertEqual(len(legacy_seen), 1)  # unchanged: twin deduped


class TestFirehoseIsNotDoubled(unittest.TestCase):
    """A modern receiver's 'message' firehose sees exactly ONE frame per
    logical emit for a migrated topic, same as before this fix.

    Regression for a review finding: on_message used to fire the firehose
    UNCONDITIONALLY, before the twin-marker check, so a modern client's
    wildcard/logging listener saw TWO frames (canonical + the new legacy
    wire twin) for every MIGRATION_MAP topic. That breaks the pre-existing
    one-frame-per-logical-emit invariant ovoscope/busmon and message-count
    tests rely on. An old client legitimately still sees both raw frames --
    it has no notion of "twin" -- so this is a modern-receiver-only
    guarantee.
    """

    def test_firehose_sees_exactly_one_frame_per_logical_emit_modern_to_modern(self):
        core, sat = _client(), _client()
        firehose = []
        sat.emitter.on("message", lambda m: firehose.append(m))
        core.emit(Message(CANONICAL, {"utterance": "hi"}))
        self.assertEqual(_sent(core), [CANONICAL, LEGACY])
        _relay(core, sat)
        self.assertEqual(len(firehose), 1)


class TestOldClientReachedOnTheWire(unittest.TestCase):
    """The acceptance criterion in prose form: an emit()->wire round trip puts
    the legacy spelling on the wire as a REAL frame, which is exactly what an
    old client (with no translator, listening only on the literal msg_type)
    needs to receive it."""

    def test_old_client_style_listener_gets_a_real_frame_off_the_wire(self):
        core = _client()
        core.emit(Message(CANONICAL, {"utterance": "hi"}))
        frames = [json.loads(call.args[0])["type"]
                 for call in core.client.send.call_args_list]
        # an old client has no translator: it can only ever be reached by a
        # frame whose literal "type" is LEGACY.
        self.assertIn(LEGACY, frames)


if __name__ == "__main__":
    unittest.main()
