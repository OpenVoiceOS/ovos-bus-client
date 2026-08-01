"""Tests for the legacy intent-topic bridge in MessageBusClient.

Old ovos-workshop built the per-intent dispatch topic from the resource
filename, so ``<skill_id>:food.order.intent`` reached the wire. Current
workshop registers the canonical ``<skill_id>:food.order``. When emit_legacy
is on, a client that has a handler bound to the suffixed spelling also gets
the dispatch mirrored onto that spelling, so the old handler still runs.

The mirror is receive-side and local-only, exactly like the namespace bridge:
it never goes back on the wire.
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


def _received(client, *topics):
    """Bind a recorder to each topic; returns the shared list of Messages."""
    got = []
    for topic in topics:
        client.on(topic, got.append)
    return got


class TestAliasDrivenReemit(unittest.TestCase):
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

    def test_mirror_is_marked_in_context(self):
        c = _client()
        got = _received(c, LEGACY)
        _deliver(c, CANONICAL)
        self.assertTrue(got[0].context[INTENT_REEMIT_CONTEXT_KEY])

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


class TestExactlyOnce(unittest.TestCase):
    def test_one_dispatch_yields_one_mirror(self):
        c = _client()
        got = _received(c, LEGACY)
        _deliver(c, CANONICAL)
        self.assertEqual(len(got), 1)

    def test_two_suffixed_handlers_each_run_once(self):
        c = _client()
        a, b = [], []
        c.on(LEGACY, a.append)
        c.on(LEGACY, b.append)
        _deliver(c, CANONICAL)
        self.assertEqual(len(a), 1)
        self.assertEqual(len(b), 1)

    def test_handler_on_both_spellings_gets_both_topics_once_each(self):
        # the intent bridge does not dedupe across spellings - a handler bound
        # to both asked for both. Workshop collapses aliases at registration.
        c = _client()
        got = _received(c, CANONICAL, LEGACY)
        _deliver(c, CANONICAL)
        self.assertEqual([m.msg_type for m in got], [CANONICAL, LEGACY])


class TestLoopPrevention(unittest.TestCase):
    def test_a_legacy_dispatch_is_not_mirrored_again(self):
        # an old core still dispatching the suffixed topic must not produce a
        # twin of a twin
        c = _client()
        got = _received(c, LEGACY, CANONICAL)
        _deliver(c, LEGACY)
        self.assertEqual([m.msg_type for m in got], [LEGACY])

    def test_a_marked_message_is_not_mirrored(self):
        c = _client()
        got = _received(c, LEGACY)
        _deliver(c, CANONICAL, context={INTENT_REEMIT_CONTEXT_KEY: True})
        self.assertEqual(got, [])

    def test_mirroring_a_mirror_would_terminate(self):
        c = _client(blanket=True)
        got = _received(c, LEGACY)
        _deliver(c, CANONICAL)
        # the mirror is the suffixed spelling; feeding it back produces nothing
        c.on_message(got[0].serialize())
        self.assertEqual(len(got), 2)  # the direct delivery only, no third twin
        self.assertEqual(got[1].msg_type, LEGACY)


class TestBlanketMode(unittest.TestCase):
    def test_blanket_mirrors_without_any_registration(self):
        c = _client(blanket=True)
        got = []
        c.emitter.on(LEGACY, got.append)  # subscribe behind the client's back
        _deliver(c, CANONICAL)
        self.assertEqual([m.msg_type for m in got], [LEGACY])

    def test_blanket_off_by_default(self):
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


class TestDisabled(unittest.TestCase):
    def test_no_mirror_when_emit_legacy_is_off(self):
        c = _client(emit_legacy=False)
        got = _received(c, LEGACY)
        _deliver(c, CANONICAL)
        self.assertEqual(got, [])

    def test_no_mirror_when_emit_legacy_is_off_even_in_blanket(self):
        c = _client(emit_legacy=False, blanket=True)
        got = []
        c.emitter.on(LEGACY, got.append)
        _deliver(c, CANONICAL)
        self.assertEqual(got, [])

    def test_no_mirror_without_spec_tools_intent_support(self):
        c = _client()
        c._intent_aliases = None  # older spec-tools: helpers not importable
        got = []
        c.emitter.on(LEGACY, got.append)
        _deliver(c, CANONICAL)
        self.assertEqual(got, [])


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


class TestWireBehaviourUnchanged(unittest.TestCase):
    def test_emitting_an_intent_puts_exactly_one_message_on_the_wire(self):
        c = _client()
        _received(c, LEGACY)
        c.emit(Message(CANONICAL, {"utterance": "one pizza"}))
        sent = [json.loads(call.args[0])["type"]
                for call in c.client.send.call_args_list]
        self.assertEqual(sent, [CANONICAL])


if __name__ == "__main__":
    unittest.main()
