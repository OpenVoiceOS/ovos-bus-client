"""Tests for the canonical OVOS-CONTEXT-1 intent-context mutators.

``Session.set_intent_context`` / ``remove_intent_context`` /
``clear_intent_context`` are the ONE canonical write path into the flat §2
``intent_context`` map. They resolve keys per §3.1 (private vs shared), mutate
the map in place (preserving Session + map identity), record removals as §5.3
null tombstones so deletions propagate in sync payloads, and every write is
reflected both in the canonical map and in the derived legacy ``context``
frame-stack view.
"""
import unittest

from ovos_spec_tools.context import gate_satisfied, resolve_key


class TestIntentContextMutators(unittest.TestCase):
    def setUp(self):
        from ovos_bus_client.session import Session
        self.session = Session("ctx-mutator-test")

    # --- set: private vs shared scope resolution (§3.1) ------------------
    def test_set_private_resolves_owner_prefixed_key(self):
        self.session.set_intent_context("topic", "weather",
                                        scope="private", owner_id="skill.a")
        self.assertIn("skill.a:topic", self.session.intent_context)
        self.assertEqual(self.session.intent_context["skill.a:topic"]["value"],
                         "weather")
        # a shared gate on the same name is NOT satisfied by the private entry
        self.assertFalse(gate_satisfied(
            self.session.intent_context,
            requires=[{"key": "topic", "scope": "shared"}],
            excludes=None, owner_id="skill.a"))
        # the private gate IS satisfied
        self.assertTrue(gate_satisfied(
            self.session.intent_context,
            requires=[{"key": "topic", "scope": "private"}],
            excludes=None, owner_id="skill.a"))

    def test_set_shared_resolves_bare_key(self):
        self.session.set_intent_context("person", "Bob", scope="shared")
        self.assertIn("person", self.session.intent_context)
        self.assertEqual(self.session.intent_context["person"]["value"], "Bob")

    def test_set_private_without_owner_raises(self):
        # a private write cannot resolve a stored key without an owner; a
        # silently dropped context write would be a debugging trap
        with self.assertRaises(ValueError):
            self.session.set_intent_context("topic", "x", scope="private",
                                            owner_id=None)
        self.assertFalse(self.session.intent_context)

    def test_set_records_optional_decay_fields_only_when_given(self):
        self.session.set_intent_context("a", 1, scope="shared")
        self.assertEqual(set(self.session.intent_context["a"]), {"value"})
        self.session.set_intent_context("b", 2, scope="shared",
                                        expires_at=123.0, turns_remaining=3)
        self.assertEqual(self.session.intent_context["b"],
                         {"value": 2, "expires_at": 123.0,
                          "turns_remaining": 3})

    def test_set_replaces_existing_entry(self):
        self.session.set_intent_context("a", 1, scope="shared")
        self.session.set_intent_context("a", 2, scope="shared")
        self.assertEqual(self.session.intent_context["a"], {"value": 2})

    # --- remove ----------------------------------------------------------
    def test_remove_tombstones_resolved_key(self):
        # removal writes a §5.3 null tombstone rather than popping, so the
        # deletion stays visible in the map this session serializes and the
        # receiving merge deletes the key; a tombstone is never live
        self.session.set_intent_context("topic", "x", owner_id="skill.a")
        self.session.remove_intent_context("topic", owner_id="skill.a")
        self.assertIsNone(self.session.intent_context["skill.a:topic"])
        self.assertFalse(gate_satisfied(
            self.session.intent_context,
            requires=[{"key": "topic", "scope": "private"}],
            excludes=None, owner_id="skill.a"))

    def test_remove_wrong_scope_leaves_entry(self):
        self.session.set_intent_context("topic", "x", owner_id="skill.a")
        # a shared removal must not touch the private entry
        self.session.remove_intent_context("topic", scope="shared")
        self.assertEqual(self.session.intent_context["skill.a:topic"],
                         {"value": "x"})

    def test_remove_missing_key_is_noop(self):
        self.session.remove_intent_context("nope", scope="shared")
        self.assertFalse(self.session.intent_context)

    def test_remove_propagates_over_sync_payload(self):
        """remove + serialize + §5.3 merge deletes the entry at the receiver."""
        from ovos_bus_client.session import SessionManager
        self.session.set_intent_context("person", "Bob", scope="shared")
        self.session.remove_intent_context("person", scope="shared")
        payload = self.session.serialize().get("intent_context")
        # the deletion rides the payload as a null entry (§5.3), never absent
        self.assertEqual(payload, {"person": None})
        receiver = {"person": {"value": "Bob"}, "room": {"value": "kitchen"}}
        SessionManager.merge_intent_context(receiver, payload)
        self.assertEqual(receiver, {"room": {"value": "kitchen"}})

    # --- clear -----------------------------------------------------------
    def test_clear_tombstones_map_in_place(self):
        self.session.set_intent_context("a", 1, scope="shared")
        self.session.set_intent_context("b", 2, scope="shared")
        before = self.session.intent_context
        self.session.clear_intent_context()
        # every entry tombstoned IN PLACE — same object, never None
        self.assertEqual(self.session.intent_context, {"a": None, "b": None})
        self.assertIs(self.session.intent_context, before)
        # nothing live remains for gating or the legacy projection
        self.assertFalse(gate_satisfied(
            self.session.intent_context,
            requires=[{"key": "a", "scope": "shared"}],
            excludes=None, owner_id="x"))
        self.assertEqual(self.session.context.frame_stack, [])

    # --- identity / mutate-in-place --------------------------------------
    def test_mutators_preserve_map_object_identity(self):
        # once the map exists, every further mutation is in place: the same
        # dict object (and Session) is kept, never replaced.
        self.session.set_intent_context("a", 1, scope="shared")
        original_map = self.session.intent_context
        self.session.set_intent_context("b", 2, scope="shared")
        self.session.remove_intent_context("b", scope="shared")
        self.session.clear_intent_context()
        self.assertIs(self.session.intent_context, original_map)

    def test_set_touches_session(self):
        from unittest.mock import patch
        with patch.object(type(self.session), "touch") as touch:
            self.session.set_intent_context("a", 1, scope="shared")
        touch.assert_called_once()

    # --- derived legacy view reflects canonical writes -------------------
    def test_shared_set_visible_in_legacy_context_view(self):
        self.session.set_intent_context("person", "Bob", scope="shared")
        # the adapt-style frame stack projects taggable shared string entries
        frames = self.session.context.frame_stack
        found = any(ent.get("origin") == "person"
                    for frame, _ in frames for ent in frame.entities)
        self.assertTrue(found)

    def test_roundtrip_set_remove_clear_reflected_both_ways(self):
        self.session.set_intent_context("person", "Bob", scope="shared")
        self.assertEqual(resolve_key("person", "shared", None), "person")
        self.assertEqual(self.session.intent_context["person"],
                         {"value": "Bob"})
        self.session.remove_intent_context("person", scope="shared")
        self.assertIsNone(self.session.intent_context["person"])
        self.assertEqual(self.session.context.frame_stack, [])
        self.session.set_intent_context("x", 1, scope="shared")
        self.session.clear_intent_context()
        self.assertEqual(self.session.context.frame_stack, [])


if __name__ == "__main__":
    unittest.main()
