"""Tests for the legacy intent-topic bridge in MessageBusClient.

Old ovos-workshop built the per-intent dispatch topic from the resource
filename, so ``<skill_id>:food.order.intent`` reached the wire. Current
workshop registers the canonical ``<skill_id>:food.order``. The bridge is two
stateless rules:

* RULE 1 (send) -- every canonical intent frame emitted is followed on the wire
  by its suffixed twin, marked as a twin. This is what reaches an old skill
  container, whose bus-client holds no bridge of its own;
* RULE 2 (receive) -- every UNMARKED suffixed frame received is also dispatched
  locally under its canonical spelling, which is what lets a spec-pure skill
  hear an old core.

The marker is the deduplication, and these tests are its proof: a canonical
frame plus its marked twin must fire the canonical handlers exactly once.
"""
import json
import unittest
from threading import Event
from unittest.mock import MagicMock

from pyee import EventEmitter

from ovos_bus_client.client.client import (INTENT_COMPAT_TWIN_KEY,
                                           MessageBusClient)
from ovos_bus_client.message import Message
from ovos_spec_tools import NamespaceTranslator

CANONICAL = "skill-food.jarbas:food.order"
LEGACY = "skill-food.jarbas:food.order.intent"


def _client(emit_legacy=True):
    """A client on a real synchronous emitter so dispatch is observable."""
    c = MessageBusClient.__new__(MessageBusClient)
    c.emitter = EventEmitter()
    c.client = MagicMock()
    c._translator = NamespaceTranslator(modernize=True, emit_legacy=emit_legacy)
    c._handler_guards = {}
    c._dedup_registrations = {}
    c.wrapped_funcs = {}
    c.connected_event = Event()
    c.connected_event.set()
    c.started_running = True
    c.session_id = "default"
    return c


def _deliver(client, msg_type, data=None, context=None):
    """Feed a serialized message in as if it arrived off the websocket."""
    client.on_message(Message(msg_type, data or {}, context or {}).serialize())


def _received(client, *topics):
    """Bind a recorder to each topic; returns the shared list of Messages."""
    got = []
    for topic in topics:
        client.on(topic, got.append)
    return got


def _sent(client):
    """The msg_types this client actually put on the wire, in order."""
    return [json.loads(call.args[0])["type"]
            for call in client.client.send.call_args_list]


def _sent_messages(client):
    """The full frames this client put on the wire, in order."""
    return [json.loads(call.args[0])
            for call in client.client.send.call_args_list]


class TestRule1Send(unittest.TestCase):
    """Every canonical intent frame is followed by its marked twin."""

    def test_canonical_frame_is_twinned_canonical_first(self):
        c = _client()
        c.emit(Message(CANONICAL, {"utterance": "one pizza"}))
        self.assertEqual(_sent(c), [CANONICAL, LEGACY])

    def test_twin_is_sent_exactly_once(self):
        c = _client()
        c.emit(Message(CANONICAL))
        self.assertEqual(_sent(c).count(LEGACY), 1)

    def test_twin_is_marked_and_carries_the_same_payload(self):
        c = _client()
        c.emit(Message(CANONICAL, {"a": 1}, {"source": ["me"]}))
        canonical, twin = _sent_messages(c)
        self.assertEqual(twin["data"], canonical["data"])
        self.assertEqual(twin["context"]["source"], ["me"])
        self.assertTrue(twin["context"][INTENT_COMPAT_TWIN_KEY])

    def test_no_listener_is_needed_for_the_twin(self):
        # nothing in this process listens on either spelling: the twin still
        # goes out, because the listener that wants it lives elsewhere.
        c = _client()
        c.emit(Message(CANONICAL))
        self.assertIn(LEGACY, _sent(c))

    def test_already_suffixed_emit_is_not_re_twinned(self):
        c = _client()
        c.emit(Message(LEGACY))
        self.assertEqual(_sent(c), [LEGACY])

    def test_non_intent_topics_are_untouched(self):
        c = _client()
        c.emit(Message("ovos.utterance.handled"))
        self.assertEqual(_sent(c), ["ovos.utterance.handled"])

    def test_no_twin_when_compat_is_disabled(self):
        c = _client(emit_legacy=False)
        c.emit(Message(CANONICAL))
        self.assertEqual(_sent(c), [CANONICAL])


class TestRule2Receive(unittest.TestCase):
    """An unmarked suffixed frame is modernized onto its canonical topic."""

    def test_old_core_reaches_a_spec_pure_listener(self):
        c = _client()
        got = _received(c, CANONICAL)
        _deliver(c, LEGACY, {"utterance": "one pizza"})
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].data, {"utterance": "one pizza"})

    def test_canonical_fires_exactly_once_per_unmarked_frame(self):
        c = _client()
        got = _received(c, CANONICAL)
        _deliver(c, LEGACY)
        _deliver(c, LEGACY)
        self.assertEqual(len(got), 2)  # two dispatches, one handler run each

    def test_suffixed_listeners_still_get_the_original_frame(self):
        c = _client()
        got = _received(c, LEGACY)
        _deliver(c, LEGACY)
        self.assertEqual(len(got), 1)

    def test_nothing_goes_back_on_the_wire(self):
        c = _client()
        _received(c, CANONICAL)
        _deliver(c, LEGACY)
        self.assertEqual(_sent(c), [])

    def test_canonical_frames_are_not_modernized_again(self):
        c = _client()
        got = _received(c, CANONICAL)
        _deliver(c, CANONICAL)
        self.assertEqual(len(got), 1)

    def test_non_intent_topics_are_untouched(self):
        c = _client()
        got = _received(c, "ovos.utterance.handled")
        _deliver(c, "ovos.utterance.handled")
        self.assertEqual(len(got), 1)

    def test_no_modernization_when_compat_is_disabled(self):
        c = _client(emit_legacy=False)
        got = _received(c, CANONICAL)
        _deliver(c, LEGACY)
        self.assertEqual(got, [])


