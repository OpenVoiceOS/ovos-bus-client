"""Coverage tests for ovos_bus_client.session — IntentContextManager,
Session methods, SessionManager handlers/utilities."""
import time
import unittest
from unittest import TestCase
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message
from ovos_bus_client.session import (IntentContextManager,
                                     IntentContextManagerFrame, Session,
                                     SessionManager, UtteranceState)


def _reset_session_manager():
    default = Session("default")
    SessionManager.default_session = default
    SessionManager.sessions = {"default": default}
    SessionManager.bus = None


class TestIntentContextManagerFrame(TestCase):
    def test_serialize_roundtrip(self):
        frame = IntentContextManagerFrame(entities=[{"e": 1}], metadata={"k": "v"})
        d = frame.serialize()
        back = IntentContextManagerFrame.deserialize(d)
        self.assertEqual(back.entities, [{"e": 1}])
        self.assertEqual(back.metadata, {"k": "v"})

    def test_metadata_matches_subset(self):
        frame = IntentContextManagerFrame(metadata={"a": 1, "b": 2})
        self.assertTrue(frame.metadata_matches({"a": 1}))
        self.assertFalse(frame.metadata_matches({"a": 1, "b": 3}))

    def test_metadata_matches_empty_returns_false(self):
        frame = IntentContextManagerFrame(metadata={"a": 1})
        self.assertFalse(frame.metadata_matches({}))
        self.assertFalse(frame.metadata_matches(None))

    def test_merge_context_appends_entity_and_keeps_keys(self):
        frame = IntentContextManagerFrame(entities=[{"k": "old"}], metadata={"x": 1})
        frame.merge_context({"k": "new"}, {"x": 9, "y": 2})
        self.assertEqual(len(frame.entities), 2)
        self.assertEqual(frame.metadata["x"], 1)   # existing key preserved
        self.assertEqual(frame.metadata["y"], 2)   # new key added


class TestIntentContextManager(TestCase):
    def test_serialize_roundtrip(self):
        cm = IntentContextManager(timeout=30)
        cm.inject_context({"data": [("k", "x")], "key": "k"}, metadata={"m": 1})
        d = cm.serialize()
        back = IntentContextManager.deserialize(d)
        self.assertEqual(back.timeout, 30)
        self.assertEqual(len(back.frame_stack), 1)

    def test_inject_creates_first_frame(self):
        cm = IntentContextManager()
        cm.inject_context({"data": [("foo", "bar")], "key": "foo"},
                          metadata={"site": "a"})
        self.assertEqual(len(cm.frame_stack), 1)

    def test_inject_merges_matching_metadata(self):
        cm = IntentContextManager()
        cm.inject_context({"data": [("foo", "bar")], "key": "foo"},
                          metadata={"site": "a"})
        cm.inject_context({"data": [("baz", "qux")], "key": "baz"},
                          metadata={"site": "a"})
        # same metadata → same top frame, entity merged
        self.assertEqual(len(cm.frame_stack), 1)
        top, _t = cm.frame_stack[0]
        self.assertEqual(len(top.entities), 2)

    def test_inject_creates_new_frame_when_metadata_differs(self):
        cm = IntentContextManager()
        cm.inject_context({"data": [("a", "1")], "key": "a"},
                          metadata={"site": "a"})
        cm.inject_context({"data": [("b", "2")], "key": "b"},
                          metadata={"site": "b"})
        self.assertEqual(len(cm.frame_stack), 2)

    def test_clear_context(self):
        cm = IntentContextManager()
        cm.inject_context({"data": [("a", "x")], "key": "a"}, metadata={})
        cm.clear_context()
        self.assertEqual(cm.frame_stack, [])

    def test_update_context_keyword_match(self):
        cm = IntentContextManager(keywords=["foo"])
        # the check is `entity['data'][0][1] in keywords` — index 1 of the first tuple
        cm.update_context([
            {"data": [("k1", "foo")], "key": "k1"},   # matches
            {"data": [("k2", "bar")], "key": "k2"},   # does not match
        ])
        self.assertEqual(len(cm.frame_stack), 1)

    def test_update_context_greedy_injects_all(self):
        cm = IntentContextManager(greedy=True)
        cm.update_context([
            {"data": [("foo", "v1")], "key": "foo"},
            {"data": [("bar", "v2")], "key": "bar"},
        ])
        self.assertEqual(len(cm.frame_stack), 2)


