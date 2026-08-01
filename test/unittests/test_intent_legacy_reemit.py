"""Tests for the legacy intent-topic bridge in MessageBusClient.

Old ovos-workshop built the per-intent dispatch topic from the resource
filename, so ``<skill_id>:food.order.intent`` reached the wire. Current
workshop registers the canonical ``<skill_id>:food.order``. The bridge below
keeps the two spellings talking to each other across every version pairing of
skill container and core:

* new core, OLD skill container - the twin goes on the WIRE, because an old
  bus-client holds no bridge of its own. This is the primary path;
* OLD core, new skill - the receiving client modernizes the suffixed dispatch
  onto its canonical topic;
* new on both sides - both spellings arrive and delivery is deduplicated, so
  each local handler runs once.
"""
import json
import unittest
from threading import Event
from unittest.mock import MagicMock

from pyee import EventEmitter

from ovos_bus_client.client.client import (INTENT_REEMIT_CONTEXT_KEY,
                                           MessageBusClient)
from ovos_bus_client.message import Message
from ovos_spec_tools import NamespaceTranslator
from ovos_spec_tools.intent_topics import IntentAliasRegistry

CANONICAL = "skill-food.jarbas:food.order"
LEGACY = "skill-food.jarbas:food.order.intent"


def _client(emit_legacy=True, blanket=False):
    """A client on a real synchronous emitter so dispatch is observable."""
    c = MessageBusClient.__new__(MessageBusClient)
    c.emitter = EventEmitter()
    c.client = MagicMock()
    c._translator = NamespaceTranslator(modernize=True, emit_legacy=emit_legacy)
    c._handler_guards = {}
    c._dedup_registrations = {}
    c._intent_aliases = IntentAliasRegistry()
    c._wire_intent_aliases = set()
    c._intent_delivered = {}
    c._intent_reemit_blanket = blanket
    c.wrapped_funcs = {}
    c.connected_event = Event()
    c.connected_event.set()
    c.started_running = True
    c.session_id = "default"
    return c


def _deliver(client, msg_type, data=None, context=None):
    """Feed a serialized message in as if it arrived off the websocket."""
    msg = Message(msg_type, data or {}, context or {})
    client.on_message(msg.serialize())
    return msg


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


class TestWireTwin(unittest.TestCase):
    """The primary path: the twin travels on the wire, not just in-process."""

    def test_aliased_intent_is_twinned_on_the_wire(self):
        c = _client()
        c.on(LEGACY, lambda m: None)
        c.emit(Message(CANONICAL, {"utterance": "one pizza"}))
        self.assertEqual(_sent(c), [CANONICAL, LEGACY])

    def test_twin_is_sent_exactly_once(self):
        c = _client()
        c.on(LEGACY, lambda m: None)
        c.on(LEGACY, lambda m: None)  # two listeners, still one twin
        c.emit(Message(CANONICAL))
        self.assertEqual(_sent(c).count(LEGACY), 1)

    def test_twin_carries_the_same_payload_and_context(self):
        c = _client()
        c.on(LEGACY, lambda m: None)
        c.emit(Message(CANONICAL, {"a": 1}, {"source": ["me"]}))
        canonical, twin = _sent_messages(c)
        self.assertEqual(twin["data"], canonical["data"])
        self.assertEqual(twin["context"]["source"], ["me"])

    def test_twin_is_marked_on_the_wire(self):
        c = _client()
        c.on(LEGACY, lambda m: None)
        c.emit(Message(CANONICAL))
        self.assertTrue(_sent_messages(c)[1]["context"][INTENT_REEMIT_CONTEXT_KEY])

    def test_canonical_goes_first(self):
        # a receiver that bridges both spellings must see the canonical
        # dispatch first, so the twin is the frame it drops as the duplicate
        c = _client()
        c.on(LEGACY, lambda m: None)
        c.emit(Message(CANONICAL))
        self.assertEqual(_sent(c)[0], CANONICAL)

    def test_unaliased_intent_puts_one_message_on_the_wire(self):
        c = _client()
        c.on(CANONICAL, lambda m: None)
        c.emit(Message(CANONICAL))
        self.assertEqual(_sent(c), [CANONICAL])

    def test_non_intent_topics_are_never_twinned(self):
        c = _client()
        c.emit(Message("ovos.utterance.handled"))
        self.assertEqual(_sent(c), ["ovos.utterance.handled"])

    def test_a_suffixed_emit_is_not_twinned_again(self):
        c = _client()
        c.on(LEGACY, lambda m: None)
        c.emit(Message(LEGACY))
        self.assertEqual(_sent(c), [LEGACY])

    def test_a_marked_emit_is_not_twinned(self):
        c = _client()
        c.on(LEGACY, lambda m: None)
        c.emit(Message(CANONICAL, context={INTENT_REEMIT_CONTEXT_KEY: True}))
        self.assertEqual(_sent(c), [CANONICAL])


