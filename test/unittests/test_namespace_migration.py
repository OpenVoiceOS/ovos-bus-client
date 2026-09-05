"""Tests for namespace translation + handler dedup in MessageBusClient.

Two orthogonal emit-side flags, both ON by default during the migration window:
  modernize   : emitting a legacy topic also emits the ovos.* spec topic.
  emit_legacy : emitting an ovos.* spec topic also emits the legacy topic.
So every migrated event travels on BOTH namespaces. A handler registered on the
legacy AND the spec topic is deduped (the mirror copy is dropped) so it fires
once — without suppressing two genuine same-topic events.
"""
import json
import unittest
from threading import Event
from unittest.mock import MagicMock, patch

from pyee import EventEmitter

from ovos_bus_client.client.client import MessageBusClient, _bus_flag
from ovos_bus_client.message import Message
from ovos_spec_tools import NamespaceTranslator


def _client(modernize=True, emit_legacy=True, emitter=None, wire_legacy_twins=True):
    c = MessageBusClient.__new__(MessageBusClient)
    c.emitter = emitter if emitter is not None else MagicMock()
    c.client = MagicMock()
    c._translator = NamespaceTranslator(modernize=modernize, emit_legacy=emit_legacy)
    c._wire_legacy_twins = wire_legacy_twins
    c._handler_guards = {}
    c._dedup_registrations = {}
    c.wrapped_funcs = {}
    c.connected_event = Event()
    c.connected_event.set()
    c.started_running = True
    c.session_id = "default"
    return c


def _recv_client(modernize=True, emit_legacy=True):
    """A client wired to a real synchronous emitter so on_message dispatch
    (the receive-side namespace bridge) can be observed deterministically."""
    return _client(modernize=modernize, emit_legacy=emit_legacy,
                   emitter=EventEmitter())


def _sent_types(c):
    return [json.loads(call.args[0])["type"] for call in c.client.send.call_args_list]


def _sent(c):
    """[(topic, data), ...] for every serialized Message put on the wire."""
    out = []
    for call in c.client.send.call_args_list:
        payload = json.loads(call.args[0])
        out.append((payload["type"], payload["data"]))
    return out


class TestDefaultsOn(unittest.TestCase):
    def test_both_flags_default_on(self):
        # no env var and empty config -> the default (True) is returned
        env = {k: v for k, v in __import__("os").environ.items()
               if k not in ("OVOS_BUS_MODERNIZE", "OVOS_BUS_EMIT_LEGACY")}
        with patch.dict("os.environ", env, clear=True), \
                patch("ovos_config.Configuration", return_value={}):
            self.assertTrue(_bus_flag("OVOS_BUS_MODERNIZE", "modernize", default=True))
            self.assertTrue(_bus_flag("OVOS_BUS_EMIT_LEGACY", "emit_legacy", default=True))

    def test_env_can_disable(self):
        with patch.dict("os.environ", {"OVOS_BUS_EMIT_LEGACY": "false"}):
            self.assertFalse(_bus_flag("OVOS_BUS_EMIT_LEGACY", "emit_legacy", default=True))


class TestEmitSendsOnce(unittest.TestCase):
    """A legacy emit puts exactly ONE message on the wire: its spec counterpart
    is bridged on the receive side only, never as a second wire copy (which the
    broadcast server would echo back and double in the capture firehose).

    A CANONICAL (``ovos.*``) emit of a migrated topic is the asymmetric case
    (bus-client wire-twin fix, RULE 1 of the namespace bridge, mirroring the
    intent-topic bridge above): it puts a REAL second wire frame out on the
    legacy spelling too, because an old pre-spec-tools client subscribed only
    to the legacy topic has no translator of its own and a receive-side-only
    bridge never reaches it. See test_namespace_wire_twin.py for the full
    twin/dedup coverage."""

    def test_legacy_emit_sends_once(self):
        c = _client()
        c.emit(Message("speak", {"utterance": "hi"}))
        self.assertEqual(_sent_types(c), ["speak"])

    def test_spec_emit_sends_the_canonical_frame_and_its_legacy_twin(self):
        c = _client()
        c.emit(Message("ovos.utterance.handle", {"utterances": ["hi"]}))
        self.assertEqual(_sent_types(c),
                         ["ovos.utterance.handle", "recognizer_loop:utterance"])

    def test_unmapped_sends_once(self):
        c = _client()
        c.emit(Message("some.topic", {"x": 1}))
        self.assertEqual(_sent_types(c), ["some.topic"])

    def test_flags_off_send_once(self):
        c = _client(modernize=False, emit_legacy=False)
        c.emit(Message("speak", {"utterance": "hi"}))
        self.assertEqual(_sent_types(c), ["speak"])


