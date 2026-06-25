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

from ovos_bus_client.client.client import MessageBusClient, _bus_flag
from ovos_bus_client.message import Message
from ovos_spec_tools import NamespaceTranslator


def _client(modernize=True, emit_legacy=True):
    c = MessageBusClient.__new__(MessageBusClient)
    c.emitter = MagicMock()
    c.client = MagicMock()
    c._translator = NamespaceTranslator(modernize=modernize, emit_legacy=emit_legacy)
    c._handler_guards = {}
    c._dedup_registrations = {}
    c.wrapped_funcs = {}
    c.connected_event = Event()
    c.connected_event.set()
    c.started_running = True
    c.session_id = "default"
    return c


def _sent_types(c):
    return [json.loads(call.args[0])["type"] for call in c.client.send.call_args_list]


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


class TestEmitTranslation(unittest.TestCase):
    def test_legacy_emit_adds_spec(self):
        c = _client()
        c.emit(Message("speak", {"utterance": "hi"}))
        self.assertEqual(_sent_types(c), ["speak", "ovos.utterance.speak"])

    def test_spec_emit_adds_legacy(self):
        c = _client()
        c.emit(Message("ovos.utterance.handle", {"utterances": ["hi"]}))
        self.assertEqual(_sent_types(c),
                         ["ovos.utterance.handle", "recognizer_loop:utterance"])

    def test_unmapped_never_translated(self):
        c = _client()
        c.emit(Message("some.topic", {"x": 1}))
        self.assertEqual(_sent_types(c), ["some.topic"])

    def test_flags_off_send_once(self):
        c = _client(modernize=False, emit_legacy=False)
        c.emit(Message("speak", {"utterance": "hi"}))
        self.assertEqual(_sent_types(c), ["speak"])


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
