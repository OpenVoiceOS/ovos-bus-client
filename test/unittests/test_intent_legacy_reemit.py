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


def _client(emit_legacy=True, modernize=True):
    """A client on a real synchronous emitter so dispatch is observable."""
    c = MessageBusClient.__new__(MessageBusClient)
    c.emitter = EventEmitter()
    c.client = MagicMock()
    c._translator = NamespaceTranslator(modernize=modernize, emit_legacy=emit_legacy)
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
    """Feed a serialized message in as if it arrived off the websocket."""
    client.on_message(Message(msg_type, data or {}, context or {}).serialize())


def _received(client, *topics):
    """Bind a recorder to each topic; returns the shared list of Messages.

    A FRESH closure per topic, exactly as ``ovos-workshop`` 9.3.2a1+ does when
    it binds one skill method to both spellings — the case a per-handler guard
    cannot see and a per-pair guard can.
    """
    got = []
    for topic in topics:
        client.on(topic, lambda message: got.append(message))
    return got


def _relay(sender, receiver):
    """Feed every frame ``sender`` put on the wire into ``receiver``."""
    for call in sender.client.send.call_args_list:
        receiver.on_message(call.args[0])


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

    def test_no_modernization_when_modernize_is_disabled(self):
        c = _client(modernize=False)
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

    def test_a_handler_bound_to_both_spellings_runs_once_per_dispatch(self):
        # ONE logical dispatch reaches the process as two frames. A handler
        # bound to both spellings must run ONCE: the receive-side pair guard
        # recognises the second frame as the mirror of the first and drops it.
        c = _client()
        got = _received(c, CANONICAL, LEGACY)
        _deliver(c, CANONICAL)
        _deliver(c, LEGACY, context={INTENT_COMPAT_TWIN_KEY: True})
        self.assertEqual(len(got), 1)


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


class TestExactlyOncePerDispatch(unittest.TestCase):
    """The acceptance criterion: one logical dispatch, one handler run.

    Every case is a full ``emit() -> wire -> on_message()`` round trip through
    two clients, so the frames under test are the frames the bridge really
    produces. The receive-side guard is keyed by the TOPIC PAIR, so it collapses
    the mirror no matter which spellings a handler is bound to and no matter
    which of the two wrapper closures ``ovos-workshop`` hands each binding.
    """

    def test_dual_bound_handler_new_core(self):
        # workshop 9.3.2a1+ in a NEW skill container, new core emitting the
        # canonical frame plus its twin. Two frames arrive; the handler runs 1x.
        core, skill = _client(), _client()
        got = _received(skill, CANONICAL, LEGACY)
        core.emit(Message(CANONICAL, {"utterance": ["one pizza"]}))
        self.assertEqual(_sent(core), [CANONICAL, LEGACY])
        _relay(core, skill)
        self.assertEqual(len(got), 1)

    def test_dual_bound_handler_old_core(self):
        # an OLD core puts a single unmarked suffixed frame on the wire. The
        # suffixed binding fires it, RULE 2 modernizes it, and the canonical
        # binding must NOT fire it a second time.
        skill = _client()
        got = _received(skill, CANONICAL, LEGACY)
        _deliver(skill, LEGACY, {"utterance": ["one pizza"]})
        self.assertEqual(len(got), 1)

    def test_suffixed_only_handler_new_core(self):
        # an OLD workshop running inside a NEW bus-client: bound to the
        # suffixed topic only. The canonical frame reaches no binding; the twin
        # reaches one.
        core, skill = _client(), _client()
        got = _received(skill, LEGACY)
        core.emit(Message(CANONICAL, {"utterance": ["one pizza"]}))
        _relay(core, skill)
        self.assertEqual(len(got), 1)

    def test_canonical_only_handler_old_core(self):
        # a spec-pure skill hearing an old core: delivery is RULE 2's job.
        skill = _client()
        got = _received(skill, CANONICAL)
        _deliver(skill, LEGACY, {"utterance": ["one pizza"]})
        self.assertEqual(len(got), 1)

    def test_two_independent_handlers_on_the_canonical_topic(self):
        # the pair guard is SHARED across handlers, so it must not treat the
        # second handler's look at the same frame as a mirror.
        core, skill = _client(), _client()
        a, b = [], []
        skill.on(CANONICAL, a.append)
        skill.on(CANONICAL, b.append)
        core.emit(Message(CANONICAL, {"utterance": ["one pizza"]}))
        _relay(core, skill)
        self.assertEqual((len(a), len(b)), (1, 1))

    def test_two_skills_interleaved_do_not_cross_suppress(self):
        other_canon = "skill-weather.jarbas:weather.query"
        other_legacy = other_canon + ".intent"
        core, skill = _client(), _client()
        got_food = _received(skill, CANONICAL, LEGACY)
        got_weather = _received(skill, other_canon, other_legacy)
        core.emit(Message(CANONICAL, {"utterance": ["one pizza"]}))
        core.emit(Message(other_canon, {"utterance": ["rain?"]}))
        _relay(core, skill)
        self.assertEqual(len(got_food), 1)
        self.assertEqual(len(got_weather), 1)

    def test_repeated_dispatches_each_run_the_handler_once(self):
        # the guard collapses a mirror, never a genuine repeat.
        core, skill = _client(), _client()
        got = _received(skill, CANONICAL, LEGACY)
        for _ in range(3):
            core.emit(Message(CANONICAL, {"utterance": ["one pizza"]}))
        _relay(core, skill)
        self.assertEqual(len(got), 3)


