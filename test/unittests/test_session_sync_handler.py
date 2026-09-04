"""Tests for the OVOS-CONTEXT-1 §5.3 ``ovos.session.sync`` handler.

The handler merges the inbound snapshot's ``intent_context`` entry-by-entry
onto the managed (singleton) session: present entry objects set or replace,
``null`` entries delete, absent keys are unchanged. The merge mutates the
working map **in place** — the tracked session's map keeps its object
identity and stays a dict, never ``None`` — and the legacy default-session
echo fires only for a bare request that carries no session snapshot.
"""
import unittest
from unittest.mock import MagicMock

from ovos_bus_client.message import Message
from ovos_bus_client.session import Session, SessionManager


def _sync_message(session_dict):
    return Message("ovos.session.sync", context={"session": session_dict})


class TestHandleSessionSync(unittest.TestCase):
    def setUp(self):
        self._sessions = dict(SessionManager.sessions)
        self._bus = SessionManager.bus
        SessionManager.bus = None

    def tearDown(self):
        SessionManager.sessions.clear()
        SessionManager.sessions.update(self._sessions)
        SessionManager.bus = self._bus

    def _track(self, session_id, intent_context=None):
        sess = Session(session_id)
        if intent_context is not None:
            sess.intent_context.update(intent_context)
        SessionManager.sessions[session_id] = sess
        return sess

    def test_set_and_replace_entries(self):
        sess = self._track("sync-1", {"person": {"value": "Bob"}})
        SessionManager.handle_session_sync(_sync_message(
            {"session_id": "sync-1",
             "intent_context": {"person": {"value": "Alice"},
                                "room": {"value": "kitchen"}}}))
        self.assertEqual(sess.intent_context,
                         {"person": {"value": "Alice"},
                          "room": {"value": "kitchen"}})

    def test_null_entry_deletes(self):
        sess = self._track("sync-2", {"person": {"value": "Bob"},
                                      "room": {"value": "kitchen"}})
        SessionManager.handle_session_sync(_sync_message(
            {"session_id": "sync-2", "intent_context": {"person": None}}))
        self.assertEqual(sess.intent_context, {"room": {"value": "kitchen"}})

    def test_absent_keys_unchanged(self):
        sess = self._track("sync-3", {"person": {"value": "Bob"}})
        SessionManager.handle_session_sync(_sync_message(
            {"session_id": "sync-3",
             "intent_context": {"room": {"value": "kitchen"}}}))
        self.assertEqual(sess.intent_context["person"], {"value": "Bob"})

    def test_empty_sync_leaves_map_a_dict_and_identity(self):
        sess = self._track("sync-4")
        held = sess.intent_context
        SessionManager.handle_session_sync(_sync_message(
            {"session_id": "sync-4"}))
        # never rebound, never None: membership on the live map must not raise
        self.assertIs(sess.intent_context, held)
        self.assertNotIn("x", sess.intent_context)

    def test_merge_preserves_map_identity(self):
        sess = self._track("sync-5", {"person": {"value": "Bob"}})
        held = sess.intent_context
        SessionManager.handle_session_sync(_sync_message(
            {"session_id": "sync-5",
             "intent_context": {"room": {"value": "kitchen"}}}))
        self.assertIs(sess.intent_context, held)
        self.assertIn("room", held)

    def test_unheld_session_is_left_alone(self):
        # §2.2/§2.5: a named session this process holds nothing for belongs to
        # another client -- there is nowhere to merge it and nowhere to keep it
        SessionManager.handle_session_sync(_sync_message(
            {"session_id": "sync-new",
             "intent_context": {"person": {"value": "Bob"}}}))
        self.assertIsNone(SessionManager.sessions.get("sync-new"))
        self.assertIsNone(SessionManager.held_session("sync-new"))

    def test_removal_propagates_end_to_end(self):
        """skill-side remove + sync deletes the entry on the managed session."""
        managed = self._track("sync-6", {"person": {"value": "Bob"}})
        # the skill's local copy, as received on its dispatch Message
        local = Session.deserialize(managed.serialize())
        local.remove_intent_context("person", scope="shared")
        SessionManager.handle_session_sync(_sync_message(local.serialize()))
        self.assertNotIn("person",
                         SessionManager.sessions["sync-6"].intent_context)

    def test_no_echo_for_spec_sync(self):
        """A session-carrying §5.3 sync is not a default-session request."""
        SessionManager.bus = MagicMock()
        self._track("sync-7")
        SessionManager.handle_session_sync(_sync_message(
            {"session_id": "sync-7",
             "intent_context": {"person": {"value": "Bob"}}}))
        SessionManager.bus.emit.assert_not_called()

    def test_bare_request_echoes_default_session(self):
        SessionManager.bus = MagicMock()
        SessionManager.get_default_session()
        SessionManager.handle_session_sync(Message("ovos.session.sync"))
        SessionManager.bus.emit.assert_called_once()
        emitted = SessionManager.bus.emit.call_args[0][0]
        self.assertEqual(emitted.msg_type, "ovos.session.update_default")


if __name__ == "__main__":
    unittest.main()
