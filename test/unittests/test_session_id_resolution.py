"""SESSION-1 §2/§3.1/§6 — resolving a raw carrier's ``session_id`` locally.

``resolve_session_id`` is the public ``ovos_spec_tools.session`` resolver and
must apply the non-empty-string rule §6 requires: a wrong-typed value is
malformed and reads as omitted (§2.1), and an omitted id names the default
session (§3.1).
"""
import unittest
from unittest.mock import MagicMock

from ovos_bus_client.client.client import MessageBusClient
from ovos_bus_client.message import Message
from ovos_bus_client.session import (DEFAULT_SESSION_ID, SessionManager,
                                     resolve_session_id)


class TestResolveSessionId(unittest.TestCase):

    def test_absent_and_unusable_ids_resolve_to_the_default(self):
        for carrier in ({}, {"session_id": None}, {"session_id": ""},
                         {"session_id": "default"}, {"session_id": 123},
                         {"session_id": 1.5}, {"session_id": True},
                         {"session_id": []}, {"session_id": {}}):
            self.assertEqual(resolve_session_id(carrier), DEFAULT_SESSION_ID,
                              f"{carrier!r} should resolve to the default")

    def test_a_named_string_id_resolves_to_itself(self):
        self.assertEqual(resolve_session_id({"session_id": "abc"}), "abc")


class TestWrongTypedIdOnTheWireIsTheDefaultSession(unittest.TestCase):
    """A carrier naming a wrong-typed id must dispatch as the default session,
    not be treated as a distinct named session (which minted a fresh uuid4 /
    dropped the message entirely before this fix)."""

    def setUp(self):
        SessionManager.reset_default_session()
        self.bus = MessageBusClient()
        self.bus.client = MagicMock()
        self.bus.connected_event.set()

    def tearDown(self):
        SessionManager.reset_default_session()

    def test_int_session_id_dispatches_as_the_default_session(self):
        before = set(SessionManager.sessions.keys())
        seen = []
        self.bus.on("speak", seen.append)
        raw = Message("speak", {"utterance": "hi"},
                      {"session": {"session_id": 123,
                                   "lang": "pt-pt"}}).serialize()
        self.bus.on_message(raw)

        self.assertEqual(len(seen), 1, "the message must still dispatch")
        self.assertEqual(SessionManager.get_default_session().session_id,
                          DEFAULT_SESSION_ID)
        self.assertEqual(set(SessionManager.sessions.keys()), before,
                          "no spurious session may be minted for a "
                          "wrong-typed session_id")


if __name__ == "__main__":
    unittest.main()