class TestTwinCarriesTheContextVerbatim(unittest.TestCase):
    """The twin is the SAME dispatch, so its context must be byte-identical.

    ``Message.forward()`` re-stamps the session: for ``session_id == "default"``
    it replaces the carried session with the emitting process's own, so a twin
    built with it leaves with a different ``lang`` and an emptied
    ``active_skills``. That corrupts the frame an old skill acts on, AND it
    breaks the receive-side pair guard, which pairs on a payload+context
    fingerprint.
    """

    CONTEXT = {
        "source": ["core"],
        "destination": ["skills"],
        "session": {
            "session_id": "default",
            "lang": "pt-pt",
            "active_skills": [["skill-food.jarbas", 1234.0]],
        },
    }

    def test_twin_context_is_identical_to_the_canonical_frame(self):
        c = _client()
        c.emit(Message(CANONICAL, {"utterance": ["uma piza"]}, dict(self.CONTEXT)))
        canonical, twin = _sent_messages(c)
        twin_context = dict(twin["context"])
        self.assertTrue(twin_context.pop(INTENT_COMPAT_TWIN_KEY))
        self.assertEqual(twin_context, canonical["context"])

    def test_twin_keeps_the_session_language_and_active_skills(self):
        c = _client()
        c.emit(Message(CANONICAL, {}, dict(self.CONTEXT)))
        _, twin = _sent_messages(c)
        self.assertEqual(twin["context"]["session"]["lang"], "pt-pt")
        self.assertEqual(twin["context"]["session"]["active_skills"],
                         [["skill-food.jarbas", 1234.0]])

    def test_twin_payload_is_a_copy_not_a_shared_reference(self):
        c = _client()
        data = {"utterance": ["uma piza"]}
        c.emit(Message(CANONICAL, data, dict(self.CONTEXT)))
        canonical, twin = _sent_messages(c)
        self.assertEqual(twin["data"], canonical["data"])

    def test_modernized_frame_keeps_the_context_verbatim(self):
        c = _client()
        got = _received(c, CANONICAL)
        _deliver(c, LEGACY, {"utterance": ["uma piza"]}, dict(self.CONTEXT))
        self.assertEqual(got[0].context["session"]["lang"], "pt-pt")
        self.assertEqual(got[0].context["source"], ["core"])


