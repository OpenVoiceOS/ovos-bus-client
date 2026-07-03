"""Canonical collection fields must always be iterable containers.

The canonical parent stores an empty list/dict field as ``None`` (SESSION-1
§2.1 omit-when-empty). bus-client folds those back to ``[]`` / ``{}`` so a
``session.blacklisted_intents``-style membership test never raises
``TypeError: argument of type 'NoneType' is not iterable``, honouring the
bidirectional-wire contract: tolerate ``None`` inbound, always present a
container outbound.
"""
import unittest

from ovos_bus_client.session import (Session, _CANONICAL_LIST_FIELDS,
                                      _CANONICAL_DICT_FIELDS)


class TestEmptyContainerNormalization(unittest.TestCase):
    def test_constructor_none_becomes_empty_list(self):
        sess = Session(blacklisted_intents=None)
        self.assertEqual(sess.blacklisted_intents, [])
        self.assertNotIn("anything", sess.blacklisted_intents)

    def test_constructor_empty_list_stays_empty_list(self):
        sess = Session(blacklisted_intents=[])
        self.assertEqual(sess.blacklisted_intents, [])

    def test_default_session_has_iterable_list_fields(self):
        sess = Session()
        for name in _CANONICAL_LIST_FIELDS:
            value = getattr(sess, name)
            self.assertIsInstance(value, list, name)
            # membership on a default field must never raise
            self.assertNotIn("x", value)

    def test_default_session_has_dict_fields(self):
        sess = Session()
        for name in _CANONICAL_DICT_FIELDS:
            self.assertIsInstance(getattr(sess, name), dict, name)

    def test_serialize_emits_empty_list_for_blacklisted_intents(self):
        data = Session(blacklisted_intents=[]).serialize()
        self.assertEqual(data["blacklisted_intents"], [])

    def test_deserialize_restores_empty_list_not_none(self):
        data = Session().serialize()
        self.assertEqual(data["blacklisted_intents"], [])
        sess = Session.deserialize(data)
        self.assertEqual(sess.blacklisted_intents, [])
        self.assertNotIn("x", sess.blacklisted_intents)

    def test_deserialize_null_wire_value_folds_to_empty_list(self):
        data = Session().serialize()
        data["blacklisted_intents"] = None
        sess = Session.deserialize(data)
        self.assertEqual(sess.blacklisted_intents, [])
        self.assertNotIn("x", sess.blacklisted_intents)

    def test_deserialize_missing_key_folds_to_empty_list(self):
        data = Session().serialize()
        data.pop("blacklisted_intents", None)
        sess = Session.deserialize(data)
        self.assertEqual(sess.blacklisted_intents, [])

    def test_roundtrip_is_idempotent(self):
        first = Session.deserialize(Session().serialize())
        second = Session.deserialize(first.serialize())
        self.assertEqual(first.blacklisted_intents, [])
        self.assertEqual(second.blacklisted_intents, [])
        self.assertNotIn("x", second.blacklisted_intents)

    def test_sibling_list_fields_roundtrip_to_list(self):
        data = Session().serialize()
        for name in _CANONICAL_LIST_FIELDS:
            data[name] = None
        sess = Session.deserialize(data)
        for name in _CANONICAL_LIST_FIELDS:
            self.assertIsInstance(getattr(sess, name), list, name)
            self.assertNotIn("x", getattr(sess, name))


if __name__ == "__main__":
    unittest.main()
