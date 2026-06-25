"""Tests for ovos_bus_client.util.migration — namespace-migration helpers."""
import unittest
from unittest.mock import MagicMock

from ovos_bus_client.message import Message
from ovos_bus_client.util.migration import (
    TransitionalDeduplicator,
    utterance_key,
    emit_migration_pair,
)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class TestTransitionalDeduplicator(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.dedup = TransitionalDeduplicator(window=1.0, clock=self.clock)

    def test_first_occurrence_is_not_duplicate(self):
        self.assertFalse(self.dedup.is_duplicate("k"))

    def test_second_within_window_is_duplicate(self):
        self.assertFalse(self.dedup.is_duplicate("k"))
        self.assertTrue(self.dedup.is_duplicate("k"))

    def test_repeat_after_window_is_not_duplicate(self):
        self.assertFalse(self.dedup.is_duplicate("k"))
        self.clock.advance(1.5)
        self.assertFalse(self.dedup.is_duplicate("k"))

    def test_distinct_keys_independent(self):
        self.assertFalse(self.dedup.is_duplicate("a"))
        self.assertFalse(self.dedup.is_duplicate("b"))
        self.assertTrue(self.dedup.is_duplicate("a"))
        self.assertTrue(self.dedup.is_duplicate("b"))

    def test_none_key_never_duplicate(self):
        self.assertFalse(self.dedup.is_duplicate(None))
        self.assertFalse(self.dedup.is_duplicate(None))

    def test_reset_forgets_keys(self):
        self.dedup.is_duplicate("k")
        self.dedup.reset()
        self.assertFalse(self.dedup.is_duplicate("k"))

    def test_max_keys_bounds_memory(self):
        d = TransitionalDeduplicator(window=1e9, max_keys=3, clock=self.clock)
        for i in range(10):
            d.is_duplicate(f"k{i}")
        self.assertLessEqual(len(d._seen), 3)

    def test_purge_drops_only_expired(self):
        self.dedup.is_duplicate("old")
        self.clock.advance(0.5)
        self.dedup.is_duplicate("new")
        self.clock.advance(0.6)  # old now > 1.0s, new at 0.6s
        self.assertFalse(self.dedup.is_duplicate("old"))  # expired -> fresh
        self.assertTrue(self.dedup.is_duplicate("new"))   # still within window


class TestUtteranceKey(unittest.TestCase):
    def test_string_and_list_same_text_match(self):
        self.assertEqual(utterance_key("hello world", "en-us"),
                         utterance_key(["hello world"], "en-us"))

    def test_lang_distinguishes(self):
        self.assertNotEqual(utterance_key("hi", "en-us"),
                            utterance_key("hi", "pt-pt"))

    def test_empty_returns_none(self):
        self.assertIsNone(utterance_key(None))
        self.assertIsNone(utterance_key([]))
        self.assertIsNone(utterance_key(""))

    def test_dual_emit_is_deduped_by_key(self):
        """The whole point: same utterance on two topics dedupes to one."""
        dedup = TransitionalDeduplicator(window=1.0, clock=FakeClock())
        legacy = Message("recognizer_loop:utterance",
                         {"utterances": ["turn on the lights"], "lang": "en-us"})
        new = Message("ovos.utterance.handle",
                      {"utterances": ["turn on the lights"], "lang": "en-us"})
        k1 = utterance_key(legacy.data["utterances"], legacy.data["lang"])
        k2 = utterance_key(new.data["utterances"], new.data["lang"])
        self.assertFalse(dedup.is_duplicate(k1))
        self.assertTrue(dedup.is_duplicate(k2))


class TestEmitMigrationPair(unittest.TestCase):
    def test_emits_both_topics_with_data(self):
        bus = MagicMock()
        src = Message("source", context={"session": {"session_id": "s1"}})
        emit_migration_pair(bus, src, "speak", "ovos.utterance.speak",
                            {"utterance": "hi", "lang": "en-us"})
        types = [c.args[0].msg_type for c in bus.emit.call_args_list]
        self.assertEqual(types, ["speak", "ovos.utterance.speak"])
        # context is forwarded (session preserved) on both
        for c in bus.emit.call_args_list:
            self.assertEqual(c.args[0].context.get("session", {}).get("session_id"), "s1")
            self.assertEqual(c.args[0].data["utterance"], "hi")

    def test_data_defaults_to_message_data(self):
        # forward() defaults payload to {}; the helper must reuse message.data
        bus = MagicMock()
        src = Message("recognizer_loop:utterance",
                      {"utterances": ["hello"], "lang": "en-us"})
        emit_migration_pair(bus, src, "recognizer_loop:utterance",
                            "ovos.utterance.handle")
        for c in bus.emit.call_args_list:
            self.assertEqual(c.args[0].data, {"utterances": ["hello"], "lang": "en-us"})


if __name__ == "__main__":
    unittest.main()
