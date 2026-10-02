"""Regression test for review round 3 of ovos-bus-client#364.

``_canonical_key`` matches an adapt ``entity_type`` against the munged
spelling of every private key in the store. Two owner ids that munge to
the same alnum form (``tea.skill`` and ``tea_skill``) each holding an
ordinary private key (``person``) collide on that spelling. Picking
either by insertion order lets a legacy write meant for one owner
silently mutate the other owner's private entry. ``_canonical_key`` must
refuse to pick a winner in that case and fall back to the bare key
instead, leaving both owners' private entries untouched.
"""
import unittest
from unittest.mock import patch

from ovos_bus_client.session import Session


def _adapt_entity(value, entity_type, confidence=1.0):
    return {"key": value,
            "data": [(value, entity_type)],
            "confidence": confidence,
            "origin": entity_type}


class TestCanonicalKeyRefusesPrivatePrivateCollision(unittest.TestCase):
    def setUp(self):
        self.session = Session("ctx-review364-r3-test")
        self.session.intent_context = None
        self.session.set_intent_context("person", "Bob", scope="private",
                                         owner_id="tea.skill")
        self.session.set_intent_context("person", "Eve", scope="private",
                                         owner_id="tea_skill")

    def test_canonical_key_returns_bare_key_on_owner_collision(self):
        # both private entries munge to the same adapt spelling; there is
        # no bare "tea_skillperson" entry yet, so the lookup returns None
        # rather than picking either owner's private entry
        key = self.session.context._canonical_key("tea_skillperson")
        self.assertIsNone(key)

    def test_canonical_key_logs_one_warning_naming_both_owners(self):
        with patch("ovos_bus_client.session.LOG.warning") as mock_warning:
            self.session.context._canonical_key("tea_skillperson")
        self.assertEqual(mock_warning.call_count, 1)
        logged = " ".join(str(a) for a in mock_warning.call_args.args)
        self.assertIn("tea.skill", logged)
        self.assertIn("tea_skill", logged)

    def test_legacy_inject_does_not_overwrite_either_private_entry(self):
        self.session.context.inject_context(
            _adapt_entity("Carol", "tea_skillperson"))

        self.assertEqual(
            self.session.intent_context["tea.skill:person"]["value"], "Bob")
        self.assertEqual(
            self.session.intent_context["tea_skill:person"]["value"], "Eve")
        self.assertEqual(
            self.session.intent_context["tea_skillperson"]["value"], "Carol")


if __name__ == "__main__":
    unittest.main()
