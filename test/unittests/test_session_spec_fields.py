"""Tests for the OVOS-SESSION-1 / OVOS-PIPELINE-1 §7.1 / OVOS-CONVERSE-1
§2.1, §2.2 session fields on ovos_bus_client.session.Session:

- active_handlers / converse_handlers shape and serialize round-trip
- dedup + head-first recency ordering
- cap eviction (tail-drop)
- TTL pruning
- response_mode single-holder + omission-not-null
- active_skills / utterance_states back-compat projections
"""
import time
from unittest import TestCase

from ovos_spec_tools.session import Session as SpecSession
from ovos_bus_client.session import (Session, UtteranceState,
                                     DEFAULT_CONVERSE_HANDLERS_CAP)


class TestCanonicalSubclass(TestCase):
    """The bus-client Session is a thin subclass of the canonical
    OVOS-SESSION-1 reference implementation: the spec wire-shape fields and
    helpers come from the parent, the bus-client subclass only layers
    deployment defaults + lifecycle + back-compat on top."""

    def test_is_canonical_subclass(self):
        self.assertTrue(issubclass(Session, SpecSession))
        self.assertIsInstance(Session(), SpecSession)

    def test_default_cap_inherited_constant(self):
        # the cap constant is re-exported from the canonical module
        from ovos_spec_tools.session import (
            DEFAULT_CONVERSE_HANDLERS_CAP as SPEC_CAP)
        self.assertEqual(DEFAULT_CONVERSE_HANDLERS_CAP, SPEC_CAP)

    def test_handler_helpers_inherited_from_parent(self):
        # the recency/cap/prune helpers are not redefined on the subclass —
        # they resolve to the canonical parent implementation.
        for name in ("add_active_handler", "remove_active_handler",
                     "add_converse_handler", "remove_converse_handler",
                     "prune_converse_handlers", "set_response_mode",
                     "clear_response_mode"):
            with self.subTest(method=name):
                # subclass overrides exist (to add touch()) ...
                self.assertIn(name, Session.__dict__)
                # ... but delegate to the canonical parent
                self.assertIn(name, SpecSession.__dict__)

    def test_coerce_static_helpers_inherited(self):
        # static coercion helpers live on the parent, inherited unchanged
        self.assertNotIn("_coerce_handlers", Session.__dict__)
        self.assertNotIn("_coerce_response_mode", Session.__dict__)
        self.assertIs(Session._coerce_handlers, SpecSession._coerce_handlers)
        self.assertIs(Session._coerce_response_mode,
                      SpecSession._coerce_response_mode)

    def test_spec_fields_present(self):
        # the OVOS-SESSION-1 §3 registered fields are all carried
        s = Session()
        for field in ("secondary_langs", "output_lang", "stt_lang",
                      "request_lang", "detected_lang", "intent_context",
                      "blacklisted_pipelines", "audio_transformers",
                      "tts_transformers"):
            self.assertTrue(hasattr(s, field), field)

    def test_touch_called_on_mutation(self):
        # the subclass overrides mutators to also bump touch_time
        s = Session()
        before = s.touch_time
        s.touch_time = 0  # force a detectable change
        s.add_active_handler("skill.a")
        self.assertGreaterEqual(s.touch_time, before)


