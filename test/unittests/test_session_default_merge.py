import unittest


class TestSessionDeserializeWirePayload(unittest.TestCase):
    """OVOS-SESSION-2 §5.1: SessionManager._store's default-session fold
    (Session.merge_from) is presence-aware only when the deserialized
    Session still carries the arrival snapshot on ``wire_payload``.
    ``Session.deserialize`` must stamp it, matching the
    ``ovos_spec_tools.session.Session.from_dict`` contract.
    """

    def test_deserialize_stamps_wire_payload(self):
        from ovos_bus_client.session import Session
        payload = {"session_id": "default", "lang": "en-us",
                   "site_id": "somewhere"}
        sess = Session.deserialize(payload)
        self.assertEqual(sess.wire_payload, payload)

    def test_deserialize_empty_payload_still_stamped(self):
        from ovos_bus_client.session import Session
        sess = Session.deserialize({})
        # even an empty wire payload is a real arrival snapshot (all fields
        # omitted = all fields carried-as-unset), never None
        self.assertIsNotNone(sess.wire_payload)
        self.assertEqual(sess.wire_payload, {})

    def test_from_message_stamps_wire_payload(self):
        from ovos_bus_client.session import Session
        from ovos_bus_client.message import Message
        payload = {"session_id": "default", "lang": "pt-pt"}
        msg = Message("recognizer_loop:utterance",
                      context={"session": payload})
        sess = Session.from_message(msg)
        self.assertEqual(sess.wire_payload, payload)


class TestSessionManagerDefaultSessionFieldMerge(unittest.TestCase):
    """End-to-end repro of the §5.1 defect: a minimal inbound default-session
    message must not clobber previously-stored default-session state it
    never mentioned.
    """

    def setUp(self):
        from ovos_bus_client.session import SessionManager
        SessionManager.sessions.clear()
        SessionManager.default_session = None

    def tearDown(self):
        from ovos_bus_client.session import SessionManager
        SessionManager.sessions.clear()
        SessionManager.default_session = None

    def test_minimal_default_session_message_does_not_clobber(self):
        from ovos_bus_client.session import Session, SessionManager

        first = Session.deserialize({
            "session_id": "default",
            "lang": "en-us",
            "intent_context": {"naptime:sleeping": [{"entities": [],
                                                      "metadata": {}}]},
            "location": {"city": {"name": "Lisbon"}},
            "is_speaking": True,
        })
        SessionManager.update(first)

        stored = SessionManager.sessions["default"]
        self.assertTrue(stored.is_speaking)
        self.assertIn("naptime:sleeping", stored.intent_context)
        self.assertEqual(stored.location_preferences["city"]["name"], "Lisbon")

        second = Session.deserialize({"session_id": "default", "lang": "en-us"})
        SessionManager.update(second)

        stored = SessionManager.sessions["default"]
        self.assertTrue(stored.is_speaking,
                         "is_speaking got clobbered by a minimal inbound "
                         "default-session message that never mentioned it")
        self.assertIn("naptime:sleeping", stored.intent_context,
                       "intent_context got wiped by a minimal inbound "
                       "default-session message that never mentioned it")
        self.assertEqual(stored.location_preferences["city"]["name"], "Lisbon",
                          "location_preferences got wiped by a minimal inbound "
                          "default-session message that never mentioned it")

    def test_post_deserialize_mutation_still_reaches_store(self):
        """A touch()-shape flow: deserialize, mutate a field, then hand the
        object to SessionManager.update — the mutated field must land in the
        singleton store (wire_payload stamping must not freeze the snapshot
        against later in-process mutation).
        """
        from ovos_bus_client.session import Session, SessionManager

        sess = Session.deserialize({"session_id": "default", "lang": "en-us"})
        sess.site_id = "kitchen"
        SessionManager.update(sess)

        stored = SessionManager.sessions["default"]
        self.assertEqual(stored.site_id, "kitchen")


if __name__ == "__main__":
    unittest.main()
