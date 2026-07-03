"""Unified-store tests for the legacy ``Session.context`` back-compat view.

These assert the single-source-of-truth invariant: the legacy adapt-style
``IntentContextManager`` frame-stack API and the canonical OVOS-CONTEXT-1
``session.intent_context`` flat map are two views over ONE store. A write
through either path is visible through the other and gates via CONTEXT-1.
"""
import time
import unittest
from unittest.mock import patch

from ovos_spec_tools.context import gate_satisfied


def _adapt_entity(value, entity_type, confidence=1.0):
    """Build an entity in the shape the adapt engine feeds/reads."""
    return {"key": value,
            "data": [(value, entity_type)],
            "confidence": confidence,
            "origin": entity_type}


class TestContextUnifiedStore(unittest.TestCase):
    def setUp(self):
        from ovos_bus_client.session import Session
        self.session = Session("ctx-view-test")
        self.session.intent_context = None

    # (a) legacy write -> canonical read + CONTEXT-1 gating
    def test_legacy_inject_visible_in_intent_context_and_gates(self):
        self.session.context.inject_context(_adapt_entity("Bob", "person"))

        # visible in the canonical flat map as a CONTEXT-1 entry
        self.assertIn("person", self.session.intent_context)
        entry = self.session.intent_context["person"]
        self.assertEqual(entry["value"], "Bob")

        # gates via CONTEXT-1 (shared scope, bare key)
        self.assertTrue(gate_satisfied(self.session.intent_context,
                                       requires=[{"key": "person",
                                                  "scope": "shared"}],
                                       excludes=None, owner_id="some.skill"))

    # (b) canonical write -> legacy adapt get_context read
    def test_canonical_write_visible_through_legacy_get_context(self):
        self.session.intent_context = {
            "person": {"value": "Bob", "turns_remaining": 3}}
        ctx = self.session.context.get_context()
        # adapt reads (value, entity_type) out of data[0]
        pairs = [e["data"][0] for e in ctx]
        self.assertIn(("Bob", "person"), pairs)

    def test_legacy_remove_context_deletes_canonical_entry(self):
        self.session.intent_context = {"person": {"value": "Bob"}}
        self.session.context.remove_context("person")
        self.assertNotIn("person", self.session.intent_context or {})

    def test_legacy_clear_context_empties_canonical_store(self):
        self.session.intent_context = {"person": {"value": "Bob"},
                                       "room": {"value": "kitchen"}}
        self.session.context.clear_context()
        self.assertFalse(self.session.intent_context)

    def test_update_context_greedy_writes_canonical(self):
        # greedy mode injects every scanned entity
        view = self.session.context
        view.context_greedy = True
        view.update_context([_adapt_entity("kitchen", "room")])
        self.assertEqual(self.session.intent_context["room"]["value"], "kitchen")

    def test_dead_entry_not_projected_to_frame_stack(self):
        self.session.intent_context = {
            "person": {"value": "Bob", "expires_at": time.time() - 10}}
        ctx = self.session.context.get_context()
        self.assertEqual(ctx, [])


class TestContextWarnOnAccess(unittest.TestCase):
    def setUp(self):
        from ovos_bus_client.session import Session
        self.session = Session("ctx-warn-test")

    def test_context_getter_warns(self):
        with patch("ovos_bus_client.session.log_deprecation") as dep:
            _ = self.session.context
        self.assertTrue(dep.called)

    def test_active_skills_getter_warns(self):
        with patch("ovos_bus_client.session.log_deprecation") as dep:
            _ = self.session.active_skills
        self.assertTrue(dep.called)

    def test_active_skills_setter_warns(self):
        with patch("ovos_bus_client.session.log_deprecation") as dep:
            self.session.active_skills = [["s", time.time()]]
        self.assertTrue(dep.called)

    def test_utterance_states_getter_warns(self):
        with patch("ovos_bus_client.session.log_deprecation") as dep:
            _ = self.session.utterance_states
        self.assertTrue(dep.called)

    def test_utterance_states_mutation_warns(self):
        from ovos_bus_client.session import UtteranceState
        with patch("ovos_bus_client.session.log_deprecation") as dep:
            view = self.session.utterance_states
            view["some.skill"] = UtteranceState.RESPONSE.value
        self.assertTrue(dep.called)