class TestActiveHandlersShape(TestCase):
    def test_default_empty(self):
        s = Session()
        self.assertEqual(s.active_handlers, [])
        self.assertEqual(s.converse_handlers, [])
        self.assertIsNone(s.response_mode)

    def test_add_active_handler_shape(self):
        s = Session()
        s.add_active_handler("skill.a")
        self.assertEqual(len(s.active_handlers), 1)
        entry = s.active_handlers[0]
        self.assertEqual(set(entry.keys()), {"skill_id", "activated_at"})
        self.assertEqual(entry["skill_id"], "skill.a")
        self.assertIsInstance(entry["activated_at"], float)

    def test_head_first_recency(self):
        s = Session()
        s.add_active_handler("skill.a")
        s.add_active_handler("skill.b")
        s.add_active_handler("skill.c")
        # most recently activated is at the head
        self.assertEqual([h["skill_id"] for h in s.active_handlers],
                         ["skill.c", "skill.b", "skill.a"])

    def test_dedup_promotes_to_head(self):
        s = Session()
        s.add_active_handler("skill.a", activated_at=1.0)
        s.add_active_handler("skill.b", activated_at=2.0)
        s.add_active_handler("skill.a", activated_at=3.0)  # re-activate
        ids = [h["skill_id"] for h in s.active_handlers]
        self.assertEqual(ids, ["skill.a", "skill.b"])  # a promoted, no dup
        self.assertEqual(len(s.active_handlers), 2)
        self.assertEqual(s.active_handlers[0]["activated_at"], 3.0)

    def test_remove_active_handler(self):
        s = Session()
        s.add_active_handler("skill.a")
        s.add_active_handler("skill.b")
        s.remove_active_handler("skill.a")
        self.assertEqual([h["skill_id"] for h in s.active_handlers], ["skill.b"])


class TestConverseHandlers(TestCase):
    def test_dedup_and_recency(self):
        s = Session()
        s.add_converse_handler("skill.a", activated_at=1.0)
        s.add_converse_handler("skill.b", activated_at=2.0)
        s.add_converse_handler("skill.a", activated_at=3.0)
        ids = [h["skill_id"] for h in s.converse_handlers]
        self.assertEqual(ids, ["skill.a", "skill.b"])

    def test_cap_eviction_tail_drop(self):
        # §2.1 — the cap is supplied per insertion by the orchestrator.
        s = Session()
        for i in range(5):
            s.add_converse_handler(f"skill.{i}", activated_at=float(i), cap=3)
        # head-first, only the 3 most recent survive (skill.4, 3, 2)
        ids = [h["skill_id"] for h in s.converse_handlers]
        self.assertEqual(ids, ["skill.4", "skill.3", "skill.2"])
        self.assertEqual(len(s.converse_handlers), 3)

    def test_cap_unbounded(self):
        s = Session()
        for i in range(100):
            s.add_converse_handler(f"skill.{i}", cap=0)  # unbounded
        self.assertEqual(len(s.converse_handlers), 100)

    def test_cap_is_not_session_state(self):
        # §2.1 — the cap is a deployment value applied at insertion time,
        # never an attribute carried on the session.
        s = Session()
        self.assertFalse(hasattr(s, "converse_handlers_cap"))

    def test_default_cap_value(self):
        # the default cap is the spec's documented §2.1 value, used as the
        # add_converse_handler default arg — not a session field.
        self.assertEqual(DEFAULT_CONVERSE_HANDLERS_CAP, 64)

    def test_ttl_prune(self):
        now = time.time()
        s = Session()
        s.converse_handlers = [
            {"skill_id": "fresh", "activated_at": now - 1},
            {"skill_id": "stale", "activated_at": now - 1000},
        ]
        s.prune_converse_handlers(ttl=300, now=now)
        ids = [h["skill_id"] for h in s.converse_handlers]
        self.assertEqual(ids, ["fresh"])

    def test_ttl_zero_disables_prune(self):
        now = time.time()
        s = Session()
        s.converse_handlers = [{"skill_id": "old", "activated_at": now - 9999}]
        s.prune_converse_handlers(ttl=0, now=now)
        self.assertEqual(len(s.converse_handlers), 1)

    def test_remove_converse_handler(self):
        s = Session()
        s.add_converse_handler("skill.a")
        s.add_converse_handler("skill.b")
        s.remove_converse_handler("skill.b")
        self.assertEqual([h["skill_id"] for h in s.converse_handlers], ["skill.a"])


