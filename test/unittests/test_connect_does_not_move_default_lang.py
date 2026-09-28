"""T-6516: a connecting client must not move the orchestrator's default lang.

The report: an nl-NL install answers in English after a media request. The
cause is not the media stack. Every MessageBusClient used to emit
``ovos.session.sync`` on connect (a REQUEST), and ``emit`` stamps
``context["session"]`` with ``_own_session()`` on a message that carries none
-- which on a default-id client is that process's own config-derived default
session. An orchestrator folds a present inbound field onto its default-session
store (OVOS-SESSION-2 §5.1), so a process started without the user's
``mycroft.conf`` carried ``en-US`` and set the whole deployment's default lang
to it on connect. Core then broadcasts its store to every process.

OVOS-SESSION-2 §2.7 rules the request out and supplies the replacement:

    This specification defines no topic on which any participant pushes a
    session at another.

    Each derives its initial view of that session from the deployment
    configuration ... no handshake, bootstrap request, or announcement is
    needed to make them agree.

The handler is NOT the defect and is not changed: §5.1 fixes the merge as "a
property of the pathway, fixed here, never of the producer's intent", so an
orchestrator that skipped a client's ``lang`` would be disobeying §5.1 to
correct an emitter (architecture ruling,
knowledge/wiki/audits/architecture/T-6516-session-2-default-lang.md).
"""
import unittest
from unittest.mock import MagicMock

from ovos_bus_client.client import MessageBusClient
from ovos_bus_client.message import Message
from ovos_bus_client.session import Session, SessionManager


class TestConnectDoesNotMoveTheDefaultLang(unittest.TestCase):
    """The end the reporter feels: the store's lang after a connect."""

    def setUp(self):
        self._saved = dict(SessionManager.sessions)
        self._saved_bus = SessionManager.bus
        # the orchestrator's store, as an nl-NL deployment holds it
        store = Session("default")
        store.lang = "nl-NL"
        SessionManager.sessions = {"default": store}
        SessionManager.bus = None  # sync() must not broadcast from a test

    def tearDown(self):
        SessionManager.sessions = self._saved
        SessionManager.bus = self._saved_bus

    def _client_with_its_own_default(self, lang):
        """A client whose process resolved a different default lang."""
        client = MessageBusClient()
        client.client = MagicMock()
        client.client.send = MagicMock()
        own = Session("default")
        own.lang = lang
        client._own_session = lambda: own
        return client

    def test_a_connect_sends_nothing_at_all(self):
        client = self._client_with_its_own_default("en-US")
        client.on_open()
        client.flush()
        self.assertEqual(
            client.client.send.call_count, 0,
            f"connect put {client.client.send.call_args_list} on the wire; "
            f"§2.7 needs no bootstrap request")

    def test_the_store_keeps_its_lang_across_a_connect(self):
        """The assertion that fails on dev: there the connect emits a sync
        stamped en-US, the orchestrator folds it, and the store moves."""
        client = self._client_with_its_own_default("en-US")
        client.on_open()
        client.flush()

        # whatever the connect did emit, feed it to the orchestrator's handler
        # exactly as a real core would receive it
        for call in client.client.send.call_args_list:
            SessionManager.handle_session_sync(
                Message.deserialize(call[0][0]))

        self.assertEqual(SessionManager.get_default_session().lang, "nl-NL")

    def test_the_control_the_handler_still_folds_a_declared_session(self):
        """The negative control, so the test above cannot pass by the handler
        having been broken: a DECLARED default session still lands, which is
        how the orchestrator's own broadcast works (§5.1)."""
        snap = Session("default")
        snap.lang = "pt-PT"
        SessionManager.handle_session_sync(
            Message("ovos.session.sync", {"session": snap.serialize()}))
        self.assertEqual(SessionManager.get_default_session().lang, "pt-PT")

    def test_a_reconnect_does_not_move_it_either(self):
        """The reporter's trigger was a media request, which is when a
        service restarts and RE-connects. Ten connects, still nl-NL."""
        client = self._client_with_its_own_default("en-US")
        for _ in range(10):
            client.on_open()
            client.flush()
            for call in client.client.send.call_args_list:
                SessionManager.handle_session_sync(
                    Message.deserialize(call[0][0]))
            client.client.send.reset_mock()
        self.assertEqual(SessionManager.get_default_session().lang, "nl-NL")


if __name__ == "__main__":
    unittest.main()
