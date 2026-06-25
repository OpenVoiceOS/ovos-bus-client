"""Tests for opt-in namespace translation on emit in MessageBusClient.

Two orthogonal, emit-side flags (both default off):
  modernize   : emitting a legacy topic also emits the ovos.* spec topic.
  emit_legacy : emitting an ovos.* spec topic also emits the legacy topic.

There is no listener-side magic: on()/remove() are unchanged, and each listener
subscribes to exactly one namespace.
"""
import json
import unittest
from threading import Event
from unittest.mock import MagicMock, patch

from ovos_bus_client.client.client import MessageBusClient, _bus_flag
from ovos_bus_client.message import Message


def _client(modernize=False, emit_legacy=False):
    c = MessageBusClient.__new__(MessageBusClient)
    c.emitter = MagicMock()
    c.client = MagicMock()
    c._modernize = modernize
    c._emit_legacy = emit_legacy
    c.wrapped_funcs = {}
    c.connected_event = Event()
    c.connected_event.set()
    c.started_running = True
    c.session_id = "default"
    return c


def _sent_types(c):
    return [json.loads(call.args[0])["type"] for call in c.client.send.call_args_list]


class TestFlagReading(unittest.TestCase):
    def test_env_enables(self):
        with patch.dict("os.environ", {"OVOS_BUS_MODERNIZE": "true"}):
            self.assertTrue(_bus_flag("OVOS_BUS_MODERNIZE", "modernize"))

    def test_env_disables(self):
        with patch.dict("os.environ", {"OVOS_BUS_EMIT_LEGACY": "0"}):
            self.assertFalse(_bus_flag("OVOS_BUS_EMIT_LEGACY", "emit_legacy"))


class TestModernize(unittest.TestCase):
    def test_legacy_emit_also_sends_spec(self):
        c = _client(modernize=True)
        c.emit(Message("speak", {"utterance": "hi"}))
        self.assertEqual(_sent_types(c), ["speak", "ovos.utterance.speak"])

    def test_spec_emit_not_translated_to_legacy(self):
        c = _client(modernize=True)  # modernize does NOT add legacy
        c.emit(Message("ovos.utterance.speak", {"utterance": "hi"}))
        self.assertEqual(_sent_types(c), ["ovos.utterance.speak"])


class TestEmitLegacy(unittest.TestCase):
    def test_spec_emit_also_sends_legacy(self):
        c = _client(emit_legacy=True)
        c.emit(Message("ovos.utterance.handle", {"utterances": ["hi"]}))
        self.assertEqual(_sent_types(c),
                         ["ovos.utterance.handle", "recognizer_loop:utterance"])

    def test_legacy_emit_not_translated_to_spec(self):
        c = _client(emit_legacy=True)  # emit_legacy does NOT add spec
        c.emit(Message("recognizer_loop:utterance", {"utterances": ["hi"]}))
        self.assertEqual(_sent_types(c), ["recognizer_loop:utterance"])


class TestBothAndNeither(unittest.TestCase):
    def test_both_off_sends_once(self):
        c = _client()
        c.emit(Message("speak", {"utterance": "hi"}))
        self.assertEqual(_sent_types(c), ["speak"])

    def test_unmapped_topic_never_translated(self):
        c = _client(modernize=True, emit_legacy=True)
        c.emit(Message("some.random.topic", {"x": 1}))
        self.assertEqual(_sent_types(c), ["some.random.topic"])

    def test_both_on_translate_each_direction_once(self):
        c = _client(modernize=True, emit_legacy=True)
        c.emit(Message("speak", {"utterance": "hi"}))          # legacy -> +spec
        c.emit(Message("ovos.utterance.handle", {"u": 1}))     # spec   -> +legacy
        self.assertEqual(_sent_types(c), [
            "speak", "ovos.utterance.speak",
            "ovos.utterance.handle", "recognizer_loop:utterance",
        ])


class TestNoListenerSideMagic(unittest.TestCase):
    def test_on_is_plain_subscribe(self):
        c = _client(modernize=True, emit_legacy=True)
        handler = MagicMock()
        c.on("speak", handler)
        c.emitter.on.assert_called_once_with("speak", handler)

    def test_no_migration_attributes(self):
        c = _client()
        self.assertFalse(hasattr(c, "_migration_wrapper"))
        self.assertFalse(hasattr(c, "_migration_handlers"))


if __name__ == "__main__":
    unittest.main()
