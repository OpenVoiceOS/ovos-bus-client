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
    def test_legacy_inject_refreshes_the_entry_it_names(self):
        # the adapt entity_type names the stored private entry through its
        # munged spelling, and the write lands on that entry
        self.session.set_intent_context("person", "Bob", scope="private",
                                        owner_id="tea.skill")
        self.session.context.inject_context(
            _adapt_entity("Alice", "tea_skillperson"))

        entry = self.session.intent_context["tea.skill:person"]
        self.assertEqual(entry["value"], "Alice")
        self.assertNotIn("tea_skillperson", self.session.intent_context)

        # gates via CONTEXT-1 as the PRIVATE entry it is, for its owner only
        self.assertTrue(gate_satisfied(self.session.intent_context,
                                       requires=[{"key": "person",
                                                  "scope": "private"}],
                                       excludes=None, owner_id="tea.skill"))
        self.assertFalse(gate_satisfied(self.session.intent_context,
                                        requires=[{"key": "person",
                                                   "scope": "shared"}],
                                        excludes=None, owner_id="other.skill"))

    def test_legacy_inject_still_mints_a_bare_key(self):
        # the write-side half is held back until the read side below carries a
        # version floor: a writer that stops minting, read by a peer that does
        # not recompute the spelling, breaks adapt tagging silently
        self.session.context.inject_context(_adapt_entity("Bob", "person"))
        self.assertEqual(self.session.intent_context["person"]["value"], "Bob")

    def test_legacy_inject_refreshes_a_live_shared_entry(self):
        # a bare key an ecosystem agreed on (§2) already fixes the scope, so
        # the adapt write refreshes it instead of being dropped
        self.session.set_intent_context("person", "Bob", scope="shared")
        self.session.context.inject_context(_adapt_entity("Alice", "person"))
        self.assertEqual(self.session.intent_context["person"]["value"],
                         "Alice")

    # (b) canonical write -> legacy adapt get_context read
    def test_canonical_write_visible_through_legacy_get_context(self):
        self.session.intent_context = {
            "person": {"value": "Bob", "turns_remaining": 3}}
        ctx = self.session.context.get_context()
        # adapt reads (value, entity_type) out of data[0]
        pairs = [e["data"][0] for e in ctx]
        self.assertIn(("Bob", "person"), pairs)

    def test_legacy_remove_context_tombstones_canonical_entry(self):
        self.session.intent_context = {"person": {"value": "Bob"}}
        self.session.context.remove_context("person")
        # removed = tombstoned (null entry, CONTEXT-1 §5.3), so the deletion
        # propagates in the sync payload; a tombstone is never live
        self.assertIsNone(self.session.intent_context["person"])
        self.assertFalse(gate_satisfied(self.session.intent_context,
                                        requires=[{"key": "person",
                                                   "scope": "shared"}],
                                        excludes=None, owner_id="x"))
        self.assertEqual(self.session.context.get_context(), [])

    def test_legacy_remove_missing_key_is_noop(self):
        self.session.intent_context = {"person": {"value": "Bob"}}
        self.session.context.remove_context("nope")
        self.assertNotIn("nope", self.session.intent_context)

    def test_legacy_clear_context_tombstones_canonical_store(self):
        self.session.intent_context = {"person": {"value": "Bob"},
                                       "room": {"value": "kitchen"}}
        self.session.context.clear_context()
        # every entry becomes a §5.3 tombstone: dead for gating/projection,
        # but the deletion is carried in the serialized map
        self.assertEqual(self.session.intent_context,
                         {"person": None, "room": None})
        self.assertEqual(self.session.context.frame_stack, [])

    def test_frame_stack_assignment_replaces(self):
        # legacy callers prune the stack by assigning a filtered list, so
        # assignment must carry removal semantics for the projected keys
        view = self.session.context
        self.session.set_intent_context("person", "Bob", scope="shared")
        self.session.set_intent_context("room", "kitchen", scope="shared")
        kept = [(frame, ts) for frame, ts in view.frame_stack
                if frame.entities[0]["data"][0][1] == "room"]
        view.frame_stack = kept
        self.assertIsNone(self.session.intent_context["person"])  # tombstoned
        self.assertEqual(self.session.intent_context["room"]["value"],
                         "kitchen")

    def test_frame_stack_assignment_leaves_unprojectable_entries(self):
        # entries the legacy stack cannot represent (null flags, non-string
        # values) are invisible to the view; assignment says nothing about them
        view = self.session.context
        self.session.set_intent_context("person", "Bob", scope="shared")
        self.session.intent_context["skill.a:flag"] = {"value": None}
        view.frame_stack = []
        self.assertIsNone(self.session.intent_context["person"])
        self.assertEqual(self.session.intent_context["skill.a:flag"],
                         {"value": None})

    def test_non_string_value_not_projected_to_frame_stack(self):
        # only a non-null STRING value is a taggable surface form (§7); flags
        # and numeric presence markers gate via intent_context only (§6)
        self.session.intent_context = {"count": {"value": 3},
                                       "flag": {"value": None},
                                       "person": {"value": "Bob"}}
        keys = [frame.entities[0]["origin"]
                for frame, _ in self.session.context.frame_stack]
        self.assertEqual(keys, ["person"])

    def test_update_context_greedy_writes_canonical(self):
        # greedy mode injects every scanned entity, and one that names a live
        # entry through its adapt spelling lands on that entry
        view = self.session.context
        view.context_greedy = True
        self.session.set_intent_context("room", "hall", scope="private",
                                        owner_id="tea.skill")
        view.update_context([_adapt_entity("kitchen", "tea_skillroom")])
        self.assertEqual(self.session.intent_context["tea.skill:room"]["value"],
                         "kitchen")
        self.assertNotIn("tea_skillroom", self.session.intent_context)

    def test_dead_entry_not_projected_to_frame_stack(self):
        self.session.intent_context = {
            "person": {"value": "Bob", "expires_at": time.time() - 10}}
        ctx = self.session.context.get_context()
        self.assertEqual(ctx, [])