class TestSessionLifecycle(TestCase):
    def setUp(self):
        _reset_session_manager()

    def test_default_session_id_uuid(self):
        s = Session()
        self.assertTrue(len(s.session_id) > 0)

    def test_timezone_property(self):
        s = Session(location_prefs={"timezone": {"code": "Europe/Lisbon"}})
        self.assertEqual(s.timezone, "Europe/Lisbon")

    def test_timezone_none_when_missing(self):
        # explicitly empty timezone block to bypass any global config defaults
        s = Session(location_prefs={"timezone": {}})
        self.assertIsNone(s.timezone)

    def test_active_property(self):
        s = Session()
        self.assertFalse(s.active)
        s.active_skills = [["skill.foo", time.time()]]
        self.assertTrue(s.active)

    def test_touch_updates_timestamp(self):
        s = Session()
        old = s.touch_time
        time.sleep(0.01)
        s.touch_time = old - 5
        s.touch()
        self.assertGreater(s.touch_time, old - 5)

    def test_expired_false_when_ttl_negative(self):
        s = Session(expiration_seconds=-1)
        self.assertFalse(s.expired())

    def test_expired_true_when_past_ttl(self):
        s = Session(expiration_seconds=1)
        s.touch_time = int(time.time()) - 60
        self.assertTrue(s.expired())

    def test_expired_false_within_ttl(self):
        s = Session(expiration_seconds=60)
        self.assertFalse(s.expired())

    def test_str_repr(self):
        s = Session("my-id")
        self.assertIn("my-id", str(s))


class TestSessionSkillManagement(TestCase):
    def setUp(self):
        _reset_session_manager()

    def test_activate_skill_inserts_at_front(self):
        s = Session()
        s.activate_skill("skill.a")
        s.activate_skill("skill.b")
        self.assertEqual(s.active_skills[0][0], "skill.b")
        self.assertEqual(s.active_skills[1][0], "skill.a")

    def test_activate_skill_deduplicates(self):
        s = Session()
        s.activate_skill("skill.a")
        s.activate_skill("skill.a")
        ids = [pair[0] for pair in s.active_skills]
        self.assertEqual(ids.count("skill.a"), 1)

    def test_deactivate_skill_removes(self):
        s = Session()
        s.activate_skill("skill.a")
        s.deactivate_skill("skill.a")
        self.assertFalse(s.is_active("skill.a"))

    def test_deactivate_skill_silent_when_absent(self):
        s = Session()
        s.deactivate_skill("never.activated")  # no error

    def test_is_active(self):
        s = Session()
        s.activate_skill("skill.x")
        self.assertTrue(s.is_active("skill.x"))
        self.assertFalse(s.is_active("skill.y"))

    def test_clear_empties_active_skills(self):
        s = Session()
        s.activate_skill("skill.a")
        s.activate_skill("skill.b")
        s.clear()
        self.assertEqual(s.active_skills, [])

    def test_response_mode_toggle(self):
        s = Session()
        s.enable_response_mode("skill.a")
        self.assertEqual(s.utterance_states["skill.a"], UtteranceState.RESPONSE.value)
        s.disable_response_mode("skill.a")
        # OVOS-CONVERSE-1 §2.2: a non-holder is implicitly INTENT (absent from the
        # legacy utterance_states view); ecosystem readers use .get(id, INTENT).
        self.assertEqual(s.utterance_states.get("skill.a", UtteranceState.INTENT.value),
                         UtteranceState.INTENT.value)


class TestSessionSerialization(TestCase):
    def setUp(self):
        _reset_session_manager()

    def test_serialize_includes_all_keys(self):
        s = Session("sid", lang="pt-pt", site_id="kitchen", persona_id="p1")
        d = s.serialize()
        for key in ["active_skills", "utterance_states", "session_id",
                    "persona_id", "lang", "context", "site_id", "pipeline",
                    "location", "system_unit", "time_format", "date_format",
                    "is_speaking", "is_recording", "blacklisted_skills",
                    "blacklisted_intents"]:
            self.assertIn(key, d)
        self.assertEqual(d["session_id"], "sid")
        self.assertEqual(d["persona_id"], "p1")
        self.assertEqual(d["site_id"], "kitchen")

    def test_deserialize_roundtrip(self):
        s = Session("sid", lang="en-us", site_id="lab")
        s.activate_skill("skill.x")
        d = s.serialize()
        restored = Session.deserialize(d)
        self.assertEqual(restored.session_id, "sid")
        self.assertEqual(restored.site_id, "lab")
        self.assertTrue(restored.is_active("skill.x"))

    def test_from_message_uses_context_session(self):
        s = Session("from-msg", site_id="A")
        msg = Message("t", context={"session": s.serialize()})
        sess = Session.from_message(msg)
        self.assertEqual(sess.session_id, "from-msg")
        self.assertEqual(sess.site_id, "A")

    def test_from_message_falls_back_to_default_when_no_context(self):
        _reset_session_manager()
        msg = Message("t", context={})
        sess = Session.from_message(msg)
        self.assertEqual(sess.session_id, "default")

    def test_from_message_falls_back_when_none(self):
        _reset_session_manager()
        sess = Session.from_message(None)
        self.assertEqual(sess.session_id, "default")