class TestResponseMode(TestCase):
    def test_set_and_shape(self):
        s = Session()
        s.set_response_mode("skill.a", expires_at=123.0)
        self.assertEqual(s.response_mode, {"skill_id": "skill.a", "expires_at": 123.0})

    def test_single_holder_overwrite(self):
        s = Session()
        s.set_response_mode("skill.a", expires_at=1.0)
        s.set_response_mode("skill.b", expires_at=2.0)  # overwrites silently
        self.assertEqual(s.response_mode["skill_id"], "skill.b")

    def test_clear_by_holder(self):
        s = Session()
        s.set_response_mode("skill.a", expires_at=1.0)
        s.clear_response_mode("skill.b")  # not the holder -> no-op
        self.assertIsNotNone(s.response_mode)
        s.clear_response_mode("skill.a")  # holder -> clears
        self.assertIsNone(s.response_mode)

    def test_clear_unconditional(self):
        s = Session()
        s.set_response_mode("skill.a", expires_at=1.0)
        s.clear_response_mode()  # no skill_id -> unconditional
        self.assertIsNone(s.response_mode)

    def test_omission_not_null_in_serialize(self):
        s = Session()
        d = s.serialize()
        # SESSION-1 §2.1: absent when empty, never JSON null
        self.assertNotIn("response_mode", d)
        self.assertNotIn("active_handlers", d)
        self.assertNotIn("converse_handlers", d)
        s.set_response_mode("skill.a", expires_at=9.0)
        s.add_active_handler("skill.a")
        s.add_converse_handler("skill.a")
        d = s.serialize()
        self.assertIn("response_mode", d)
        self.assertIn("active_handlers", d)
        self.assertIn("converse_handlers", d)
        self.assertIsNotNone(d["response_mode"])

    def test_malformed_response_mode_coerced_to_none(self):
        # SESSION-1 §2.1: a null/malformed value behaves as omitted
        self.assertIsNone(Session._coerce_response_mode(None))
        self.assertIsNone(Session._coerce_response_mode({}))
        self.assertIsNone(Session._coerce_response_mode({"expires_at": 1}))
        self.assertIsNone(Session._coerce_response_mode("garbage"))


class TestSerializeRoundTrip(TestCase):
    def test_roundtrip_spec_fields(self):
        s = Session("sid")
        s.add_active_handler("skill.a", activated_at=1.0)
        s.add_active_handler("skill.b", activated_at=2.0)
        s.add_converse_handler("skill.c", activated_at=3.0)
        s.set_response_mode("skill.b", expires_at=99.0)
        d = s.serialize()
        back = Session.deserialize(d)
        self.assertEqual([h["skill_id"] for h in back.active_handlers],
                         ["skill.b", "skill.a"])
        self.assertEqual([h["skill_id"] for h in back.converse_handlers],
                         ["skill.c"])
        self.assertEqual(back.response_mode, {"skill_id": "skill.b", "expires_at": 99.0})

    def test_empty_roundtrip(self):
        s = Session("sid")
        back = Session.deserialize(s.serialize())
        self.assertEqual(back.active_handlers, [])
        self.assertEqual(back.converse_handlers, [])
        self.assertIsNone(back.response_mode)

    def test_spec_fields_take_precedence_over_legacy(self):
        # when both active_handlers and legacy active_skills present, spec wins
        data = {
            "session_id": "sid",
            "active_skills": [["legacy.skill", 1.0]],
            "active_handlers": [{"skill_id": "spec.skill", "activated_at": 2.0}],
        }
        s = Session.deserialize(data)
        self.assertEqual([h["skill_id"] for h in s.active_handlers], ["spec.skill"])


