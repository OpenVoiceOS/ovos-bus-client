"""Regression tests for review round 2 of ovos-bus-client#364.

CONFIRMED-1: ``_canonical_key`` must prefer the private key when a private
key projects to the adapt spelling of a bare, shared entry (OVOS-CONTEXT-1
§3 at architecture ab495cb: a stored key is read as its shape says "however
the writer derived it").

CONFIRMED-4: the frame-stack shadow rule must drop a bare twin only when its
value equals the private entry's value. When a legacy write later changes
the bare, shared entry to a different value, that shared entry is a real
entry under §3 and must keep its own frame rather than being hidden behind
the stale private one.
"""
import unittest

from ovos_bus_client.session import Session


def _adapt_entity(value, entity_type, confidence=1.0):
    return {"key": value,
            "data": [(value, entity_type)],
            "confidence": confidence,
            "origin": entity_type}


class TestCanonicalKeyPrefersPrivate(unittest.TestCase):
    def setUp(self):
        self.session = Session("ctx-review364-test")
        self.session.intent_context = None

    def test_canonical_key_resolves_to_private_entry_not_bare_twin(self):
        self.session.set_intent_context("person", "Bob", scope="private",
                                        owner_id="tea.skill")
        # a bare, shared entry that happens to project to the same adapt
        # spelling as the private entry above
        self.session.intent_context["tea_skillperson"] = {"value": "Alice"}

        key = self.session.context._canonical_key("tea_skillperson")
        self.assertEqual(key, "tea.skill:person")

    def test_legacy_inject_writes_the_private_entry_not_the_bare_twin(self):
        self.session.set_intent_context("person", "Bob", scope="private",
                                        owner_id="tea.skill")
        self.session.intent_context["tea_skillperson"] = {"value": "Alice"}

        self.session.context.inject_context(
            _adapt_entity("Carol", "tea_skillperson"))

        self.assertEqual(
            self.session.intent_context["tea.skill:person"]["value"],
            "Carol")

    def test_legacy_remove_clears_the_private_entry(self):
        # removal must tombstone the PRIVATE entry the spelling resolves
        # to, not a bare twin of the same spelling
        self.session.set_intent_context("person", "Bob", scope="private",
                                        owner_id="tea.skill")
        self.session.intent_context["tea_skillperson"] = {"value": "Bob"}

        self.session.context.remove_context("tea_skillperson")

        self.assertIsNone(self.session.intent_context["tea.skill:person"])


class TestShadowRuleKeepsDivergentSharedFrame(unittest.TestCase):
    def setUp(self):
        self.session = Session("ctx-review364-shadow-test")
        self.session.intent_context = None

    def test_equal_values_still_collapse_to_one_frame(self):
        self.session.set_intent_context("person", "Bob", scope="private",
                                        owner_id="tea.skill")
        self.session.intent_context["tea_skillperson"] = {"value": "Bob"}

        frames = self.session.context.frame_stack
        self.assertEqual(len(frames), 1)

    def test_divergent_shared_value_keeps_both_frames(self):
        self.session.set_intent_context("person", "Bob", scope="private",
                                        owner_id="tea.skill")
        self.session.intent_context["tea_skillperson"] = {"value": "Alice"}

        frames = self.session.context.frame_stack
        values = sorted(
            frame.entities[0]["key"] for frame, _ts in frames)
        self.assertEqual(values, ["Alice", "Bob"])


if __name__ == "__main__":
    unittest.main()
