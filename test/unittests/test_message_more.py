"""More message.py coverage — _json_dump, _json_load, serialize variants."""
import json
import unittest
from unittest import TestCase

from ovos_bus_client.message import (CollectionMessage, GUIMessage, Message,
                                     dig_for_message)
from ovos_bus_client.session import Session


class _HasSerialize:
    def serialize(self):
        return {"serialized": True}


class TestJsonDump(TestCase):
    def test_serialize_handles_object_with_serialize_method(self):
        m = Message("t", {"obj": _HasSerialize()})
        # serialize should call .serialize() on nested objects
        s = m.serialize()
        decoded = json.loads(s)
        self.assertEqual(decoded["data"]["obj"], {"serialized": True})

    def test_serialize_handles_nested_lists(self):
        m = Message("t", {"items": [_HasSerialize(), "plain", 1]})
        decoded = json.loads(m.serialize())
        self.assertEqual(decoded["data"]["items"][0], {"serialized": True})
        self.assertEqual(decoded["data"]["items"][1], "plain")

    def test_serialize_handles_nested_dict(self):
        m = Message("t", {"outer": {"inner": _HasSerialize()}})
        decoded = json.loads(m.serialize())
        self.assertEqual(decoded["data"]["outer"]["inner"], {"serialized": True})

    def test_serialize_session_object(self):
        s = Session("sid")
        m = Message("t", {}, {"session": s})
        decoded = json.loads(m.serialize())
        self.assertEqual(decoded["context"]["session"]["session_id"], "sid")


class TestDeserializePayloadTypes(TestCase):
    """The legacy private ``Message._json_load`` is gone with the
    spec-tools subclass. The public :meth:`Message.deserialize` covers
    the same payload shapes (``str``, ``dict``, ``bytes``) and rejects
    malformed JSON or a non-object root — the rejection still raises
    a class that catches as ``AssertionError`` for back-compat."""

    def test_accepts_string_payload(self):
        m = Message.deserialize('{"type": "t", "data": {}, "context": {}}')
        self.assertEqual(m.msg_type, "t")

    def test_accepts_dict_payload(self):
        d = {"type": "t", "data": {}, "context": {}}
        m = Message.deserialize(d)
        self.assertEqual(m.msg_type, "t")

    def test_rejects_non_object_root(self):
        with self.assertRaises(AssertionError):
            Message.deserialize('[1, 2, 3]')


class TestDeserialize(TestCase):
    def test_deserialize_full_message(self):
        m = Message("speak", {"utterance": "hi"}, {"k": "v"})
        restored = Message.deserialize(m.serialize())
        self.assertEqual(restored.msg_type, "speak")
        self.assertEqual(restored.data["utterance"], "hi")
        self.assertEqual(restored.context["k"], "v")

    def test_deserialize_missing_keys_defaults(self):
        m = Message.deserialize('{"type": "x"}')
        self.assertEqual(m.msg_type, "x")
        self.assertEqual(m.data, {})
        self.assertEqual(m.context, {})


class TestCollectionMessageContextHandling(TestCase):
    def test_success_with_explicit_context(self):
        cm = CollectionMessage("t", "h1", "q1", {}, {"k": "v"})
        resp = cm.success({"x": 1}, context={"new": "ctx"})
        # explicit context takes precedence — note that .reply still merges
        self.assertIn("new", resp.context)


class TestGUIMessageDeserialize(TestCase):
    def test_deserialize_constructs_gui_message(self):
        m = GUIMessage("gui.show", page="X")
        s = m.serialize()
        restored = GUIMessage.deserialize(s)
        self.assertEqual(restored.msg_type, "gui.show")


class TestDigForMessageEdgeCases(TestCase):
    def test_dig_with_message_in_local_var(self):
        msg = Message("found")

        def helper():
            return dig_for_message()

        # call helper inside a function that has 'message' local
        def caller(message):
            return helper()

        self.assertIs(caller(msg), msg)


if __name__ == "__main__":
    unittest.main()