class TestReceiveSideBridge(unittest.TestCase):
    """on_message dispatches the received topic AND its namespace counterpart to
    LOCAL listeners, firing the 'message' capture firehose exactly once."""

    def _firehose(self, c):
        seen = []
        c.emitter.on("message", lambda m: seen.append(m))
        return seen

    def test_one_wire_message_one_firehose_entry(self):
        c = _recv_client()
        firehose = self._firehose(c)
        c.on_message(Message("ovos.utterance.speak", {"utterance": "hi"}).serialize())
        self.assertEqual(len(firehose), 1)

    def test_spec_wire_reaches_legacy_listener(self):
        c = _recv_client()
        legacy_seen = []
        c.on("speak", lambda m: legacy_seen.append(m))
        c.on_message(Message("ovos.utterance.speak", {"utterance": "hi"}).serialize())
        self.assertEqual(len(legacy_seen), 1)
        self.assertEqual(legacy_seen[0].msg_type, "speak")
        self.assertEqual(legacy_seen[0].data["utterance"], "hi")

    def test_legacy_wire_reaches_spec_listener(self):
        c = _recv_client()
        spec_seen = []
        c.on("ovos.utterance.handle", lambda m: spec_seen.append(m))
        c.on_message(Message("recognizer_loop:utterance",
                             {"utterances": ["hi"]}).serialize())
        self.assertEqual(len(spec_seen), 1)
        self.assertEqual(spec_seen[0].data["utterances"], ["hi"])

    def test_counterpart_not_re_sent_on_wire(self):
        # bridging the counterpart to local listeners must not put it back on
        # the wire (that would re-broadcast and double the firehose everywhere)
        c = _recv_client()
        c.on_message(Message("ovos.utterance.speak", {"utterance": "hi"}).serialize())
        c.client.send.assert_not_called()

    def test_counterpart_snapshots_context_before_handler_dispatch(self):
        """Async handlers cannot race counterpart context construction."""
        c = _recv_client()
        counterpart_seen = []

        def mutate_original(message):
            message.context["request_id"] = "handler-mutated"

        c.emitter.on("speak", mutate_original)
        c.emitter.on(
            "ovos.utterance.speak",
            lambda message: counterpart_seen.append(message),
        )
        c.on_message(Message(
            "speak",
            {"utterance": "hi"},
            {"request_id": "wire-value"},
        ).serialize())

        self.assertEqual(len(counterpart_seen), 1)
        self.assertEqual(
            counterpart_seen[0].context["request_id"],
            "wire-value",
        )


class TestReceiveSidePayloadTranslation(unittest.TestCase):
    """The locally-dispatched counterpart carries the counterpart topic's PAYLOAD
    shape, not a verbatim copy (translate_payload bridge)."""

    def _capture(self, c, topic):
        seen = []
        c.on(topic, lambda m: seen.append(m))
        return seen

    def test_shape_changing_legacy_to_spec_reshapes_payload(self):
        c = _recv_client()
        spec_seen = self._capture(c, "ovos.intent.deregister")
        # legacy detach_intent payload shape: {"intent_name": "<skill_id>:<name>"}
        c.on_message(Message("detach_intent",
                             {"intent_name": "skill.foo:HelloIntent"}).serialize())
        self.assertEqual(len(spec_seen), 1)
        self.assertEqual(spec_seen[0].msg_type, "ovos.intent.deregister")
        # reshaped into the spec shape ({"skill_id", "intent_name"})
        self.assertEqual(spec_seen[0].data,
                         {"skill_id": "skill.foo", "intent_name": "HelloIntent"})

    def test_shape_changing_spec_to_legacy_reshapes_payload(self):
        c = _recv_client()
        legacy_seen = self._capture(c, "detach_intent")
        c.on_message(Message("ovos.intent.deregister",
                             {"skill_id": "skill.foo",
                              "intent_name": "HelloIntent"}).serialize())
        self.assertEqual(len(legacy_seen), 1)
        self.assertEqual(legacy_seen[0].msg_type, "detach_intent")
        # reshaped to the legacy munged form -> "<skill_id>:<intent_name>"
        self.assertEqual(legacy_seen[0].data,
                         {"intent_name": "skill.foo:HelloIntent"})

    def test_payload_compatible_rename_stays_equivalent(self):
        c = _recv_client()
        spec_seen = self._capture(c, "ovos.utterance.speak")
        data = {"utterance": "hi", "lang": "en-us"}
        c.on_message(Message("speak", dict(data)).serialize())
        self.assertEqual(len(spec_seen), 1)
        # payload-compatible rename: counterpart carries equivalent (identity) data
        self.assertEqual(spec_seen[0].data, data)


