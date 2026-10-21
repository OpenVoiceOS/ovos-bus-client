"""Regression tests for review round 5 of ovos-bus-client#364.

CONFIRMED-1: ``frame_stack`` builds ``private_values`` from every dict entry,
so an EXPIRED private entry still hides a live bare entry that shares its
adapt spelling and value. ``_is_live_entry`` must gate that loop the same
way it gates the read below.

CONFIRMED-2: ``remove_context`` resolves an adapt spelling to the private
key and tombstones only that key. The hidden bare twin with the same
spelling and value then reappears on the next read. OVOS-CONTEXT-1 §3
holds a stored key is read "however the writer derived it": a removal must
reach every entry the read side would otherwise still surface under that
spelling, not just the one the private lookup resolved to. The
``frame_stack`` setter has the same gap for the origins it prunes.
"""
import unittest

from ovos_bus_client.session import Session


class TestExpiredPrivateEntryDoesNotHideLiveBareTwin(unittest.TestCase):
    def setUp(self):
        self.session = Session("ctx-review364-r5-expired-test")
        self.session.intent_context = None

    def test_expired_private_entry_does_not_mask_live_bare_entry(self):
        self.session.set_intent_context("person", "Bob", scope="private",
                                        owner_id="tea.skill")
        # the private entry expired already, but a live bare entry with the
        # same adapt spelling and value was written afterwards
        self.session.intent_context["tea.skill:person"]["expires_at"] = 0
        self.session.intent_context["tea_skillperson"] = {"value": "Bob"}

        frames = self.session.context.frame_stack
        values = [frame.entities[0]["key"] for frame, _ts in frames]
        self.assertEqual(values, ["Bob"])


class TestRemoveContextClearsHiddenBareTwin(unittest.TestCase):
    def setUp(self):
        self.session = Session("ctx-review364-r5-remove-twin-test")
        self.session.intent_context = None

    def test_remove_after_legacy_twin_leaves_nothing_on_next_read(self):
        self.session.set_intent_context("person", "Bob", scope="private",
                                        owner_id="tea.skill")
        self.session.intent_context["tea_skillperson"] = {"value": "Bob"}

        self.session.context.remove_context("tea_skillperson")

        frames = self.session.context.frame_stack
        self.assertEqual(frames, [])

    def test_frame_stack_setter_clears_hidden_bare_twin(self):
        self.session.set_intent_context("person", "Bob", scope="private",
                                        owner_id="tea.skill")
        self.session.intent_context["tea_skillperson"] = {"value": "Bob"}

        self.session.context.frame_stack = []

        frames = self.session.context.frame_stack
        self.assertEqual(frames, [])


if __name__ == "__main__":
    unittest.main()
