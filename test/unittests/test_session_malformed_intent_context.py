"""A malformed intent context costs that field, not the whole session.

The intent-context parser used to raise its own ``AttributeError``,
``TypeError`` and ``ValueError`` out of ``Session.deserialize``. Those escaped
every caller written for that function -- ``SessionManager``'s
``ovos.session.sync`` handler among them -- and surfaced on the bus client's
receive thread, so one peer sending one bad frame could take the reader down.

Catching them is still the point. Rejecting the whole carrier for it was not:
OVOS-SESSION-1 §2.5 is field-by-field, and every other field in this
deserializer already honours that -- a bad ``site_id``, ``pipeline`` or
``active_skills`` logs "treating as omitted" and the session survives with its
``session_id`` and ``lang``. ``context`` was the sole exception, so a peer with
one bad frame stack also cost the reader a perfectly good session identity.
"""
import json
import unittest

from ovos_bus_client.message import Message
from ovos_bus_client.session import (IntentContextManager,
                                     MalformedSession, Session,
                                     SessionManager)


_ENTITY = {"data": [["value", "key"]], "key": "key", "confidence": 1.0}


class TestMalformedIntentContext(unittest.TestCase):
    # Each of these makes the parser raise a DIFFERENT builtin, which is why
    # the guard names three rather than catching the one that was noticed.
    MALFORMED = (
        ("context is not an object", {"context": 5}),
        ("context is a list", {"context": []}),
        ("frame stack is not a list", {"context": {"frame_stack": "nope"}}),
        # "" and {} are iterable, so the parser read them as an empty stack and
        # returned a perfectly valid Session for a payload that is not one.
        ("frame stack is an empty string", {"context": {"frame_stack": ""}}),
        ("frame stack is an empty mapping", {"context": {"frame_stack": {}}}),
        ("frame is not a pair", {"context": {"frame_stack": [["x", 1, 2]]}}),
        ("frame is not a mapping", {"context": {"frame_stack": [["x", 1]]}}),
        ("frame entities are not a list",
         {"context": {"frame_stack": [[{"entities": "nope"}, 1]]}}),
        # the one that used to reach Session.__init__ and raise there, outside
        # every handler written for deserialize
        ("an entity is not a mapping",
         {"context": {"frame_stack": [[{"entities": ["nope"]}, 1]]}}),
        ("frame stack item is a bare mapping",
         {"context": {"frame_stack": [{"entities": "nope"}]}}),
        # the timestamp only reaches the fold's arithmetic when the frame has
        # an entity it can actually map, so these carry a valid one
        ("frame timestamp is a string",
         {"context": {"frame_stack": [[{"entities": [_ENTITY]}, "nope"]]}}),
        ("frame timestamp is a mapping",
         {"context": {"frame_stack": [[{"entities": [_ENTITY]}, {}]]}}),
    )

    def test_a_malformed_context_is_omitted_and_the_session_survives(self):
        """Neither an escaping builtin nor a discarded session."""
        for name, payload in self.MALFORMED:
            with self.subTest(name):
                carrier = dict(payload, session_id="s-1", lang="en-us")
                session = Session.deserialize(carrier)
                self.assertEqual(session.session_id, "s-1",
                                 "a good session_id was thrown away")
                self.assertEqual(session.lang, "en-US",
                                 "a good lang was thrown away")
                self.assertEqual(len(session.context.frame_stack), 0,
                                 "the malformed context was not omitted")

    def test_no_parser_builtin_escapes(self):
        """The original defect: these reached the receive thread."""
        for name, payload in self.MALFORMED:
            with self.subTest(name):
                try:
                    Session.deserialize(payload)
                except (AttributeError, TypeError, ValueError) as error:
                    self.fail(f"{type(error).__name__} escaped for {name}: {error}")

    def test_a_null_or_numeric_timestamp_is_accepted(self):
        """The fold substitutes now for None and adds the timeout to a number,
        so both are well-formed and must not be rejected."""
        for timestamp in (None, 0, 1789789436, 1789789436.5):
            with self.subTest(timestamp=timestamp):
                self.assertEqual(
                    Session.deserialize({
                        "session_id": "ts",
                        "context": {"frame_stack": [[{"entities": [_ENTITY]},
                                                     timestamp]]},
                    }).session_id, "ts")

    def test_a_non_object_context_is_omitted_too(self):
        session = Session.deserialize({"session_id": "s-2", "context": 5})
        self.assertEqual(session.session_id, "s-2")
        self.assertEqual(len(session.context.frame_stack), 0)

    def test_a_malformed_carrier_itself_is_still_rejected(self):
        """Field tolerance is not carrier tolerance: §2.5 still rejects a
        session that is not an object at all, and callers rely on that."""
        with self.assertRaises(MalformedSession):
            Session.deserialize("not-a-session")

    def test_a_canonical_round_trip_still_parses(self):
        """Whatever serialize() emits must deserialize; the shape check is
        written against that output, so this is the regression that matters."""
        manager = IntentContextManager()
        # inject_context takes an ENTITY mapping, and wraps it in a frame
        manager.inject_context({"data": [["value", "key"]], "key": "key",
                                "confidence": 1.0})
        payload = {"session_id": "rt", "context": manager.serialize()}
        self.assertEqual(Session.deserialize(payload).session_id, "rt")
        # and again through JSON, where the (frame, ts) tuples become lists
        self.assertEqual(
            Session.deserialize(json.loads(json.dumps(payload))).session_id,
            "rt")

    def test_an_explicit_null_context_is_absent_not_malformed(self):
        """A serializer that writes the key as null rather than omitting it is
        not sending a malformed session, and must not be rejected as one."""
        self.assertEqual(
            Session.deserialize({"session_id": "null", "context": None}).session_id,
            "null")

    def test_a_well_formed_session_is_untouched(self):
        self.assertEqual(
            Session.deserialize({"session_id": "abc", "context": {}}).session_id,
            "abc")
        self.assertEqual(
            Session.deserialize({"session_id": "bare"}).session_id, "bare")
        self.assertEqual(
            Session.deserialize(
                {"session_id": "empty", "context": {"frame_stack": []}}
            ).session_id, "empty")

    def test_the_sync_handler_survives_a_bad_peer_frame(self):
        """The handler already catches MalformedSession; this is what makes
        that catch reachable for a malformed context."""
        for name, payload in self.MALFORMED:
            with self.subTest(name):
                SessionManager.handle_session_sync(
                    Message("ovos.session.sync", {"session": payload}))


if __name__ == "__main__":
    unittest.main()
