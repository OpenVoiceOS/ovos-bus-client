"""Coverage tests for ovos_bus_client.message — reply/forward/response,
CollectionMessage, GUIMessage, dig_for_message edge cases."""
import unittest
from unittest import TestCase

from ovos_bus_client.message import CollectionMessage, GUIMessage, Message, dig_for_message


class TestMessageEquality(TestCase):
    def test_eq_true(self):
        a = Message("t", {"x": 1}, {"y": 2})
        b = Message("t", {"x": 1}, {"y": 2})
        self.assertEqual(a, b)

    def test_eq_false_type(self):
        self.assertNotEqual(Message("a"), Message("b"))

    def test_eq_with_non_message(self):
        self.assertFalse(Message("t") == "not a message")
        self.assertFalse(Message("t") == 42)


class TestMessageAsDict(TestCase):
    def test_as_dict_keys(self):
        m = Message("t", {"d": 1}, {"c": 2})
        d = m.as_dict
        self.assertIn("type", d)
        self.assertIn("data", d)
        self.assertIn("context", d)
        self.assertEqual(d["type"], "t")


class TestForward(TestCase):
    def test_forward_keeps_context(self):
        orig = Message("a", {"x": 1}, {"source": "S", "destination": "D"})
        fwd = orig.forward("b", {"y": 2})
        self.assertEqual(fwd.msg_type, "b")
        self.assertEqual(fwd.data, {"y": 2})
        # context preserved as-is (no source/destination swap)
        self.assertEqual(fwd.context["source"], "S")
        self.assertEqual(fwd.context["destination"], "D")

    def test_forward_default_data(self):
        fwd = Message("a", context={"k": "v"}).forward("b")
        self.assertEqual(fwd.data, {})
        self.assertEqual(fwd.context["k"], "v")


class TestReply(TestCase):
    def test_reply_swaps_source_and_destination(self):
        orig = Message("a", {}, {"source": "S", "destination": "D"})
        reply = orig.reply("a.response")
        self.assertEqual(reply.context["source"], "D")
        self.assertEqual(reply.context["destination"], "S")

    def test_reply_does_not_promote_data_destination_to_context(self):
        """The historical bus-client ``Message.reply`` quirk of
        promoting ``data['destination']`` into the new context's
        ``destination`` before the §5.2 swap was always a bug — ``data``
        is the topic's payload and ``context`` owns routing per
        OVOS-MSG-1. The quirk is gone with the spec-tools envelope; the
        swap only consults ``context.source`` / ``context.destination``."""
        orig = Message("a", {}, {"source": "S"})
        reply = orig.reply("a.response", data={"destination": "X"})
        # source was "S", destination was absent — the §5.2 swap sets
        # destination←source and leaves source unchanged (§5.2: only the
        # dst→src direction fires when the original destination is absent).
        self.assertEqual(reply.context["destination"], "S")
        self.assertEqual(reply.context["source"], "S")
        # data['destination'] sits in the payload, untouched.
        self.assertEqual(reply.data, {"destination": "X"})

    def test_reply_merges_extra_context(self):
        orig = Message("a", {}, {"k1": "v1"})
        reply = orig.reply("a.response", context={"k2": "v2"})
        self.assertEqual(reply.context["k1"], "v1")
        self.assertEqual(reply.context["k2"], "v2")


class TestResponse(TestCase):
    def test_response_appends_response_suffix(self):
        orig = Message("foo.query", {}, {"source": "S", "destination": "D"})
        resp = orig.response({"answer": 42})
        self.assertEqual(resp.msg_type, "foo.query.response")
        self.assertEqual(resp.data["answer"], 42)
        self.assertEqual(resp.context["source"], "D")


