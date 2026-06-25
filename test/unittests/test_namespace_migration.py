"""Tests for the opt-in transparent legacy<->ovos.* namespace migration in
MessageBusClient (emit dual-send, on dual-listen + dedup, remove cleanup)."""
import json
import unittest
from threading import Event
from unittest.mock import MagicMock, patch

from ovos_bus_client.client.client import (
    MessageBusClient,
    _namespace_migration_enabled,
)
from ovos_bus_client.message import Message


def _client(migration=True):
    """A MessageBusClient with the migration machinery, no real websocket."""
    c = MessageBusClient.__new__(MessageBusClient)
    c.emitter = MagicMock()
    c.client = MagicMock()
    c._namespace_migration = migration
    c._migration_window = 1.0
    c._migration_handlers = {}
    c.wrapped_funcs = {}
    c.connected_event = Event()
    c.connected_event.set()
    c.started_running = True
    c.session_id = "default"
    return c


class TestFlagReading(unittest.TestCase):
    def test_env_enables(self):
        with patch.dict("os.environ", {"OVOS_BUS_NAMESPACE_MIGRATION": "true"}):
            self.assertTrue(_namespace_migration_enabled())

    def test_env_disables(self):
        with patch.dict("os.environ", {"OVOS_BUS_NAMESPACE_MIGRATION": "0"}):
            self.assertFalse(_namespace_migration_enabled())


class TestEmitDualSend(unittest.TestCase):
    def _sent_types(self, c):
        return [json.loads(call.args[0])["type"]
                for call in c.client.send.call_args_list]

    def test_legacy_emit_also_sends_spec(self):
        c = _client(migration=True)
        c.emit(Message("speak", {"utterance": "hi", "lang": "en-us"}))
        self.assertEqual(self._sent_types(c), ["speak", "ovos.utterance.speak"])

    def test_spec_emit_also_sends_legacy(self):
        c = _client(migration=True)
        c.emit(Message("ovos.utterance.handle", {"utterances": ["hi"]}))
        self.assertEqual(self._sent_types(c),
                         ["ovos.utterance.handle", "recognizer_loop:utterance"])

    def test_unmapped_topic_sends_once(self):
        c = _client(migration=True)
        c.emit(Message("some.random.topic", {"x": 1}))
        self.assertEqual(self._sent_types(c), ["some.random.topic"])

    def test_migration_off_sends_once(self):
        c = _client(migration=False)
        c.emit(Message("speak", {"utterance": "hi"}))
        self.assertEqual(self._sent_types(c), ["speak"])


class TestOnDualListenAndDedup(unittest.TestCase):
    def test_on_registers_both_namespaces(self):
        c = _client(migration=True)
        handler = MagicMock()
        c.on("speak", handler)
        topics = {call.args[0] for call in c.emitter.on.call_args_list}
        self.assertEqual(topics, {"speak", "ovos.utterance.speak"})
        # same wrapped function on both
        wrappers = {id(call.args[1]) for call in c.emitter.on.call_args_list}
        self.assertEqual(len(wrappers), 1)

    def test_on_unmapped_topic_registers_plain(self):
        c = _client(migration=True)
        handler = MagicMock()
        c.on("some.topic", handler)
        c.emitter.on.assert_called_once_with("some.topic", handler)

    def test_wrapper_dedupes_dual_copies(self):
        c = _client(migration=True)
        handler = MagicMock()
        wrapper = c._migration_wrapper(handler)
        legacy = Message("speak", {"utterance": "hi", "lang": "en-us"})
        spec = Message("ovos.utterance.speak", {"utterance": "hi", "lang": "en-us"})
        wrapper(legacy)
        wrapper(spec)  # same content, canonical topic -> deduped
        handler.assert_called_once()

    def test_wrapper_distinct_content_both_fire(self):
        c = _client(migration=True)
        handler = MagicMock()
        wrapper = c._migration_wrapper(handler)
        wrapper(Message("speak", {"utterance": "one"}))
        wrapper(Message("speak", {"utterance": "two"}))
        self.assertEqual(handler.call_count, 2)

    def test_wrapper_expires_after_window(self):
        c = _client(migration=True)
        c._migration_window = 1.0
        handler = MagicMock()
        wrapper = c._migration_wrapper(handler)
        with patch("ovos_bus_client.client.client.time.monotonic") as clk:
            clk.return_value = 0.0
            wrapper(Message("speak", {"utterance": "hi"}))
            clk.return_value = 2.0
            wrapper(Message("speak", {"utterance": "hi"}))
        self.assertEqual(handler.call_count, 2)


class TestRemoveCleansBoth(unittest.TestCase):
    def test_remove_unsubscribes_both_namespaces(self):
        c = _client(migration=True)
        c._remove_normal = MagicMock()
        handler = MagicMock()
        c.on("speak", handler)
        c.remove("speak", handler)
        removed = {call.args[0] for call in c._remove_normal.call_args_list}
        self.assertEqual(removed, {"speak", "ovos.utterance.speak"})
        self.assertEqual(c._migration_handlers, {})


if __name__ == "__main__":
    unittest.main()