class TestRegistrationObservation(unittest.TestCase):
    """The emitting process learns aliases by watching registrations."""

    def test_padatious_registration_teaches_the_alias(self):
        c = _client()
        _deliver(c, "padatious:register_intent",
                 {"name": LEGACY, "lang": "en-US", "samples": ["one pizza"]})
        c.emit(Message(CANONICAL))
        self.assertEqual(_sent(c), [CANONICAL, LEGACY])

    def test_adapt_registration_teaches_the_alias(self):
        c = _client()
        _deliver(c, "register_intent", {"name": LEGACY, "requires": []})
        c.emit(Message(CANONICAL))
        self.assertEqual(_sent(c), [CANONICAL, LEGACY])

    def test_intent4_keyword_registration_teaches_the_alias(self):
        c = _client()
        _deliver(c, "ovos.intent.register.keyword",
                 {"skill_id": "skill-food.jarbas",
                  "intent_name": "food.order.intent", "lang": "en-US"})
        c.emit(Message(CANONICAL))
        self.assertEqual(_sent(c), [CANONICAL, LEGACY])

    def test_intent4_template_registration_teaches_the_alias(self):
        c = _client()
        _deliver(c, "ovos.intent.register.template",
                 {"skill_id": "skill-food.jarbas",
                  "intent_name": "food.order.intent", "lang": "en-US"})
        c.emit(Message(CANONICAL))
        self.assertEqual(_sent(c), [CANONICAL, LEGACY])

    def test_skill_id_may_come_from_context(self):
        c = _client()
        _deliver(c, "ovos.intent.register.keyword",
                 {"intent_name": "food.order.intent", "lang": "en-US"},
                 {"skill_id": "skill-food.jarbas"})
        c.emit(Message(CANONICAL))
        self.assertEqual(_sent(c), [CANONICAL, LEGACY])

    def test_a_canonical_registration_teaches_nothing(self):
        c = _client()
        _deliver(c, "padatious:register_intent",
                 {"name": CANONICAL, "lang": "en-US"})
        c.emit(Message(CANONICAL))
        self.assertEqual(_sent(c), [CANONICAL])

    def test_a_registration_without_a_skill_id_is_ignored(self):
        c = _client()
        _deliver(c, "ovos.intent.register.keyword",
                 {"intent_name": "food.order.intent", "lang": "en-US"})
        c.emit(Message(CANONICAL))
        self.assertEqual(_sent(c), [CANONICAL])

    def test_a_malformed_registration_is_ignored(self):
        c = _client()
        _deliver(c, "padatious:register_intent", {"lang": "en-US"})
        _deliver(c, "padatious:register_intent", {"name": ""})
        _deliver(c, "padatious:register_intent", {"name": 42})
        c.emit(Message(CANONICAL))
        self.assertEqual(_sent(c), [CANONICAL])

    def test_entity_registrations_teach_nothing(self):
        c = _client()
        _deliver(c, "padatious:register_entity",
                 {"name": LEGACY, "lang": "en-US"})
        c.emit(Message(CANONICAL))
        self.assertEqual(_sent(c), [CANONICAL])

    def test_a_wire_alias_survives_a_local_remove(self):
        # the listener that wants the twin lives in another process, so a
        # local remove() says nothing about it
        c = _client()
        _deliver(c, "padatious:register_intent", {"name": LEGACY})
        handler = lambda m: None
        c.on(LEGACY, handler)
        c.remove(LEGACY, handler)
        c.emit(Message(CANONICAL))
        self.assertEqual(_sent(c), [CANONICAL, LEGACY])