class TestMarkerIsTheDedup(unittest.TestCase):
    """A new emitter's canonical + marked twin pair collapses to one run."""

    def test_canonical_handler_runs_once_for_the_pair(self):
        c = _client()
        got = _received(c, CANONICAL)
        _deliver(c, CANONICAL)
        _deliver(c, LEGACY, context={INTENT_COMPAT_TWIN_KEY: True})
        self.assertEqual(len(got), 1)

    def test_suffixed_listener_still_gets_the_twin_frame(self):
        c = _client()
        got = _received(c, LEGACY)
        _deliver(c, CANONICAL)
        _deliver(c, LEGACY, context={INTENT_COMPAT_TWIN_KEY: True})
        self.assertEqual(len(got), 1)

    def test_a_handler_bound_to_both_spellings_runs_once_per_frame(self):
        # the pair is two wire frames, so a handler on both topics hears both.
        # Neither frame is duplicated by the bridge, which is what rule 2's
        # marker check guarantees.
        c = _client()
        got = _received(c, CANONICAL, LEGACY)
        _deliver(c, CANONICAL)
        _deliver(c, LEGACY, context={INTENT_COMPAT_TWIN_KEY: True})
        self.assertEqual(len(got), 2)


class TestMarkerDoesNotLeakToDescendants(unittest.TestCase):
    """The twin marker must not ride forward()/reply() onto later messages.

    Message.forward()/reply() deep-copy the whole context. If the marker
    survived on a received twin, a handler that forwards that context to emit an
    UNRELATED suffixed intent would brand the follow-up a twin, and RULE 2 would
    silently drop its canonical spelling — exactly the old-emitter -> new-core
    population the receive rule exists to serve.
    """

    UNRELATED_LEGACY = "other-skill.jarbas:unrelated.intent"
    UNRELATED_CANON = "other-skill.jarbas:unrelated"

    def test_received_twin_is_dispatched_with_an_unmarked_context(self):
        # RULE 2 uses the marker to decide "skip", then pops it: the frame that
        # reaches the legacy listener carries no marker.
        c = _client()
        got = _received(c, LEGACY)
        _deliver(c, LEGACY, context={INTENT_COMPAT_TWIN_KEY: True})
        self.assertEqual(len(got), 1)  # the twin still reaches its listener
        self.assertNotIn(INTENT_COMPAT_TWIN_KEY, got[0].context)

    def test_forward_off_a_twin_does_not_suppress_an_unrelated_intent(self):
        c = _client()
        got_twin = _received(c, LEGACY)
        _deliver(c, LEGACY, context={INTENT_COMPAT_TWIN_KEY: True})
        twin_msg = got_twin[0]
        # a handler does the standard OVOS thing: forward this frame's context
        # onward when emitting a follow-up for a totally unrelated intent.
        followup = twin_msg.forward(self.UNRELATED_LEGACY, {})
        self.assertNotIn(INTENT_COMPAT_TWIN_KEY, followup.context)
        got_canon = _received(c, self.UNRELATED_CANON)
        c.on_message(followup.serialize())
        # the unrelated canonical topic IS modernized: the marker did not leak.
        self.assertEqual(len(got_canon), 1)

    def test_reply_off_a_twin_does_not_suppress_an_unrelated_intent(self):
        c = _client()
        got_twin = _received(c, LEGACY)
        _deliver(c, LEGACY, context={INTENT_COMPAT_TWIN_KEY: True})
        followup = got_twin[0].reply(self.UNRELATED_LEGACY, {})
        got_canon = _received(c, self.UNRELATED_CANON)
        c.on_message(followup.serialize())
        self.assertEqual(len(got_canon), 1)

    def test_marker_survives_on_the_wire_for_a_second_receiver(self):
        # wire survival: a marked twin arriving at a second process is NOT
        # re-modernized (its canonical companion was already sent by the origin).
        c = _client()
        got_canon = _received(c, CANONICAL)
        got_legacy = _received(c, LEGACY)
        _deliver(c, LEGACY, context={INTENT_COMPAT_TWIN_KEY: True})
        self.assertEqual(len(got_legacy), 1)  # twin delivered to its listener
        self.assertEqual(len(got_canon), 0)   # but not re-modernized
        self.assertEqual(_sent(c), [])        # and nothing re-twinned onto wire


if __name__ == "__main__":
    unittest.main()
