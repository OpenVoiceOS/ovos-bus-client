"""OVOS-SESSION-2 §5.1 — an arriving default session merges into the store.

The receive path hands the raw Message to ``SessionManager.fold_inbound``, so a
field the message omits leaves the stored value standing. That merge only
exists once the registry offers an arrival point; older spec-tools releases
fold lazily inside ``get`` and these tests do not apply to them.
"""
import unittest
from unittest.mock import MagicMock

from ovos_bus_client.client.client import MessageBusClient
from ovos_bus_client.message import Message
from ovos_bus_client.session import SessionManager, HAS_FOLD_INBOUND


def _inbound(carrier):
    return Message("recognizer_loop:utterance", {"utterances": ["hello"]},
                   {"session": carrier}).serialize()


@unittest.skipUnless(HAS_FOLD_INBOUND,
                     "spec-tools registry has no fold_inbound arrival point")
class TestDefaultSessionFoldOnReceive(unittest.TestCase):
    def setUp(self):
        SessionManager.reset_default_session()
        self.bus = MessageBusClient()
        self.bus.client = MagicMock()
        self.bus.connected_event.set()

    def tearDown(self):
        SessionManager.reset_default_session()

    def test_omitted_fields_survive_the_next_arrival(self):
        self.bus.on_message(_inbound({"session_id": "default",
                                      "lang": "pt-pt",
                                      "site_id": "kitchen"}))
        stored = SessionManager.get_default_session()
        self.assertEqual(stored.lang, "pt-PT")
        self.assertEqual(stored.site_id, "kitchen")

        # a minimal second arrival carries no lang and no site_id, so §5.1
        # leaves both stored values alone
        self.bus.on_message(_inbound({"session_id": "default"}))
        self.assertIs(SessionManager.get_default_session(), stored)
        self.assertEqual(stored.lang, "pt-PT")
        self.assertEqual(stored.site_id, "kitchen")

    def test_carried_field_replaces_the_stored_one(self):
        self.bus.on_message(_inbound({"session_id": "default",
                                      "lang": "pt-pt",
                                      "site_id": "kitchen"}))
        self.bus.on_message(_inbound({"session_id": "default",
                                      "site_id": "bedroom"}))
        stored = SessionManager.get_default_session()
        self.assertEqual(stored.site_id, "bedroom")
        self.assertEqual(stored.lang, "pt-PT")

    def test_absent_carrier_is_the_default_session(self):
        self.bus.on_message(_inbound({"session_id": "default",
                                      "site_id": "kitchen"}))
        raw = Message("recognizer_loop:utterance",
                      {"utterances": ["hello"]}).serialize()
        self.bus.on_message(raw)
        self.assertEqual(SessionManager.get_default_session().site_id,
                         "kitchen")

    def test_named_session_leaves_the_default_store_alone(self):
        self.bus.on_message(_inbound({"session_id": "default",
                                      "site_id": "kitchen"}))
        self.bus.on_message(_inbound({"session_id": "kitchen-sat",
                                      "site_id": "bedroom"}))
        self.assertEqual(SessionManager.get_default_session().site_id,
                         "kitchen")


if __name__ == "__main__":
    unittest.main()