class TestInboundModernize(unittest.TestCase):
    """OLD core, new skill: a suffixed dispatch reaches a canonical listener."""

    def test_suffixed_dispatch_reaches_a_canonical_listener(self):
        c = _client()
        got = _received(c, CANONICAL)
        _deliver(c, LEGACY, {"utterance": "one pizza"})
        self.assertEqual([m.msg_type for m in got], [CANONICAL])
        self.assertEqual(got[0].data, {"utterance": "one pizza"})

    def test_suffixed_dispatch_fires_a_canonical_listener_exactly_once(self):
        c = _client()
        got = _received(c, CANONICAL)
        _deliver(c, LEGACY)
        self.assertEqual(len(got), 1)

    def test_the_modernized_copy_is_marked(self):
        c = _client()
        got = _received(c, CANONICAL)
        _deliver(c, LEGACY)
        self.assertTrue(got[0].context[INTENT_REEMIT_CONTEXT_KEY])

    def test_both_spellings_still_each_run_once(self):
        c = _client()
        canonical_got, legacy_got = [], []
        c.on(CANONICAL, canonical_got.append)
        c.on(LEGACY, legacy_got.append)
        _deliver(c, LEGACY)
        self.assertEqual(len(canonical_got), 1)
        self.assertEqual(len(legacy_got), 1)

    def test_modernizing_is_not_put_back_on_the_wire(self):
        c = _client()
        _received(c, CANONICAL)
        _deliver(c, LEGACY)
        c.client.send.assert_not_called()


class TestTwinnedPairDedup(unittest.TestCase):
    """New core, new skill: both spellings arrive, each handler runs once."""

    def _deliver_pair(self, client, data=None, context=None):
        """The two frames a new core puts on the wire, in wire order."""
        canonical = Message(CANONICAL, data or {}, dict(context or {}))
        client.on_message(canonical.serialize())
        twin = canonical.forward(LEGACY, canonical.data)
        twin.context[INTENT_REEMIT_CONTEXT_KEY] = True
        client.on_message(twin.serialize())

    def test_a_canonical_listener_runs_once(self):
        c = _client()
        got = _received(c, CANONICAL)
        self._deliver_pair(c)
        self.assertEqual(len(got), 1)

    def test_a_suffixed_listener_runs_once(self):
        c = _client()
        got = _received(c, LEGACY)
        self._deliver_pair(c)
        self.assertEqual(len(got), 1)

    def test_a_listener_on_both_spellings_runs_once_per_spelling(self):
        c = _client()
        got = _received(c, CANONICAL, LEGACY)
        self._deliver_pair(c)
        self.assertEqual(sorted(m.msg_type for m in got), [CANONICAL, LEGACY])

    def test_the_dropped_twin_does_not_reach_the_firehose(self):
        c = _client()
        got = []
        c.on(LEGACY, lambda m: None)
        c.emitter.on("message", got.append)
        self._deliver_pair(c)
        self.assertEqual(len(got), 1)

    def test_an_unbridged_listener_still_gets_the_wire_twin(self):
        # nobody registered the alias here, so the local mirror never runs -
        # the twin is exactly what this listener was sent
        c = _client()
        got = []
        c.emitter.on(LEGACY, got.append)  # subscribed behind the client's back
        self._deliver_pair(c)
        self.assertEqual([m.msg_type for m in got], [LEGACY])

    def test_two_separate_dispatches_are_two_events(self):
        # dedup collapses a near-simultaneous pair, never two real dispatches
        c = _client()
        got = _received(c, CANONICAL)
        self._deliver_pair(c, {"utterance": "one pizza"})
        self._deliver_pair(c, {"utterance": "two pizzas"})
        self.assertEqual(len(got), 2)

    def test_the_dedup_window_expires(self):
        c = _client()
        c._translator.window = 0  # every record is stale on the next look
        got = _received(c, LEGACY)
        self._deliver_pair(c)
        self.assertEqual(len(got), 2)  # bridged copy plus the wire twin


