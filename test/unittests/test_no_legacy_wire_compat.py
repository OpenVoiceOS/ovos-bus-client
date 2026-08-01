"""Guards for the post-compat world: the legacy wire bridge is gone.

These are the inverted twins of the old ``test_namespace_migration.py`` and
``test_intent_legacy_reemit.py``. Those files proved the bridge WORKED. This
one proves it is ABSENT, so a bridge that creeps back in fails the suite
loudly instead of quietly doubling bus traffic again.

Three properties are pinned:

* an ``ovos.*`` spec topic reaches only its own listeners — the legacy topic
  it replaced is never delivered, and the reverse;
* a canonical ``<skill_id>:<intent>`` dispatch never reaches a listener bound
  to the old ``<skill_id>:<intent>.intent`` spelling;
* a deployment that still asks for the bridge by config or environment gets a
  ``RuntimeError``, not silence.
"""
import unittest
from threading import Event
from unittest.mock import MagicMock, patch

from pyee import EventEmitter

from ovos_bus_client.client import client as client_mod
from ovos_bus_client.client.client import MessageBusClient
from ovos_bus_client.message import Message
from ovos_spec_tools import MIGRATION_MAP, SPEC_TO_LEGACY

LEGACY_UTTERANCE = "recognizer_loop:utterance"
SPEC_UTTERANCE = MIGRATION_MAP[LEGACY_UTTERANCE].value
SKILL_ID = "ovos-skill-fake.openvoiceos"
CANONICAL_INTENT = f"{SKILL_ID}:food.order"
LEGACY_INTENT = f"{CANONICAL_INTENT}.intent"


def _client():
    """A client wired to a real synchronous emitter, built without __init__ so
    no websocket or config is touched."""
    c = MessageBusClient.__new__(MessageBusClient)
    c.emitter = EventEmitter()
    c.client = MagicMock()
    c.wrapped_funcs = {}
    c.connected_event = Event()
    c.connected_event.set()
    c.started_running = True
    c.session_id = "default"
    return c


def _deliver(c, msg_type, data=None, context=None):
    """Feed one serialized frame through the receive path."""
    msg = Message(msg_type, data or {}, context or {})
    c.on_message(msg.serialize())


class TestNoNamespaceBridge(unittest.TestCase):
    def test_spec_topic_does_not_reach_legacy_listeners(self):
        c = _client()
        got = []
        c.on(LEGACY_UTTERANCE, lambda m: got.append(m))
        _deliver(c, SPEC_UTTERANCE, {"utterances": ["hello"]})
        self.assertEqual(got, [])

    def test_legacy_topic_does_not_reach_spec_listeners(self):
        c = _client()
        got = []
        c.on(SPEC_UTTERANCE, lambda m: got.append(m))
        _deliver(c, LEGACY_UTTERANCE, {"utterances": ["hello"]})
        self.assertEqual(got, [])

    def test_no_migrated_topic_is_bridged_in_either_direction(self):
        """Sweep the whole map rather than trusting one sample pair."""
        for legacy, spec in MIGRATION_MAP.items():
            with self.subTest(topic=legacy):
                c = _client()
                seen = []
                c.on(legacy, lambda m: seen.append("legacy"))
                c.on(spec.value, lambda m: seen.append("spec"))
                _deliver(c, spec.value)
                self.assertEqual(seen, ["spec"])
                seen.clear()
                _deliver(c, legacy)
                self.assertEqual(seen, ["legacy"])

    def test_handler_on_both_namespaces_is_no_longer_deduped(self):
        """The mirror-guard is gone with the mirror: two real subscriptions to
        two real topics now fire twice, like any other pair of subscriptions."""
        c = _client()
        calls = []

        def handler(message):
            calls.append(message.msg_type)

        c.on(LEGACY_UTTERANCE, handler)
        c.on(SPEC_UTTERANCE, handler)
        _deliver(c, SPEC_UTTERANCE)
        _deliver(c, LEGACY_UTTERANCE)
        self.assertEqual(calls, [SPEC_UTTERANCE, LEGACY_UTTERANCE])

    def test_on_registers_the_callback_itself_not_a_wrapper(self):
        c = _client()

        def handler(message):
            pass

        c.on(SPEC_UTTERANCE, handler)
        self.assertEqual(c.emitter.listeners(SPEC_UTTERANCE), [handler])
        c.remove(SPEC_UTTERANCE, handler)
        self.assertEqual(c.emitter.listeners(SPEC_UTTERANCE), [])


class TestNoIntentTopicTwin(unittest.TestCase):
    def test_canonical_dispatch_does_not_reach_the_suffixed_twin(self):
        c = _client()
        got = []
        c.on(LEGACY_INTENT, lambda m: got.append(m))
        _deliver(c, CANONICAL_INTENT, {"utterance": "order food"})
        self.assertEqual(got, [])

    def test_canonical_listener_still_receives_the_canonical_dispatch(self):
        c = _client()
        got = []
        c.on(CANONICAL_INTENT, lambda m: got.append(m.msg_type))
        _deliver(c, CANONICAL_INTENT, {"utterance": "order food"})
        self.assertEqual(got, [CANONICAL_INTENT])

    def test_no_reemit_marker_is_stamped_on_any_message(self):
        c = _client()
        got = []
        c.on(CANONICAL_INTENT, lambda m: got.append(m))
        _deliver(c, CANONICAL_INTENT)
        self.assertEqual(got[0].context, {})


class TestBridgeSurfaceIsGone(unittest.TestCase):
    """The attributes the bridge hung off must not exist any more."""

    def test_client_module_exports_no_bridge_symbols(self):
        for name in ("INTENT_REEMIT_CONTEXT_KEY", "IntentAliasRegistry",
                     "legacy_reemit_targets", "NamespaceTranslator"):
            self.assertFalse(hasattr(client_mod, name), name)

    def test_client_instances_carry_no_bridge_state(self):
        c = _client()
        for name in ("_translator", "_handler_guards", "_dedup_registrations",
                     "_intent_aliases", "_intent_reemit_blanket"):
            self.assertFalse(hasattr(c, name), name)


class TestRemovedFlagsAreLoud(unittest.TestCase):
    def test_env_flag_raises(self):
        for env_var in ("OVOS_BUS_EMIT_LEGACY", "OVOS_BUS_MODERNIZE",
                        "OVOS_BUS_INTENT_REEMIT_BLANKET"):
            with self.subTest(env_var=env_var):
                with patch.dict(client_mod.environ, {env_var: "true"}):
                    with self.assertRaises(RuntimeError) as ctx:
                        client_mod._reject_removed_bridge_flags()
                self.assertIn(env_var, str(ctx.exception))

    def test_config_flag_raises(self):
        with patch.dict(client_mod.environ, {}, clear=True):
            with patch("ovos_config.Configuration",
                       return_value={"websocket": {"emit_legacy": True}}):
                with self.assertRaises(RuntimeError):
                    client_mod._reject_removed_bridge_flags()

    def test_unset_flags_are_accepted(self):
        with patch.dict(client_mod.environ, {}, clear=True):
            with patch("ovos_config.Configuration", return_value={}):
                client_mod._reject_removed_bridge_flags()


class TestSpecToLegacyIndexStillExists(unittest.TestCase):
    """The spec-tools helpers stay — they are pure functions used by linters
    and migration tooling. Only the bus wiring was removed."""

    def test_map_is_still_importable(self):
        self.assertIn(LEGACY_UTTERANCE, MIGRATION_MAP)
        self.assertIn(SPEC_UTTERANCE, SPEC_TO_LEGACY)


if __name__ == "__main__":
    unittest.main()
