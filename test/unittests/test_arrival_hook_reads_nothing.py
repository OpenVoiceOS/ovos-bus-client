"""The arrival hook checks the carrier and reads nothing else.

``MessageBusClient._take_inbound_session`` is a transport receive point.
OVOS-SESSION-2 §2.6 allows a session mutation only at a lifecycle boundary
(transformer, ``Match.updated_session``, handler invocation) and §6.1 makes
the bus stateless with respect to session. So the hook only runs the
SESSION-1 §2.5 carrier check. It builds no ``Session``, so it does not
promote ``context.lang`` into the carrier dict, and it does not read the
carried fields a second time, so one wrong-typed field logs its §2 WARN
one time per message (from the handler's ``SessionManager.get``).
"""
import copy
import logging
import unittest
from unittest.mock import MagicMock

from ovos_bus_client.client.client import MessageBusClient
from ovos_bus_client.message import Message
from ovos_bus_client.session import SessionManager


class _Records(logging.Handler):
    """Collect every OVOS-SESSION-1 WARN once, whichever logger it came by."""

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.records = []

    def emit(self, record):
        text = record.getMessage()
        if "OVOS-SESSION-1" in text and not any(r is record for r in self.records):
            self.records.append(text)


class TestArrivalHookReadsNothing(unittest.TestCase):

    def setUp(self):
        SessionManager.reset_default_session()
        self.addCleanup(SessionManager.reset_default_session)
        self.records = _Records()
        logging.getLogger().addHandler(self.records)
        self.addCleanup(logging.getLogger().removeHandler, self.records)
        self.bus = MessageBusClient()
        self.bus.client = MagicMock()
        self.bus.connected_event.set()

    def _deliver(self, topic, carrier, context_lang="pt-PT"):
        """Put one message through on_message; return what the handler saw."""
        seen = {}

        def handler(message):
            seen["carrier"] = message.context.get("session")
            seen["session"] = SessionManager.get(message)

        self.bus.on(topic, handler)
        raw = Message(topic, {"utterances": ["hello"]},
                      {"session": copy.deepcopy(carrier),
                       "lang": context_lang}).serialize()
        self.bus.on_message(raw)
        self.assertIn("carrier", seen, "handler did not run")
        return seen

    def test_one_malformed_field_logs_one_warn_record(self):
        # SESSION-1 §2: the record names the field and the wire type. The
        # count is the finding (T-2243): the hook used to read the carrier
        # once and the handler's get read it again, two records for one bug.
        seen = self._deliver("test.no.twin", {"session_id": "a", "lang": 5})
        self.assertEqual(len(self.records.records), 1, self.records.records)
        self.assertIn("`lang`", self.records.records[0])
        self.assertIn("number", self.records.records[0])
        self.assertEqual(seen["session"].session_id, "a")

    def test_inbound_carrier_dict_is_unchanged_after_delivery(self):
        # SESSION-2 §2.6 / §6.1 and SESSION-1 §3.2.7: context.lang is a
        # signal the transport reads and MUST NOT write into the carrier.
        carrier = {"session_id": "a", "site_id": "kitchen"}
        seen = self._deliver("test.no.twin", carrier, context_lang="pt-PT")
        self.assertEqual(seen["carrier"], carrier)
        self.assertNotIn("lang", seen["carrier"])
        # the handler still resolves a Session; the language comes from the
        # §3.2.7 precedence in SessionManager.get, not from a carrier write
        self.assertEqual(seen["session"].session_id, "a")
        self.assertEqual(self.records.records, [])

    def test_named_session_arrival_touches_no_store(self):
        stored = SessionManager.get_default_session()
        stored.site_id = "kitchen"
        self._deliver("test.no.twin", {"session_id": "a", "site_id": "bedroom"})
        self.assertIs(SessionManager.get_default_session(), stored)
        self.assertEqual(stored.site_id, "kitchen")

    def test_wrong_typed_session_id_logs_one_record_on_a_plain_topic(self):
        # the hook no longer resolves the id, so the only §2 record left on
        # an unmigrated topic is the handler's own get
        self._deliver("test.no.twin", {"session_id": 7})
        self.assertEqual(len(self.records.records), 1, self.records.records)
        self.assertIn("`session_id`", self.records.records[0])

    def test_wrong_typed_session_id_on_a_migrated_topic_is_two_records(self):
        # Accepted, not fixed here: recognizer_loop:utterance has a namespace
        # twin, and Message.forward (ovos-spec-tools) stamps the derived
        # frame through resolve_session_id. That is a second consumer act on
        # a derived Message, and §2 puts the WARN on each consumer. The
        # arrival hook itself contributes none of the two.
        self._deliver("recognizer_loop:utterance", {"session_id": 7})
        self.assertEqual(len(self.records.records), 2, self.records.records)
        for text in self.records.records:
            self.assertIn("`session_id`", text)


if __name__ == "__main__":
    unittest.main()