class TestLoopPrevention(unittest.TestCase):
    def test_a_bridged_copy_fed_back_produces_nothing_new(self):
        c = _client()
        got = _received(c, LEGACY)
        _deliver(c, CANONICAL)
        c.on_message(got[0].serialize())  # the twin, back off the wire
        self.assertEqual(len(got), 1)

    def test_a_suffixed_dispatch_is_not_twinned_again(self):
        c = _client()
        got = _received(c, LEGACY, CANONICAL)
        _deliver(c, LEGACY)
        self.assertEqual([m.msg_type for m in got], [LEGACY, CANONICAL])

    def test_no_cascade_in_blanket_mode(self):
        c = _client(blanket=True)
        got = _received(c, LEGACY)
        _deliver(c, CANONICAL)
        c.on_message(got[0].serialize())
        self.assertEqual(len(got), 1)


class TestAliasDrivenLocalMirror(unittest.TestCase):
    """The secondary layer: an old-style listener in THIS process."""

    def test_suffixed_subscription_receives_canonical_dispatch(self):
        c = _client()
        got = _received(c, LEGACY)
        _deliver(c, CANONICAL, {"utterance": "one pizza"})
        self.assertEqual([m.msg_type for m in got], [LEGACY])
        self.assertEqual(got[0].data, {"utterance": "one pizza"})

    def test_mirror_keeps_data_and_context(self):
        c = _client()
        got = _received(c, LEGACY)
        _deliver(c, CANONICAL, {"a": 1}, {"session": {"session_id": "abc"},
                                          "source": ["me"]})
        self.assertEqual(got[0].data, {"a": 1})
        self.assertEqual(got[0].context["source"], ["me"])
        self.assertEqual(got[0].context["session"]["session_id"], "abc")

    def test_no_mirror_without_a_suffixed_subscription(self):
        c = _client()
        got = _received(c, CANONICAL)
        _deliver(c, CANONICAL)
        self.assertEqual([m.msg_type for m in got], [CANONICAL])

    def test_mirror_is_not_put_back_on_the_wire(self):
        c = _client()
        _received(c, LEGACY)
        _deliver(c, CANONICAL)
        c.client.send.assert_not_called()

    def test_once_subscription_also_registers_the_alias(self):
        c = _client()
        got = []
        c.once(LEGACY, got.append)
        _deliver(c, CANONICAL)
        self.assertEqual([m.msg_type for m in got], [LEGACY])

    def test_non_intent_topics_are_never_mirrored(self):
        c = _client()
        got = []
        c.on("ovos.utterance.handled", got.append)
        c.on("ovos.utterance.handled.intent", got.append)
        _deliver(c, "ovos.utterance.handled")
        self.assertEqual([m.msg_type for m in got], ["ovos.utterance.handled"])

    def test_two_suffixed_handlers_each_run_once(self):
        c = _client()
        a, b = [], []
        c.on(LEGACY, a.append)
        c.on(LEGACY, b.append)
        _deliver(c, CANONICAL)
        self.assertEqual(len(a), 1)
        self.assertEqual(len(b), 1)