class TestDigForMessage(TestCase):
    def test_dig_returns_first_message_found(self):
        msg = Message("found")

        def inner(message):
            return dig_for_message()

        result = inner(msg)
        self.assertIs(result, msg)

    def test_dig_returns_none_if_no_message_in_stack(self):
        self.assertIsNone(dig_for_message())

    def test_dig_returns_none_for_non_message_arg(self):
        not_a_message = {"type": "speak"}  # dict, not Message

        def inner(_):
            return dig_for_message()

        self.assertIsNone(inner(not_a_message))


class TestCollectionMessage(TestCase):
    def setUp(self):
        self.orig = Message("question:query",
                            {"phrase": "what time is it"},
                            {"source": "S", "destination": "D",
                             "__collect_id__": "qid-1"})

    def test_from_message_carries_handler_and_query(self):
        cm = CollectionMessage.from_message(self.orig, "h-1", "qid-1")
        self.assertIsInstance(cm, CollectionMessage)
        self.assertEqual(cm.handler_id, "h-1")
        self.assertEqual(cm.query_id, "qid-1")
        self.assertEqual(cm.msg_type, "question:query")
        self.assertEqual(cm.data, self.orig.data)

    def test_success_response_format(self):
        cm = CollectionMessage.from_message(self.orig, "h-1", "qid-1")
        resp = cm.success({"answer": "9am", "conf": 0.9})
        self.assertEqual(resp.msg_type, "question:query.response")
        self.assertTrue(resp.data["succeeded"])
        self.assertEqual(resp.data["handler"], "h-1")
        self.assertEqual(resp.data["query"], "qid-1")
        self.assertEqual(resp.data["answer"], "9am")

    def test_success_default_data(self):
        cm = CollectionMessage.from_message(self.orig, "h-1", "qid-1")
        resp = cm.success()
        self.assertTrue(resp.data["succeeded"])

    def test_failure_response_format(self):
        cm = CollectionMessage.from_message(self.orig, "h-1", "qid-1")
        resp = cm.failure()
        self.assertEqual(resp.msg_type, "question:query.response")
        self.assertFalse(resp.data["succeeded"])
        self.assertEqual(resp.data["handler"], "h-1")

    def test_extend_response_format(self):
        cm = CollectionMessage.from_message(self.orig, "h-1", "qid-1")
        resp = cm.extend(timeout=5)
        self.assertEqual(resp.msg_type, "question:query.handling")
        self.assertEqual(resp.data["timeout"], 5)
        self.assertEqual(resp.data["query"], "qid-1")
        self.assertEqual(resp.data["handler"], "h-1")


class TestGUIMessage(TestCase):
    def test_constructor_stores_kwargs_as_data(self):
        m = GUIMessage("gui.page.show", page="foo.qml", values={"a": 1})
        self.assertEqual(m.msg_type, "gui.page.show")
        self.assertEqual(m.data["page"], "foo.qml")
        self.assertEqual(m.data["values"], {"a": 1})

    def test_serialize_roundtrip(self):
        m = GUIMessage("gui.value.set", values={"temp": 22})
        s = m.serialize()
        self.assertIsInstance(s, str)
        restored = GUIMessage.deserialize(s)
        self.assertEqual(restored.msg_type, "gui.value.set")
        self.assertEqual(restored.data["values"], {"temp": 22})


class TestMessageConstructorValidation(TestCase):
    """Malformed-input rejection. The bus-client subclass inherits
    OVOS-MSG-1's :class:`MalformedMessage` from spec-tools, which
    multi-inherits ``AssertionError`` so legacy ``except
    AssertionError`` handlers continue to catch the same conditions."""

    def test_non_dict_data_raises(self):
        with self.assertRaises(AssertionError):
            Message("t", data=["not", "a", "dict"])

    def test_non_dict_context_raises(self):
        with self.assertRaises(AssertionError):
            Message("t", context="bad")

    def test_none_data_defaults_to_dict(self):
        m = Message("t", data=None, context=None)
        self.assertEqual(m.data, {})
        self.assertEqual(m.context, {})


if __name__ == "__main__":
    unittest.main()