class TestFlagMatrix(unittest.TestCase):
    """RULE 1 rides ``emit_legacy``; RULE 2 rides ``modernize``.

    They are different operations. Twinning a dispatch onto the deprecated
    spelling IS legacy emission. Dispatching the canonical spelling of a frame
    that arrived suffixed is MODERNIZATION — gating it on ``emit_legacy`` means
    an operator who sets ``OVOS_BUS_EMIT_LEGACY=false`` to quiet the wire
    silently loses old-core -> new-skill delivery.
    """

    def _twins(self, **flags):
        c = _client(**flags)
        c.emit(Message(CANONICAL))
        return LEGACY in _sent(c)

    def _modernizes(self, **flags):
        c = _client(**flags)
        got = _received(c, CANONICAL)
        _deliver(c, LEGACY)
        return len(got) == 1

    def test_both_on(self):
        self.assertTrue(self._twins(emit_legacy=True, modernize=True))
        self.assertTrue(self._modernizes(emit_legacy=True, modernize=True))

    def test_emit_legacy_off_modernize_on(self):
        # the wire stays quiet, but an old core is still heard
        self.assertFalse(self._twins(emit_legacy=False, modernize=True))
        self.assertTrue(self._modernizes(emit_legacy=False, modernize=True))

    def test_emit_legacy_on_modernize_off(self):
        self.assertTrue(self._twins(emit_legacy=True, modernize=False))
        self.assertFalse(self._modernizes(emit_legacy=True, modernize=False))

    def test_both_off(self):
        self.assertFalse(self._twins(emit_legacy=False, modernize=False))
        self.assertFalse(self._modernizes(emit_legacy=False, modernize=False))


class TestNarrowPredicate(unittest.TestCase):
    """Only per-intent dispatch topics are twinned (bus-client#271 F2/C3).

    A colon is not an intent dispatch. Twinning on "contains a colon" doubles
    the hottest topics on the bus and breaks the documented
    one-emit-one-frame invariant that conformance captures assert.
    """

    NOT_INTENTS = [
        "recognizer_loop:utterance",
        "recognizer_loop:record_begin",
        "recognizer_loop:record_end",
        "recognizer_loop:audio_output_start",
        "recognizer_loop:audio_output_end",
        "question:query",
        "question:action",
        "padatious:register_intent",
        "stop:global",
        "speak:b64_audio",
        "skill-food.jarbas:stop",
        "skill-food.jarbas:converse",
        "skill-food.jarbas:common_query",
    ]

    def test_one_emit_one_frame(self):
        for topic in self.NOT_INTENTS:
            with self.subTest(topic=topic):
                c = _client()
                c.emit(Message(topic, {"a": 1}))
                self.assertEqual(_sent(c), [topic])

    def test_nothing_is_modernized_off_these_topics(self):
        for topic in self.NOT_INTENTS:
            with self.subTest(topic=topic):
                c = _client()
                got = _received(c, topic + ".intent")
                _deliver(c, topic)
                self.assertEqual(got, [])


class TestPairGuardTeardown(unittest.TestCase):
    """The pair guard is released with the last registration on the pair.

    It is keyed by topic pair, not by handler, so no single handler's teardown
    owns it. A client that churns through intent subscriptions would otherwise
    accumulate one dead guard per intent it ever saw.
    """

    def test_pair_guard_is_dropped_when_both_spellings_are_removed(self):
        c = _client()
        handler_canon, handler_legacy = lambda m: None, lambda m: None
        c.on(CANONICAL, handler_canon)
        c.on(LEGACY, handler_legacy)
        pair_key = frozenset({CANONICAL, LEGACY})
        self.assertIn(pair_key, c._intent_pair_guards)
        c.remove(CANONICAL, handler_canon)
        self.assertIn(pair_key, c._intent_pair_guards)  # LEGACY still bound
        c.remove(LEGACY, handler_legacy)
        self.assertNotIn(pair_key, c._intent_pair_guards)

    def test_a_surviving_handler_keeps_the_guard(self):
        c = _client()
        a, b = lambda m: None, lambda m: None
        c.on(CANONICAL, a)
        c.on(CANONICAL, b)
        c.remove(CANONICAL, a)
        self.assertIn(frozenset({CANONICAL, LEGACY}), c._intent_pair_guards)

    def test_a_rebound_handler_gets_a_fresh_guard(self):
        c = _client()
        h = lambda m: None
        c.on(CANONICAL, h)
        first = c._intent_pair_guards[frozenset({CANONICAL, LEGACY})]
        c.remove(CANONICAL, h)
        c.on(CANONICAL, h)
        self.assertIsNot(c._intent_pair_guards[frozenset({CANONICAL, LEGACY})],
                         first)

    def test_other_pairs_are_untouched(self):
        c = _client()
        other = "skill-weather.jarbas:weather.query"
        h1, h2 = lambda m: None, lambda m: None
        c.on(CANONICAL, h1)
        c.on(other, h2)
        c.remove(CANONICAL, h1)
        self.assertIn(frozenset({other, other + ".intent"}),
                      c._intent_pair_guards)



if __name__ == "__main__":
    unittest.main()