class TestActiveSkillsBackCompat(TestCase):
    def test_active_skills_projects_active_handlers(self):
        s = Session()
        s.add_active_handler("skill.a", activated_at=1.0)
        s.add_active_handler("skill.b", activated_at=2.0)
        # legacy view: list of [skill_id, ts] pairs, head-first
        self.assertEqual(s.active_skills, [["skill.b", 2.0], ["skill.a", 1.0]])

    def test_active_skills_setter_rewrites_handlers(self):
        s = Session()
        s.active_skills = [["skill.x", 5.0], ["skill.y", 6.0]]
        self.assertEqual([h["skill_id"] for h in s.active_handlers],
                         ["skill.x", "skill.y"])
        self.assertEqual(s.active_handlers[0]["activated_at"], 5.0)

    def test_legacy_activate_skill_shim(self):
        s = Session()
        s.activate_skill("skill.a")
        s.activate_skill("skill.b")
        self.assertTrue(s.is_active("skill.a"))
        self.assertEqual(s.active_skills[0][0], "skill.b")  # most recent at head

    def test_legacy_deactivate_skill_shim(self):
        s = Session()
        s.activate_skill("skill.a")
        s.deactivate_skill("skill.a")
        self.assertFalse(s.is_active("skill.a"))

    def test_active_property(self):
        s = Session()
        self.assertFalse(s.active)
        s.activate_skill("skill.a")
        self.assertTrue(s.active)

    def test_deserialize_legacy_active_skills(self):
        # old wire shape with only legacy active_skills still hydrates handlers
        data = {"session_id": "sid", "active_skills": [["skill.a", 1.0], ["skill.b", 2.0]]}
        s = Session.deserialize(data)
        self.assertEqual([h["skill_id"] for h in s.active_handlers],
                         ["skill.a", "skill.b"])

    def test_utterance_states_projects_response_mode(self):
        s = Session()
        s.enable_response_mode("skill.a")
        self.assertEqual(s.utterance_states, {"skill.a": UtteranceState.RESPONSE.value})
        s.disable_response_mode("skill.a")
        self.assertEqual(s.utterance_states, {})

    def test_deserialize_legacy_utterance_states(self):
        data = {"session_id": "sid",
                "utterance_states": {"skill.a": UtteranceState.RESPONSE.value}}
        s = Session.deserialize(data)
        self.assertIsNotNone(s.response_mode)
        self.assertEqual(s.response_mode["skill_id"], "skill.a")


class TestInheritedCanonicalScalarFields(TestCase):
    """persona_id (OVOS-PERSONA-1) and fallback_handlers (OVOS-FALLBACK-1 §4)
    are inherited canonical fields: the subclass forwards them to
    ``super().__init__`` so the parent owns validation + omit-when-empty,
    instead of re-declaring / re-emitting them."""

    def test_persona_id_forwarded_to_parent(self):
        s = Session("sid", persona_id="assistant")
        self.assertEqual(s.persona_id, "assistant")

    def test_persona_id_round_trip(self):
        s = Session("sid", persona_id="assistant")
        back = Session.deserialize(s.serialize())
        self.assertEqual(back.persona_id, "assistant")

    def test_persona_id_omitted_from_canonical_dict_when_none(self):
        # parent to_dict() honours SESSION-1 §2.1 omit-when-empty
        s = Session("sid")
        self.assertIsNone(s.persona_id)
        self.assertNotIn("persona_id", SpecSession.to_dict(s))

    def test_fallback_handlers_forwarded_to_parent(self):
        s = Session("sid", fallback_handlers=["skill.a", "skill.b"])
        self.assertEqual(s.fallback_handlers, ["skill.a", "skill.b"])

    def test_fallback_handlers_round_trip(self):
        s = Session("sid", fallback_handlers=["skill.a", "skill.b"])
        back = Session.deserialize(s.serialize())
        self.assertEqual(back.fallback_handlers, ["skill.a", "skill.b"])

    def test_fallback_handlers_omitted_when_empty(self):
        # §3.4 empty-list ≡ omission; parent to_dict() drops it
        s = Session("sid")
        self.assertIsNone(s.fallback_handlers)
        self.assertNotIn("fallback_handlers", SpecSession.to_dict(s))

    def test_fallback_handlers_is_inherited_not_overridden(self):
        # the field is carried by the canonical parent, not redeclared here
        self.assertIn("fallback_handlers", SpecSession().__init__.__code__.co_varnames)
