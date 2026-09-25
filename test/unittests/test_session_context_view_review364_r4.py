"""Regression test for review round 4 of ovos-bus-client#364.

``_canonical_key`` matches an adapt ``entity_type`` against the munged
spelling of every private key in the store, but it read the raw key list
without the liveness test ``frame_stack`` applies: a tombstoned entry
(``value: null``, written by :meth:`Session.remove_intent_context`) or an
expired entry (``expires_at`` in the past) still counted as a "match", so
it could block the resolution of a live private entry or a live bare key,
and could still be named in the owner-collision warning. Dead entries
never take part in resolution.
"""
import time
import unittest
from unittest.mock import patch

from ovos_bus_client.session import Session


class TestCanonicalKeySkipsDeadEntries(unittest.TestCase):
    def setUp(self):
        self.session = Session("ctx-review364-r4-test")
        self.session.intent_context = None

    def test_removed_private_entry_does_not_block_live_owner(self):
        # owner A writes then removes private "person"; owner B (which
        # munges to the same adapt spelling) writes a live private "person"
        self.session.set_intent_context("person", "Alice", scope="private",
                                         owner_id="tea.skill")
        self.session.remove_intent_context("person", scope="private",
                                           owner_id="tea.skill")
        self.session.set_intent_context("person", "Bob", scope="private",
                                         owner_id="tea_skill")

        key = self.session.context._canonical_key("tea_skillperson")
        self.assertEqual(key, "tea_skill:person")

    def test_expired_private_entry_does_not_block_live_owner(self):
        # owner A's private "person" already expired; owner B's is live
        self.session.set_intent_context("person", "Alice", scope="private",
                                         owner_id="tea.skill",
                                         expires_at=time.time() - 1)
        self.session.set_intent_context("person", "Bob", scope="private",
                                         owner_id="tea_skill")

        key = self.session.context._canonical_key("tea_skillperson")
        self.assertEqual(key, "tea_skill:person")

    def test_removed_entry_omitted_from_collision_warning(self):
        # three owners share the adapt spelling; one has been removed, so
        # the warning must name only the two still-live owners
        self.session.set_intent_context("person", "Alice", scope="private",
                                         owner_id="tea.skill")
        self.session.set_intent_context("person", "Bob", scope="private",
                                         owner_id="tea_skill")
        self.session.set_intent_context("person", "Carol", scope="private",
                                         owner_id="tea-skill")
        self.session.remove_intent_context("person", scope="private",
                                           owner_id="tea.skill")

        with patch("ovos_bus_client.session.LOG.warning") as mock_warning:
            key = self.session.context._canonical_key("tea_skillperson")

        self.assertIsNone(key)
        self.assertEqual(mock_warning.call_count, 1)
        logged = " ".join(str(a) for a in mock_warning.call_args.args)
        self.assertNotIn("tea.skill", logged)
        self.assertIn("tea_skill", logged)
        self.assertIn("tea-skill", logged)


if __name__ == "__main__":
    unittest.main()
