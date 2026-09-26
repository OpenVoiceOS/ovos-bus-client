"""More session.py coverage — IntentContextManager.get_context and remaining bits."""
import time
import unittest
from unittest import TestCase
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message
from ovos_bus_client.session import (IntentContextManager, Session,
                                     SessionManager)


def _entity(name, value, origin=""):
    return {
        "data": [(name, value)],
        "key": name,
        "confidence": 1.0,
        "origin": origin,
    }


class TestGetContext(TestCase):
    def setUp(self):
        self.cm = IntentContextManager(timeout=3600, max_frames=5, greedy=True)

    def test_get_context_empty(self):
        self.assertEqual(self.cm.get_context(), [])

    def test_get_context_returns_entities(self):
        self.cm.inject_context(_entity("k1", "v1", origin="o1"))
        self.cm.inject_context(_entity("k2", "v2", origin="o2"))
        ctx = self.cm.get_context()
        self.assertGreaterEqual(len(ctx), 1)

    def test_get_context_with_missing_entities(self):
        self.cm.inject_context(_entity("k1", "v1", origin="o1"))
        self.cm.inject_context(_entity("k2", "v2", origin="o2"))
        # missing_entities filter just checks `entity.get('data') in missing_entities`;
        # we just verify the call returns a list without raising
        ctx = self.cm.get_context(missing_entities=["something"])
        self.assertIsInstance(ctx, list)

    def test_get_context_max_frames_bound(self):
        for i in range(3):
            self.cm.inject_context(_entity(f"k{i}", f"v{i}", origin=f"o{i}"))
        # request fewer frames than exist
        ctx = self.cm.get_context(max_frames=1)
        self.assertIsInstance(ctx, list)


class TestStripResult(TestCase):
    def test_strip_dedupes_repeated_keyword(self):
        # keyword is feature['data'][0][1]
        items = [
            {"data": [("k", "same")], "key": "k"},
            {"data": [("k", "same")], "key": "k"},   # dupe → dropped
            {"data": [("k", "different")], "key": "k"},
        ]
        out = IntentContextManager._strip_result(items)
        self.assertEqual(len(out), 2)

    def test_strip_keeps_all_when_distinct(self):
        items = [
            {"data": [("k", "one")], "key": "k"},
            {"data": [("k", "two")], "key": "k"},
        ]
        self.assertEqual(len(IntentContextManager._strip_result(items)), 2)


class TestSessionExtra(TestCase):
    def test_update_history_is_deprecated_noop(self):
        s = Session()
        # should not raise, just logs a warning
        s.update_history(Message("any"))

    def test_serialize_includes_blacklists(self):
        s = Session(blacklisted_skills=["foo.skill"],
                    blacklisted_intents=["foo.intent"])
        d = s.serialize()
        self.assertEqual(d["blacklisted_skills"], ["foo.skill"])
        self.assertEqual(d["blacklisted_intents"], ["foo.intent"])

    def test_pipeline_default_is_a_list(self):
        s = Session()
        self.assertIsInstance(s.pipeline, list)
        self.assertGreater(len(s.pipeline), 0)


class TestSessionManagerExtra(TestCase):
    def setUp(self):
        default = Session("default")
        SessionManager.sessions = {"default": default}
        SessionManager.bus = None

    def test_handle_default_session_request_delegates_to_sync(self):
        bus = MagicMock()
        SessionManager.bus = bus
        try:
            SessionManager.handle_default_session_request(Message("ovos.session.sync"))
            # sync emits an update
            self.assertTrue(bus.emit.called)
        finally:
            SessionManager.bus = None

    def test_broadcast_default_session_without_mirror_attribute(self):
        # a fresh process never wrote the `default_session` mirror -- the
        # broadcast must read the store via get_default_session(), not raise
        # AttributeError on a class attribute that was never set.
        if hasattr(SessionManager, "default_session"):
            delattr(SessionManager, "default_session")
        bus = MagicMock()
        SessionManager.bus = bus
        try:
            SessionManager._broadcast_default_session()
            self.assertTrue(bus.emit.called)
        finally:
            SessionManager.bus = None

    def test_default_session_mirror_owned_by_bus_client(self):
        # ovos-bus-client owns this pre-spec `default_session` mirror -- it
        # must keep tracking the registry through get/reset even if the
        # spec-tools base this grafts onto no longer defines the attribute
        # at all (simulated here by deleting it before each call).
        if hasattr(SessionManager, "default_session"):
            delattr(SessionManager, "default_session")
        sess = SessionManager.get_default_session()
        self.assertIs(SessionManager.default_session, sess)

        delattr(SessionManager, "default_session")
        new_sess = SessionManager.reset_default_session()
        self.assertIs(SessionManager.default_session, new_sess)
        self.assertIsNot(new_sess, sess)


class TestFromMessageLangFallback(TestCase):
    def test_from_message_merges_lang_from_context(self):
        # context has lang but session dict does not
        s = Session("s")
        sess_dict = s.serialize()
        sess_dict.pop("lang", None)
        msg = Message("t", context={"lang": "pt-pt", "session": sess_dict})
        sess = Session.from_message(msg)
        self.assertEqual(sess.lang.lower(), "pt-pt")


if __name__ == "__main__":
    unittest.main()