class TestBlanketMode(unittest.TestCase):
    def test_blanket_twins_every_intent_on_the_wire(self):
        c = _client(blanket=True)
        c.emit(Message(CANONICAL))  # nothing registered at all
        self.assertEqual(_sent(c), [CANONICAL, LEGACY])

    def test_blanket_mirrors_locally_without_any_registration(self):
        c = _client(blanket=True)
        got = []
        c.emitter.on(LEGACY, got.append)  # subscribe behind the client's back
        _deliver(c, CANONICAL)
        self.assertEqual([m.msg_type for m in got], [LEGACY])

    def test_blanket_off_by_default_on_the_wire(self):
        c = _client()
        c.emit(Message(CANONICAL))
        self.assertEqual(_sent(c), [CANONICAL])

    def test_blanket_off_by_default_locally(self):
        c = _client()
        got = []
        c.emitter.on(LEGACY, got.append)
        _deliver(c, CANONICAL)
        self.assertEqual(got, [])

    def test_blanket_still_skips_non_intent_topics(self):
        c = _client(blanket=True)
        got = []
        c.emitter.on("ovos.utterance.handled.intent", got.append)
        _deliver(c, "ovos.utterance.handled")
        self.assertEqual(got, [])
        c.emit(Message("ovos.utterance.handled"))
        self.assertEqual(_sent(c), ["ovos.utterance.handled"])


class TestDisabled(unittest.TestCase):
    def test_no_wire_twin_when_emit_legacy_is_off(self):
        c = _client(emit_legacy=False)
        c.on(LEGACY, lambda m: None)
        c.emit(Message(CANONICAL))
        self.assertEqual(_sent(c), [CANONICAL])

    def test_no_wire_twin_when_emit_legacy_is_off_even_in_blanket(self):
        c = _client(emit_legacy=False, blanket=True)
        c.emit(Message(CANONICAL))
        self.assertEqual(_sent(c), [CANONICAL])

    def test_no_mirror_when_emit_legacy_is_off(self):
        c = _client(emit_legacy=False)
        got = _received(c, LEGACY)
        _deliver(c, CANONICAL)
        self.assertEqual(got, [])

    def test_no_modernize_when_emit_legacy_is_off(self):
        c = _client(emit_legacy=False)
        got = _received(c, CANONICAL)
        _deliver(c, LEGACY)
        self.assertEqual(got, [])

    def test_no_mirror_when_emit_legacy_is_off_even_in_blanket(self):
        c = _client(emit_legacy=False, blanket=True)
        got = []
        c.emitter.on(LEGACY, got.append)
        _deliver(c, CANONICAL)
        self.assertEqual(got, [])

    def test_nothing_bridged_without_spec_tools_intent_support(self):
        c = _client()
        c._intent_aliases = None  # older spec-tools: helpers not importable
        got = []
        c.emitter.on(LEGACY, got.append)
        _deliver(c, CANONICAL)
        self.assertEqual(got, [])
        c.emit(Message(CANONICAL))
        self.assertEqual(_sent(c), [CANONICAL])


class TestAliasLifecycle(unittest.TestCase):
    def test_removing_the_last_suffixed_handler_stops_the_mirror(self):
        c = _client()
        got = []
        c.on(LEGACY, got.append)
        c.remove(LEGACY, got.append)
        _deliver(c, CANONICAL)
        self.assertEqual(got, [])

    def test_remove_all_listeners_stops_the_mirror(self):
        c = _client()
        got = []
        c.on(LEGACY, got.append)
        c.remove_all_listeners(LEGACY)
        _deliver(c, CANONICAL)
        self.assertEqual(got, [])

    def test_one_removal_of_two_handlers_keeps_the_mirror(self):
        c = _client()
        a, b = [], []
        c.on(LEGACY, a.append)
        c.on(LEGACY, b.append)
        c.remove(LEGACY, a.append)
        _deliver(c, CANONICAL)
        self.assertEqual(len(b), 1)

    def test_removing_the_last_suffixed_handler_stops_the_wire_twin(self):
        c = _client()
        handler = lambda m: None
        c.on(LEGACY, handler)
        c.remove(LEGACY, handler)
        c.emit(Message(CANONICAL))
        self.assertEqual(_sent(c), [CANONICAL])


if __name__ == "__main__":
    unittest.main()
