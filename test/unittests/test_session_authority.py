"""OVOS-SESSION-2 §2.2 / §2.5 — who is authoritative for which session.

The default session is the orchestrator's store and this process is
authoritative for it. Every named session is client-owned: the client that
holds it is the authority, the orchestrator keeps no state for it, and a named
session seen in passing belongs to somebody else.
"""
import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

import ovos_bus_client.session as session_module
from ovos_bus_client.client.client import MessageBusClient
from ovos_bus_client.message import Message
from ovos_bus_client.session import (Session, SessionManager, HAS_FOLD_INBOUND,
                                     DEFAULT_SESSION_ID, resolve_session_id,
                                     session_carrier)


def _client(session=None):
    bus = MessageBusClient(session=session)
    bus.client = MagicMock()
    bus.connected_event.set()
    return bus


class TestNamedClientEmitsItsOwnSession(unittest.TestCase):
    """§2.5/§6.4 — a client emits the named session it holds, not a stand-in."""

    def setUp(self):
        SessionManager.reset_default_session()

    def tearDown(self):
        SessionManager.reset_default_session()
        SessionManager.sessions.pop("kitchen-sat", None)

    def test_named_session_round_trips_onto_the_wire(self):
        held = Session("kitchen-sat", lang="pt-PT")
        held.site_id = "kitchen"
        bus = _client(held)

        msg = Message("speak", {"utterance": "ola"})
        bus.emit(msg)

        carrier = Session.deserialize(msg.context["session"])
        self.assertEqual(carrier.session_id, "kitchen-sat")
        self.assertEqual(carrier.lang, "pt-PT")
        self.assertEqual(carrier.site_id, "kitchen")

    def test_default_client_emits_the_live_store(self):
        bus = _client()
        SessionManager.get_default_session().site_id = "hallway"
        msg = Message("speak", {"utterance": "hi"})
        bus.emit(msg)
        self.assertEqual(
            Session.deserialize(msg.context["session"]).site_id, "hallway")


class TestFalsySessionIdIsTheDefaultSession(unittest.TestCase):
    """SESSION-1 §3.1 — a carrier naming no usable id IS the default session."""

    def setUp(self):
        SessionManager.reset_default_session()
        self.bus = _client()

    def tearDown(self):
        SessionManager.reset_default_session()

    def test_predicate_reads_unusable_ids_as_the_default(self):
        self.assertEqual(resolve_session_id({}), DEFAULT_SESSION_ID)
        self.assertEqual(resolve_session_id({"session_id": "default"}),
                          DEFAULT_SESSION_ID)
        self.assertNotEqual(resolve_session_id({"session_id": "kitchen"}),
                            DEFAULT_SESSION_ID)
        if HAS_FOLD_INBOUND:
            for unusable in ("", 0, False, [], {}):
                self.assertEqual(resolve_session_id({"session_id": unusable}),
                                 DEFAULT_SESSION_ID,
                                 f"{unusable!r} names no session")

    @unittest.skipUnless(HAS_FOLD_INBOUND, "no §3.1 predicate in the registry")
    def test_empty_id_folds_into_the_store_and_still_dispatches(self):
        seen = []
        self.bus.on("speak", seen.append)
        raw = Message("speak", {"utterance": "hi"},
                      {"session": {"session_id": "", "lang": "pt-pt"}}).serialize()
        self.bus.on_message(raw)
        self.assertEqual(SessionManager.get_default_session().lang, "pt-PT")
        self.assertEqual(len(seen), 1)

    def test_malformed_carrier_is_still_rejected(self):
        with self.assertRaises(session_module.MalformedSession):
            session_carrier(Message("speak", {}, {"session": "not-an-object"}))


class TestNamedSessionLeavesNoOrchestratorState(unittest.TestCase):
    def setUp(self):
        SessionManager.reset_default_session()
        SessionManager.bus = None

    def tearDown(self):
        SessionManager.bus = None
        SessionManager.sessions.pop("someone-else", None)

    def test_unheld_named_session_is_not_held(self):
        self.assertIsNone(SessionManager.held_session("someone-else"))

    def test_the_clients_own_named_session_is_held(self):
        held = Session("mine")
        SessionManager.bus = SimpleNamespace(session=held)
        self.assertIs(SessionManager.held_session("mine"), held)

    def test_the_default_session_is_always_held(self):
        self.assertIs(SessionManager.held_session("default"),
                      SessionManager.get_default_session())


class TestGetDoesNotTouchTheMessage(unittest.TestCase):
    """A read answers a question; it may not edit what it was asked about."""

    def test_get_leaves_the_carrier_exactly_as_it_arrived(self):
        carrier = {"session_id": "sid-read"}
        msg = Message("speak", {}, {"session": dict(carrier), "lang": "pt-PT"})
        SessionManager.get(msg)
        SessionManager.get(msg)
        self.assertEqual(msg.context["session"], carrier)

    @unittest.skipUnless(HAS_FOLD_INBOUND, "get folds on older registries")
    def test_reading_a_default_message_does_not_move_the_store(self):
        SessionManager.reset_default_session()
        SessionManager.get_default_session().site_id = "kitchen"
        msg = Message("speak", {}, {"session": {"session_id": "default"}})
        SessionManager.get(msg)
        self.assertEqual(SessionManager.get_default_session().site_id, "kitchen")


class TestGraftIsIdempotent(unittest.TestCase):
    """Re-importing the module must not stack the overrides onto themselves."""

    def test_reload_does_not_recurse(self):
        session_cls = SessionManager.session_cls
        try:
            importlib.reload(session_module)
            # the overrides still reach the registry's own implementations
            SessionManager.get(Message("speak", {}, {"session": {}}))
            SessionManager.update(Session("default"))
        finally:
            SessionManager.session_cls = session_cls


class TestTouchReplacesTheDefaultStore(unittest.TestCase):
    """Pin the sharp edge in Session.touch, for whoever owns intake next.

    ``touch`` writes the session back through ``update``. Once that write is
    OVOS-SESSION-2 §2.6's authoritative whole-replace, touching a *stale copy*
    of the default session silently drops every field the copy does not carry.
    Nothing here relies on that; core's utterance-intake work has to decide
    whether a bare timestamp bump should be a whole-store write at all.
    """

    def setUp(self):
        SessionManager.reset_default_session()

    def tearDown(self):
        SessionManager.reset_default_session()

    @unittest.skipUnless(HAS_FOLD_INBOUND, "older registries merge on write")
    def test_stale_default_copy_wipes_uncarried_fields(self):
        SessionManager.get_default_session().site_id = "kitchen"
        stale = Session.deserialize({"session_id": "default", "lang": "en-US"})
        stale.touch()
        self.assertIsNone(SessionManager.get_default_session().site_id)


if __name__ == "__main__":
    unittest.main()