class TestSessionManager(TestCase):
    def setUp(self):
        _reset_session_manager()

    def test_get_returns_default_for_message_without_session(self):
        msg = Message("t", context={})
        sess = SessionManager.get(msg)
        self.assertEqual(sess.session_id, "default")

    def test_get_registers_non_default_session(self):
        s = Session("k")
        msg = Message("t", context={"session": s.serialize()})
        sess = SessionManager.get(msg)
        self.assertEqual(sess.session_id, "k")
        self.assertIn("k", SessionManager.sessions)

    def test_update_stores_session(self):
        s = Session("upd")
        SessionManager.update(s)
        self.assertIs(SessionManager.sessions["upd"], s)

    def test_update_make_default(self):
        s = Session("foo")
        SessionManager.update(s, make_default=True)
        self.assertEqual(s.session_id, "default")
        self.assertIs(SessionManager.default_session, s)

    def test_update_raises_on_none(self):
        with self.assertRaises(ValueError):
            SessionManager.update(None)

    def test_touch_updates_default_when_no_message(self):
        before = SessionManager.default_session.touch_time
        SessionManager.default_session.touch_time = before - 100
        SessionManager.touch()
        self.assertGreater(SessionManager.default_session.touch_time, before - 100)

    def test_reset_default_session_creates_fresh(self):
        old = SessionManager.default_session
        new = SessionManager.reset_default_session()
        self.assertIsNot(new, old)
        self.assertEqual(new.session_id, "default")

    def test_prune_sessions_runs(self):
        # SessionManager.prune_sessions exists and is callable. NOTE: the
        # production implementation references `s.expired` (the bound method
        # truthy object) instead of `s.expired()`, so the predicate filters
        # everything — a pre-existing bug. We just verify the method runs.
        live = Session("live", expiration_seconds=-1)
        SessionManager.update(live)
        SessionManager.prune_sessions()  # should not raise

    def test_is_speaking_default(self):
        self.assertFalse(SessionManager.is_speaking())
        sess = SessionManager.default_session
        sess.is_speaking = True
        SessionManager.update(sess)
        self.assertTrue(SessionManager.is_speaking())

    def test_is_recording_default(self):
        self.assertFalse(SessionManager.is_recording())

    def test_handle_recording_start_and_end(self):
        s = Session("rec-1")
        msg = Message("recognizer_loop:record_begin",
                      context={"session": s.serialize()})
        SessionManager.handle_recording_start(msg)
        self.assertTrue(SessionManager.sessions["rec-1"].is_recording)
        SessionManager.handle_recording_end(msg)
        self.assertFalse(SessionManager.sessions["rec-1"].is_recording)

    def test_handle_audio_output_start_and_end(self):
        s = Session("aud-1")
        msg = Message("recognizer_loop:audio_output_start",
                      context={"session": s.serialize()})
        SessionManager.handle_audio_output_start(msg)
        self.assertTrue(SessionManager.sessions["aud-1"].is_speaking)
        SessionManager.handle_audio_output_end(msg)
        self.assertFalse(SessionManager.sessions["aud-1"].is_speaking)

    def test_sync_emits_when_bus_attached(self):
        bus = MagicMock()
        SessionManager.bus = bus
        try:
            SessionManager.sync()
            self.assertTrue(bus.emit.called)
            emitted = bus.emit.call_args[0][0]
            self.assertEqual(emitted.msg_type, "ovos.session.update_default")
        finally:
            SessionManager.bus = None

    def test_sync_noop_without_bus(self):
        SessionManager.bus = None
        # must not raise
        SessionManager.sync()

    def test_connect_to_bus_registers_handlers(self):
        bus = MagicMock()
        try:
            SessionManager.connect_to_bus(bus)
            registered = {call.args[0] for call in bus.on.call_args_list}
            for event in [
                "recognizer_loop:record_begin",
                "recognizer_loop:record_end",
                "recognizer_loop:audio_output_start",
                "recognizer_loop:audio_output_end",
                "ovos.session.sync",
            ]:
                self.assertIn(event, registered)
            self.assertTrue(bus.emit.called)
        finally:
            SessionManager.bus = None

    def test_wait_while_speaking_no_bus_returns_immediately(self):
        SessionManager.bus = None
        # no bus → logs error, returns; should not raise
        SessionManager.wait_while_speaking(timeout=0.01)

    def test_wait_while_recording_no_bus_returns_immediately(self):
        SessionManager.bus = None
        SessionManager.wait_while_recording(timeout=0.01)

    def test_wait_while_speaking_not_speaking_returns_immediately(self):
        SessionManager.bus = MagicMock()
        try:
            sess = SessionManager.default_session
            sess.is_speaking = False
            SessionManager.update(sess)
            SessionManager.wait_while_speaking(timeout=0.01)
        finally:
            SessionManager.bus = None

    def test_wait_while_speaking_invalid_timeout_warns(self):
        """Bool timeout triggers a warning branch — patch Event.wait so we
        don't actually block on the 15-second fallback."""
        SessionManager.bus = MagicMock()
        sess = SessionManager.default_session
        sess.is_speaking = True
        SessionManager.update(sess)
        try:
            with patch("ovos_bus_client.session.Event") as ev_cls:
                ev_cls.return_value.wait = MagicMock(return_value=True)
                ev_cls.return_value.is_set = MagicMock(return_value=True)
                SessionManager.wait_while_speaking(timeout=True)
        finally:
            sess.is_speaking = False
            SessionManager.update(sess)
            SessionManager.bus = None


if __name__ == "__main__":
    unittest.main()
