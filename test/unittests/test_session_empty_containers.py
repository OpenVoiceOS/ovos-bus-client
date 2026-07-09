"""Canonical collection fields must always be iterable containers in process.

The canonical parent stores an empty list/dict field as ``None`` (SESSION-1
§2.1 omit-when-empty). bus-client folds those back to ``[]`` / ``{}`` so a
``session.blacklisted_intents``-style membership test never raises
``TypeError: argument of type 'NoneType' is not iterable``.

This is an in-process guarantee about the *attribute*, not about the wire. On
the wire an empty list-valued override field is omitted, because SESSION-1 §3.4
makes ``[]`` wire-equivalent to omission — both resolve to the deployment
default at the consumer.
"""
import unittest
from unittest.mock import patch

import ovos_bus_client.session as session_module
from ovos_bus_client.session import (Session, _CANONICAL_LIST_FIELDS,
                                      _CANONICAL_DICT_FIELDS,
                                      _DEFERRABLE_LIST_FIELDS)


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

    def test_serialize_omits_empty_blacklisted_intents(self):
        # SESSION-1 §3.4: [] is wire-equivalent to omission, so it is dropped
        # rather than restated as the deployment default on every Message.
        data = Session(blacklisted_intents=[]).serialize()
        self.assertNotIn("blacklisted_intents", data)

    def test_deserialize_restores_empty_list_not_none(self):
        data = Session().serialize()
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


class TestEmitSessionDefaultsOptIn(unittest.TestCase):
    """Lean wire by default; opt in to spell out the deferrable fields."""

    @staticmethod
    def _empty_session():
        sess = Session("emit-defaults")
        for name in _DEFERRABLE_LIST_FIELDS:
            setattr(sess, name, [])
        return sess

    def test_default_is_lean(self):
        with patch.dict("os.environ", {}, clear=False):
            data = self._empty_session().serialize()
        for name in _DEFERRABLE_LIST_FIELDS:
            self.assertNotIn(name, data)

    def test_env_var_opts_in(self):
        with patch.dict("os.environ", {"OVOS_SESSION_EMIT_DEFAULTS": "true"}):
            data = self._empty_session().serialize()
        for name in _DEFERRABLE_LIST_FIELDS:
            self.assertEqual(data[name], [], name)

    def test_env_var_off_value_stays_lean(self):
        with patch.dict("os.environ", {"OVOS_SESSION_EMIT_DEFAULTS": "false"}):
            data = self._empty_session().serialize()
        for name in _DEFERRABLE_LIST_FIELDS:
            self.assertNotIn(name, data)

    def test_config_key_opts_in(self):
        with patch.dict("os.environ", {}, clear=True), \
                patch.object(session_module, "Configuration",
                             lambda: {"session": {"emit_defaults": True}}):
            data = self._empty_session().serialize()
        for name in _DEFERRABLE_LIST_FIELDS:
            self.assertEqual(data[name], [], name)

    def test_env_var_overrides_config(self):
        with patch.dict("os.environ", {"OVOS_SESSION_EMIT_DEFAULTS": "0"}), \
                patch.object(session_module, "Configuration",
                             lambda: {"session": {"emit_defaults": True}}):
            data = self._empty_session().serialize()
        for name in _DEFERRABLE_LIST_FIELDS:
            self.assertNotIn(name, data)

    def test_both_modes_deserialize_identically(self):
        lean = self._empty_session().serialize()
        with patch.dict("os.environ", {"OVOS_SESSION_EMIT_DEFAULTS": "1"}):
            verbose = self._empty_session().serialize()
        self.assertNotEqual(lean.keys(), verbose.keys())
        for name in _DEFERRABLE_LIST_FIELDS:
            self.assertEqual(getattr(Session.deserialize(lean), name),
                             getattr(Session.deserialize(verbose), name), name)

    def test_populated_values_emit_regardless(self):
        sess = Session("diverging")
        sess.blacklisted_intents = ["a.skill:some.intent"]
        with patch.dict("os.environ", {}, clear=False):
            data = sess.serialize()
        self.assertEqual(data["blacklisted_intents"], ["a.skill:some.intent"])


if __name__ == "__main__":
    unittest.main()