class TestAdaptSpellingProjection(unittest.TestCase):
    """The read side recomputes the munged adapt spelling from the key.

    A skill registers its adapt keyword as ``alphanumeric_skill_id + name``
    (ovos-workshop), so the private OVOS-CONTEXT-1 entry ``tea.skill:person``
    is the entry behind the adapt ``entity_type`` ``tea_skillperson``.
    """

    def setUp(self):
        from ovos_bus_client.session import Session
        self.session = Session("ctx-spelling-test")
        self.session.intent_context = None

    def test_private_entry_reads_back_under_the_munged_spelling(self):
        self.session.set_intent_context("person", "Bob", scope="private",
                                        owner_id="tea.skill")
        pairs = [e["data"][0] for e in self.session.context.get_context()]
        self.assertIn(("Bob", "tea_skillperson"), pairs)

    def test_origin_keeps_the_canonical_key(self):
        # the tombstone the frame-stack setter writes reaches the entry, not
        # its adapt spelling
        self.session.set_intent_context("person", "Bob", scope="private",
                                        owner_id="tea.skill")
        self.session.context.frame_stack = []
        self.assertEqual(self.session.intent_context,
                         {"tea.skill:person": None})

    def test_munged_sub_key_is_not_prefixed_twice(self):
        # an emitter that predates CONTEXT-1 sends the munged spelling as the
        # key itself; prefixing again would name a keyword no skill registered
        self.session.set_intent_context("tea_skillperson", "Bob",
                                        scope="private", owner_id="tea.skill")
        pairs = [e["data"][0] for e in self.session.context.get_context()]
        self.assertIn(("Bob", "tea_skillperson"), pairs)

    def test_bare_key_is_its_own_adapt_spelling(self):
        self.session.set_intent_context("person", "Bob", scope="shared")
        pairs = [e["data"][0] for e in self.session.context.get_context()]
        self.assertIn(("Bob", "person"), pairs)

    def test_remove_context_by_munged_spelling_tombstones_the_entry(self):
        self.session.set_intent_context("person", "Bob", scope="private",
                                        owner_id="tea.skill")
        self.session.context.remove_context("tea_skillperson")
        self.assertIsNone(self.session.intent_context["tea.skill:person"])

    def test_modern_writer_to_old_reader_sends_the_munged_spelling(self):
        # the quadrant that fails silently: an old peer reads the legacy
        # ``context`` wire key and tags on the registered keyword name
        self.session.set_intent_context("person", "Bob", scope="private",
                                        owner_id="tea.skill")
        frames = self.session.serialize()["context"]["frame_stack"]
        entity_types = [f[0]["entities"][0]["data"][0][1] for f in frames]
        self.assertEqual(entity_types, ["tea_skillperson"])

    def test_transitional_duplicate_collapses_to_one_frame(self):
        # an old writer stores the munged bare key BESIDE the private entry;
        # both project to the registered spelling, and one turn gives one frame
        self.session.set_intent_context("person", "Bob", scope="private",
                                        owner_id="tea.skill")
        self.session.set_intent_context("tea_skillperson", "Bob",
                                        scope="shared")
        types = [f.entities[0]["data"][0][1]
                 for f, _ in self.session.context.frame_stack]
        self.assertEqual(types, ["tea_skillperson"])
        origins = [f.entities[0]["origin"]
                   for f, _ in self.session.context.frame_stack]
        self.assertEqual(origins, ["tea.skill:person"])

    def test_old_writer_to_modern_reader_keeps_the_spelling(self):
        # an old peer's whole context store arrives on the legacy wire key and
        # exists nowhere else, so it is folded rather than dropped, and reads
        # back under the same spelling the old peer tagged with
        from ovos_bus_client.session import Session
        now = time.time()
        sess = Session.deserialize({
            "session_id": "old-writer-1",
            "context": {"timeout": 120,
                        "frame_stack": [
                            ({"entities": [_adapt_entity("Bob",
                                                         "tea_skillperson")],
                              "metadata": {}}, now)]}})
        self.assertIn("tea_skillperson", sess.intent_context)
        pairs = [e["data"][0] for e in sess.context.get_context()]
        self.assertIn(("Bob", "tea_skillperson"), pairs)


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
            # the attribute access above warns on its own; drop those calls so
            # this asserts the mutation path warns, not the accessor
            dep.reset_mock()
            view["some.skill"] = UtteranceState.RESPONSE.value
        # exactly once: the internal rebuild projects response_mode directly
        # instead of re-reading the deprecated property
        self.assertEqual(dep.call_count, 1)


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
        # SESSION-1 §3.4: an empty list-valued override field is wire-equivalent
        # to omission, and a producer SHOULD NOT restate the deployment default.
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

    def test_legacy_write_preserves_intent_context_identity(self):
        from ovos_bus_client.session import Session
        session = Session("identity-1")
        session.set_intent_context("pet", "dog", scope="shared")
        held = session.intent_context
        # adapt entity shape: data[0] is (value, key)
        session.context.inject_context({"key": "pet",
                                        "data": [["cat", "pet"]]})
        self.assertIs(session.intent_context, held)
        self.assertEqual(held["pet"]["value"], "cat")

    def test_legacy_clear_context_keeps_dict_and_identity(self):
        from ovos_bus_client.session import Session
        session = Session("clear-1")
        session.set_intent_context("person", "Bob", scope="shared")
        held = session.intent_context
        session.context.clear_context()
        # cleared in place: same object, still a dict, entry tombstoned
        self.assertIs(session.intent_context, held)
        self.assertEqual(session.intent_context, {"person": None})
        self.assertEqual(session.context.frame_stack, [])

    def test_legacy_remove_keeps_dict_and_identity(self):
        from ovos_bus_client.session import Session
        session = Session("remove-1")
        session.set_intent_context("person", "Bob", scope="shared")
        held = session.intent_context
        session.context.remove_context("person")
        self.assertIs(session.intent_context, held)
        self.assertEqual(session.intent_context, {"person": None})

    def test_stale_legacy_frame_stays_dead_across_deserialize(self):
        """A frame older than the legacy timeout is not resurrected."""
        from ovos_bus_client.session import Session
        now = time.time()
        legacy = {
            "session_id": "stale-1",
            "context": {"timeout": 120,
                        "frame_stack": [
                            ({"entities": [_adapt_entity("Bob", "person")],
                              "metadata": {}}, now - 10_000),
                            ({"entities": [_adapt_entity("kitchen", "room")],
                              "metadata": {}}, now),
                        ]},
        }
        sess = Session.deserialize(legacy)
        self.assertNotIn("person", sess.intent_context)
        # the live frame folds with its own timestamp anchoring the expiry
        entry = sess.intent_context["room"]
        self.assertEqual(entry["value"], "kitchen")
        self.assertAlmostEqual(entry["expires_at"], now + 120, delta=5)

    def test_update_from_fold_keeps_intent_context_a_dict(self):
        """Folding a snapshot onto the singleton never leaves None behind."""
        from ovos_bus_client.session import Session
        live = Session("fold-1")
        live.set_intent_context("person", "Bob", scope="shared")
        snapshot = Session.deserialize({"session_id": "fold-1"})
        live.update_from(snapshot)
        self.assertIsInstance(live.intent_context, dict)
        self.assertNotIn("x", live.intent_context)  # membership never raises


if __name__ == "__main__":
    unittest.main()