class TestHandlerDedup(unittest.TestCase):
    def _wrappers(self, c, func):
        return [w for _, w in c._dedup_registrations[func]]

    def test_dual_registered_handler_fires_once_on_mirror_pair(self):
        c = _client()
        handler = MagicMock()
        c.on("speak", handler)
        c.on("ovos.utterance.speak", handler)
        w_legacy, w_spec = self._wrappers(c, handler)
        data = {"utterance": "hi", "lang": "en-us"}
        w_legacy(Message("speak", data))               # original
        w_spec(Message("ovos.utterance.speak", data))  # mirror -> dropped
        handler.assert_called_once()

    def test_same_topic_repeats_not_suppressed(self):
        c = _client()
        handler = MagicMock()
        c.on("speak", handler)
        (w,) = self._wrappers(c, handler)
        data = {"utterance": "ok"}
        w(Message("speak", data))
        w(Message("speak", data))  # genuine repeat on the SAME topic -> fires
        self.assertEqual(handler.call_count, 2)

    def test_single_namespace_handler_always_fires(self):
        c = _client()
        handler = MagicMock()
        c.on("ovos.utterance.speak", handler)
        (w,) = self._wrappers(c, handler)
        w(Message("ovos.utterance.speak", {"utterance": "a"}))
        w(Message("ovos.utterance.speak", {"utterance": "b"}))
        self.assertEqual(handler.call_count, 2)

    def test_dedup_expires_after_window(self):
        c = _client()
        handler = MagicMock()
        c.on("speak", handler)
        c.on("ovos.utterance.speak", handler)
        w_legacy, w_spec = self._wrappers(c, handler)
        data = {"utterance": "hi"}
        with patch("ovos_spec_tools.messages.time.monotonic") as clk:
            clk.return_value = 0.0
            w_legacy(Message("speak", data))
            clk.return_value = 2.0  # window elapsed -> mirror no longer collapsed
            w_spec(Message("ovos.utterance.speak", data))
        self.assertEqual(handler.call_count, 2)

    def test_unmapped_topic_handler_not_wrapped(self):
        c = _client()
        handler = MagicMock()
        c.on("some.topic", handler)
        c.emitter.on.assert_called_once_with("some.topic", handler)
        self.assertNotIn(handler, c._dedup_registrations)


class TestContextInFingerprint(unittest.TestCase):
    def test_same_payload_different_session_not_collapsed(self):
        # two distinct events, same data, different session context, on the
        # counterpart topics -> must BOTH fire (not treated as a mirror pair)
        c = _client()
        handler = MagicMock()
        c.on("speak", handler)
        c.on("ovos.utterance.speak", handler)
        w_legacy, w_spec = [w for _, w in c._dedup_registrations[handler]]
        data = {"utterance": "hi"}
        w_legacy(Message("speak", data, context={"session": {"session_id": "A"}}))
        w_spec(Message("ovos.utterance.speak", data,
                       context={"session": {"session_id": "B"}}))
        self.assertEqual(handler.call_count, 2)

    def test_mirror_same_context_collapsed(self):
        c = _client()
        handler = MagicMock()
        c.on("speak", handler)
        c.on("ovos.utterance.speak", handler)
        w_legacy, w_spec = [w for _, w in c._dedup_registrations[handler]]
        ctx = {"session": {"session_id": "A"}}
        data = {"utterance": "hi"}
        w_legacy(Message("speak", data, context=dict(ctx)))
        w_spec(Message("ovos.utterance.speak", data, context=dict(ctx)))
        handler.assert_called_once()


class TestRemove(unittest.TestCase):
    def test_remove_cleans_dedup_state(self):
        c = _client()
        c._remove_normal = MagicMock()
        handler = MagicMock()
        c.on("speak", handler)
        c.on("ovos.utterance.speak", handler)
        c.remove("speak", handler)
        c.remove("ovos.utterance.speak", handler)
        self.assertNotIn(handler, c._dedup_registrations)
        self.assertNotIn(handler, c._handler_guards)
        self.assertEqual(c._remove_normal.call_count, 2)

    def test_remove_resolves_on_collect_wrapper(self):
        # an on_collect-style wrapper on a migrated topic must be removed at the
        # dedup layer too (registration is keyed by the collector wrapper)
        c = _client()
        c._remove_normal = MagicMock()
        public = MagicMock()
        collector = MagicMock()        # the wrapper on_collect() would build
        c.wrapped_funcs[public] = collector
        c.on("ovos.utterance.speak", collector)   # registers dedup wrapper
        c.remove("ovos.utterance.speak", public)  # remove by the PUBLIC func
        self.assertNotIn(collector, c._dedup_registrations)
        self.assertNotIn(public, c.wrapped_funcs)
        self.assertEqual(c._remove_normal.call_count, 1)


if __name__ == "__main__":
    unittest.main()