class TestSerializeOmissionNotNull(unittest.TestCase):
    def test_serialize_context_derived_from_intent_context(self):
        from ovos_bus_client.session import Session
        session = Session("ctx-ser-test")
        session.intent_context = {"person": {"value": "Bob"}}
        data = session.serialize()
        # the legacy ``context`` wire key is DERIVED from intent_context, not a
        # separate disjoint store: the migrated entry surfaces in its frame_stack
        frames = data["context"]["frame_stack"]
        entity_types = [f[0]["entities"][0]["data"][0][1] for f in frames]
        self.assertIn("person", entity_types)

    def test_serialize_omits_empty_blacklists_and_pipeline(self):
        from ovos_bus_client.session import Session
        session = Session("ctx-omit-test")
        session.blacklisted_skills = []
        session.blacklisted_intents = []
        session.pipeline = []
        data = session.serialize()
        # SESSION-1 §2.1 omission-not-null: empty lists are absent, never []
        self.assertNotIn("blacklisted_skills", data)
        self.assertNotIn("blacklisted_intents", data)
        self.assertNotIn("pipeline", data)


class TestLegacyWireRoundTrip(unittest.TestCase):
    def test_legacy_dict_folds_into_canonical(self):
        """A legacy producer's Session dict deserializes into canonical fields."""
        from ovos_bus_client.session import Session
        now = time.time()
        legacy = {
            "session_id": "legacy-1",
            "lang": "en-us",
            "active_skills": [["legacy.skill", now]],
            "utterance_states": {"legacy.skill": "response"},
            "context": {
                "timeout": 120,
                "frame_stack": [
                    ({"entities": [_adapt_entity("Bob", "person")],
                      "metadata": {}}, now)
                ],
            },
        }
        sess = Session.deserialize(legacy)
        # active_skills -> active_handlers
        self.assertTrue(any(h["skill_id"] == "legacy.skill"
                            for h in sess.active_handlers))
        # utterance_states -> response_mode
        self.assertEqual(sess.response_mode.get("skill_id"), "legacy.skill")
        # legacy context frame_stack -> intent_context, gates via CONTEXT-1
        self.assertEqual(sess.intent_context["person"]["value"], "Bob")
        self.assertTrue(gate_satisfied(sess.intent_context,
                                       requires=[{"key": "person",
                                                  "scope": "shared"}],
                                       excludes=None, owner_id="x"))

    def test_modern_dict_canonical_intact_and_stable(self):
        from ovos_bus_client.session import Session
        modern = {
            "session_id": "modern-1",
            "lang": "en-us",
            "active_handlers": [{"skill_id": "modern.skill",
                                 "activated_at": time.time()}],
            "intent_context": {"person": {"value": "Bob",
                                          "turns_remaining": 2}},
        }
        sess = Session.deserialize(modern)
        self.assertTrue(any(h["skill_id"] == "modern.skill"
                            for h in sess.active_handlers))
        self.assertEqual(sess.intent_context["person"]["value"], "Bob")
        # re-serialize -> deserialize stable
        again = Session.deserialize(sess.serialize())
        self.assertEqual(again.intent_context["person"]["value"], "Bob")

    def test_mixed_dict_canonical_wins(self):
        from ovos_bus_client.session import Session
        now = time.time()
        mixed = {
            "session_id": "mixed-1",
            "active_handlers": [{"skill_id": "canon.skill",
                                 "activated_at": now}],
            "active_skills": [["legacy.skill", now]],
            "intent_context": {"room": {"value": "kitchen"}},
            "context": {"timeout": 120,
                        "frame_stack": [({"entities": [_adapt_entity("Bob",
                                                                     "person")],
                                          "metadata": {}}, now)]},
        }
        sess = Session.deserialize(mixed)
        skills = [h["skill_id"] for h in sess.active_handlers]
        self.assertIn("canon.skill", skills)
        self.assertNotIn("legacy.skill", skills)
        # canonical intent_context wins; legacy context frame_stack ignored
        self.assertIn("room", sess.intent_context)
        self.assertNotIn("person", sess.intent_context)

    def test_serialize_deserialize_idempotent(self):
        from ovos_bus_client.session import Session
        session = Session("idem-1")
        session.intent_context = {"person": {"value": "Bob",
                                             "expires_at": time.time() + 999}}
        a = session.serialize()
        b = Session.deserialize(a).serialize()
        self.assertEqual(a["intent_context"], b["intent_context"])


if __name__ == "__main__":
    unittest.main()
