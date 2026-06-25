"""Tests for ovos_bus_client.util.migration.Deduplicator."""
import unittest
from unittest.mock import patch

from ovos_bus_client.util.migration import Deduplicator


class TestDeduplicator(unittest.TestCase):
    @patch("ovos_bus_client.util.migration.time.monotonic")
    def test_basic_dedup_within_window(self, clock):
        clock.return_value = 0.0
        d = Deduplicator(window=1.0)
        self.assertFalse(d.is_duplicate("k"))
        self.assertTrue(d.is_duplicate("k"))

    @patch("ovos_bus_client.util.migration.time.monotonic")
    def test_repeat_after_window_is_fresh(self, clock):
        d = Deduplicator(window=1.0)
        clock.return_value = 0.0
        self.assertFalse(d.is_duplicate("k"))
        clock.return_value = 1.5
        self.assertFalse(d.is_duplicate("k"))

    @patch("ovos_bus_client.util.migration.time.monotonic")
    def test_distinct_keys_independent(self, clock):
        clock.return_value = 0.0
        d = Deduplicator()
        self.assertFalse(d.is_duplicate("a"))
        self.assertFalse(d.is_duplicate("b"))
        self.assertTrue(d.is_duplicate("a"))

    @patch("ovos_bus_client.util.migration.time.monotonic")
    def test_expired_keys_are_purged(self, clock):
        d = Deduplicator(window=1.0)
        clock.return_value = 0.0
        d.is_duplicate("old")
        clock.return_value = 2.0
        d.is_duplicate("new")
        self.assertEqual(set(d._seen), {"new"})  # bounded to in-window keys


if __name__ == "__main__":
    unittest.main()
