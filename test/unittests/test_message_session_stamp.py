"""OVOS-CONTEXT-1 §5.3 / OVOS-MSG-1 §5 — a bound-session write must survive
onto CollectionMessage / GUIMessage derivations.

``CollectionMessage`` and ``GUIMessage`` cannot inherit the spec-tools
``Message.forward`` / ``reply`` stamping (their ``__init__`` signatures do
not match), so their ``forward``/``reply``/``response`` overrides call
``ovos_spec_tools.message._stamp_live_session`` by hand. It consults the
session a handler bound via ``SessionManager.get(msg)``, not just the
default-session store — a handler that read its session off a collect/GUI
message and wrote an intent-context entry on a *named* session must not
lose that write the moment the message is forwarded/replied/responded to.
"""
import unittest

from ovos_bus_client.message import CollectionMessage, GUIMessage, Message
from ovos_bus_client.session import Session, SessionManager


def _named_context(session_id="kitchen-sat"):
    return {"session": {"session_id": session_id, "lang": "en-US"}}


class TestCollectionMessageCarriesBoundSessionWrites(unittest.TestCase):

    def setUp(self):
        SessionManager.reset_default_session()

    def tearDown(self):
        SessionManager.reset_default_session()
        SessionManager.sessions.pop("kitchen-sat", None)

    def _bind_and_mutate(self, msg):
        sess = SessionManager.get(msg)
        sess.set_intent_context("k", "v", scope="shared")
        return sess

    def test_forward_carries_the_bound_write(self):
        msg = CollectionMessage("q", "h1", "q1", {}, _named_context())
        self._bind_and_mutate(msg)

        fwd = msg.forward("q.forwarded", {})

        stamped = Session.deserialize(fwd.context["session"])
        self.assertIn("k", stamped.intent_context)

    def test_reply_carries_the_bound_write(self):
        msg = CollectionMessage("q", "h1", "q1", {}, _named_context())
        self._bind_and_mutate(msg)

        rep = msg.reply("q.reply", {})

        stamped = Session.deserialize(rep.context["session"])
        self.assertIn("k", stamped.intent_context)

    def test_response_carries_the_bound_write(self):
        msg = CollectionMessage("q", "h1", "q1", {}, _named_context())
        self._bind_and_mutate(msg)

        resp = msg.response({})

        stamped = Session.deserialize(resp.context["session"])
        self.assertIn("k", stamped.intent_context)

    def test_explicit_session_in_reply_context_still_wins(self):
        msg = CollectionMessage("q", "h1", "q1", {}, _named_context())
        self._bind_and_mutate(msg)

        explicit = {"session_id": "kitchen-sat", "lang": "pt-PT"}
        rep = msg.reply("q.reply", {}, context={"session": explicit})

        # the caller-supplied session is honoured verbatim, not overwritten
        # with the live bound session's stamp
        self.assertEqual(rep.context["session"], explicit)
        self.assertNotIn("intent_context", rep.context["session"])


class TestGUIMessageCarriesBoundSessionWrites(unittest.TestCase):

    def setUp(self):
        SessionManager.reset_default_session()

    def tearDown(self):
        SessionManager.reset_default_session()
        SessionManager.sessions.pop("kitchen-sat", None)

    def _gui_message_with_context(self, msg_type="gui.show"):
        # GUIMessage.__init__ only accepts kwargs -> data, so build the
        # context by hand as forward()/reply() do internally.
        msg = GUIMessage(msg_type)
        msg.context = _named_context()
        return msg

    def _bind_and_mutate(self, msg):
        sess = SessionManager.get(msg)
        sess.set_intent_context("k", "v", scope="shared")
        return sess

    def test_forward_carries_the_bound_write(self):
        msg = self._gui_message_with_context()
        self._bind_and_mutate(msg)

        fwd = msg.forward("gui.forwarded", {})

        stamped = Session.deserialize(fwd.context["session"])
        self.assertIn("k", stamped.intent_context)

    def test_reply_carries_the_bound_write(self):
        msg = self._gui_message_with_context()
        self._bind_and_mutate(msg)

        rep = msg.reply("gui.reply", {})

        stamped = Session.deserialize(rep.context["session"])
        self.assertIn("k", stamped.intent_context)

    def test_response_carries_the_bound_write(self):
        msg = self._gui_message_with_context()
        self._bind_and_mutate(msg)

        resp = msg.response({})

        stamped = Session.deserialize(resp.context["session"])
        self.assertIn("k", stamped.intent_context)

    def test_explicit_session_in_reply_context_still_wins(self):
        msg = self._gui_message_with_context()
        self._bind_and_mutate(msg)

        explicit = {"session_id": "kitchen-sat", "lang": "pt-PT"}
        rep = msg.reply("gui.reply", {}, context={"session": explicit})

        self.assertEqual(rep.context["session"], explicit)
        self.assertNotIn("intent_context", rep.context["session"])


if __name__ == "__main__":
    unittest.main()
