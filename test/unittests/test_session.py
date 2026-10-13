import unittest
from unittest.mock import patch
from time import time, sleep


class TestSessionModule(unittest.TestCase):
    def test_utterance_state(self):
        from ovos_bus_client.session import UtteranceState
        for state in UtteranceState:
            self.assertIsInstance(state, UtteranceState)
            self.assertIsInstance(state, str)

    def test_location_preferences_setter_empty_value_keeps_location_dict(self):
        from ovos_bus_client.session import Session
        sess = Session()
        for empty in ({}, None):
            sess.location_preferences = empty
            self.assertEqual(sess.location, {})
            # timezone falls back to deployment config and must not raise
            # AttributeError on a None `location`
            sess.timezone


class TestIntentContextManagerFrame(unittest.TestCase):
    def test_serialize_deserialize(self):
        from ovos_bus_client.session import IntentContextManagerFrame
        test_entities = [{'key': 'e1'}, {'key': 2}, {'key': 'entity'}]
        test_metadata = {'test': True,
                         'metadata': {'test': True}}
        frame = IntentContextManagerFrame(test_entities, test_metadata)
        self.assertEqual(frame.entities, test_entities)
        self.assertEqual(frame.metadata, test_metadata)
        serialized = frame.serialize()
        self.assertEqual(serialized, {'entities': test_entities,
                                      'metadata': test_metadata})

        new_frame = IntentContextManagerFrame.deserialize(serialized)
        new_serialized = new_frame.serialize()
        self.assertEqual(serialized, new_serialized)

    def test_metadata_matches(self):
        from ovos_bus_client.session import IntentContextManagerFrame
        # TODO

    def test_merge_context(self):
        from ovos_bus_client.session import IntentContextManagerFrame
        # TODO


class TestIntentContextManager(unittest.TestCase):
    from ovos_bus_client.session import IntentContextManager
    context_manager = IntentContextManager()

    def test_init(self):
        from ovos_bus_client.session import IntentContextManager
        context_manager = IntentContextManager()
        self.assertEqual(context_manager.frame_stack, list())
        self.assertIsInstance(context_manager.timeout, int)
        self.assertIsInstance(context_manager.context_keywords, list)
        self.assertIsInstance(context_manager.context_max_frames, int)
        self.assertIsInstance(context_manager.context_greedy, bool)
        self.assertNotEqual(context_manager, self.context_manager)

    def test_serialize_deserialize(self):
        from ovos_bus_client.session import IntentContextManagerFrame, \
            IntentContextManager

        # Serialize with a frame
        self.context_manager.frame_stack.insert(0, (IntentContextManagerFrame(),
                                                    time()))
        serialized = self.context_manager.serialize()
        self.assertEqual(serialized['timeout'], self.context_manager.timeout)
        self.assertEqual(len(serialized['frame_stack']),
                         len(self.context_manager.frame_stack))
        for frame in serialized['frame_stack']:
            self.assertIsInstance(frame[0], dict)
            self.assertIsInstance(frame[1], float)

        # Times and serialized frames should be equal
        new_manager = IntentContextManager.deserialize(serialized)
        self.assertEqual(new_manager.frame_stack[0][0].serialize(),
                         self.context_manager.frame_stack[0][0].serialize())
        self.assertEqual(new_manager.frame_stack[0][1],
                         self.context_manager.frame_stack[0][1])

    def test_update_context(self):
        # TODO
        pass

    def test_clear_context(self):
        # TODO
        pass

    def test_remove_context(self):
        # TODO
        pass

    def test_inject_context(self):
        # TODO
        pass

    def test_strip_result(self):
        # TODO
        pass

    def test_get_context(self):
        # TODO
        pass


class TestSession(unittest.TestCase):
    from ovos_bus_client.session import Session
    session = Session()

    def test_init(self):
        from ovos_bus_client.session import Session, IntentContextManager
        session = Session()
        self.assertIsInstance(session.session_id, str)
        self.assertIsInstance(session.lang, str)
        self.assertEqual(session.active_skills, list())
        self.assertEqual(session.utterance_states, dict())
        self.assertIsInstance(session.touch_time, int)
        self.assertIsInstance(session.expiration_seconds, int)
        self.assertIsInstance(session.context, IntentContextManager)

        self.assertNotEqual(session, self.session)

    def test_active(self):
        self.session.active_skills = []
        self.assertFalse(self.session.active)
        self.session.active_skills = [["test_skill", time()]]
        self.assertTrue(self.session.active)

    def test_touch(self):
        sleep(1)  # Make sure touch time is older than current time
        old_time = int(self.session.touch_time)
        self.session.touch()
        self.assertGreater(self.session.touch_time, old_time)

    def test_expired(self):
        self.session.touch()
        self.session.expiration_seconds = -1
        sleep(1)
        self.assertFalse(self.session.expired())
        self.session.expiration_seconds = 5
        self.assertFalse(self.session.expired())
        self.session.expiration_seconds = 0
        self.assertTrue(self.session.expired())
        self.session.expiration_seconds = -1
        self.assertFalse(self.session.expired())

    def test_enable_response_mode(self):
        # TODO
        pass

    def test_disable_response_mode(self):
        # TODO
        pass

    def test_activate_skill(self):
        # TODO
        pass

    def test_deactivate_skill(self):
        # TODO
        pass

    def test_is_active(self):
        # TODO
        pass

    def test_clear(self):
        # TODO
        pass

    def test_serialize_deserialize(self):
        from ovos_bus_client.session import Session, IntentContextManager

        # Simple session serialize/deserialize
        test_session = Session()
        serialized = test_session.serialize()
        self.assertIsInstance(serialized, dict)
        new_session = Session.deserialize(serialized)
        self.assertIsInstance(new_session, Session)
        new_serial = new_session.serialize()
        ctx = serialized.pop('context')
        new_ctx = new_serial.pop('context')
        self.assertEqual(new_serial, serialized)
        self.assertEqual(ctx['frame_stack'], new_ctx['frame_stack'])
        self.assertEqual(new_ctx['timeout'], ctx['timeout'])

        # Test default value deserialize
        test_session = Session.deserialize(dict())
        self.assertIsInstance(test_session, Session)
        self.assertIsInstance(test_session.session_id, str)
        self.assertIsInstance(test_session.lang, str)
        self.assertIsInstance(test_session.active_skills, list)
        self.assertIsInstance(test_session.utterance_states, dict)
        self.assertIsInstance(test_session.touch_time, int)
        self.assertIsInstance(test_session.expiration_seconds, int)
        self.assertIsInstance(test_session.context, IntentContextManager)
        serialized = test_session.serialize()
        self.assertIsInstance(serialized, dict)
        self.assertIsInstance(serialized['context'], dict)

    def test_from_message(self):
        from ovos_bus_client.session import (Session, SessionManager,
                                             MalformedSession)
        from ovos_bus_client.message import Message

        # a well-formed session deserializes identically to a direct call
        well_formed = Session("sid-wf", lang="pt-PT")
        msg = Message("test", context={"session": well_formed.serialize()})
        got = Session.from_message(msg)
        self.assertIsInstance(got, Session)
        self.assertEqual(got.session_id, "sid-wf")
        self.assertEqual(got.lang, "pt-PT")

        # SESSION-1 §2.5: a present-but-malformed (non-object) session carrier is
        # a producer error — rejected with MalformedSession, never silently
        # defaulted. The inbound handler catches this to drop the one message; it
        # is a ValueError, so it never escapes as an unhandled TypeError that
        # would tear the connection down.
        for bad in ("oops", 42, ["a", "b"]):
            msg = Message("test", context={"session": bad})
            with self.assertRaises(MalformedSession):
                Session.from_message(msg)

        # an explicit null carrier is absence, not malformation (§2.1) -> default
        msg = Message("test", context={"session": None})
        got = Session.from_message(msg)
        self.assertIsInstance(got, Session)
        self.assertEqual(got.session_id,
                         SessionManager.get_default_session().session_id)

        # a dict missing every field is well-formed (all fields omissible):
        # it deserializes without raising, the consumer filling its own
        # deployment defaults (§2.1)
        msg = Message("test", context={"session": {}})
        got = Session.from_message(msg)
        self.assertIsInstance(got, Session)
        self.assertTrue(got.session_id)

        # no session key at all -> default session
        got = Session.from_message(Message("test", context={}))
        self.assertIsInstance(got, Session)


class TestSessionManager(unittest.TestCase):
    from ovos_bus_client.session import SessionManager

    def test_prune_sessions(self):
        # TODO
        self.SessionManager.prune_sessions()

    def test_reset_default_session(self):
        from ovos_bus_client.session import Session
        session = self.SessionManager.reset_default_session()
        self.assertIsInstance(session, Session)
        self.assertEqual(session, self.SessionManager.get_default_session())
        # TODO

    def test_update_writes_the_default_store_in_place(self):
        from ovos_bus_client.session import Session
        stored = self.SessionManager.get_default_session()
        snapshot = Session("default")
        snapshot.lang = "pt-PT"
        returned = self.SessionManager.update(snapshot)
        # the store keeps its identity across a write -- components hold
        # references to it, so a write may never swap the object out
        self.assertIs(returned, stored)
        self.assertIs(self.SessionManager.get_default_session(), stored)
        self.assertEqual(stored.lang, "pt-PT")

    def test_update_hands_a_named_session_straight_back(self):
        from ovos_bus_client.session import Session
        sess = Session("sid-update")
        self.assertIs(self.SessionManager.update(sess), sess)
        # OVOS-SESSION-2 §2.2: the orchestrator keeps no state for a named
        # session, so a write records nothing and the caller keeps the object
        self.assertNotIn("sid-update", self.SessionManager.sessions)

    def test_get_resolves_the_named_session_on_the_message(self):
        from ovos_bus_client.session import Session
        from ovos_bus_client.message import Message
        sess = Session("sid-get")
        msg = Message("test", context={"session": sess.serialize()})

        first = self.SessionManager.get(msg)
        second = self.SessionManager.get(msg)
        self.assertEqual(first.session_id, "sid-get")
        self.assertEqual(second.session_id, "sid-get")
        # OVOS-SESSION-2 §2.2: the orchestrator is stateless for a named
        # session, and §2.6 makes get a pure read — it builds the session
        # the carrier describes and registers nothing. Whether repeated
        # get() calls on the same message return the identical object is
        # an implementation detail of the carrier library, not this repo.
        self.assertNotIn("sid-get", self.SessionManager.sessions)

    def test_held_reference_observes_later_mutation(self):
        # the corner case the singleton fixes: a reference taken early in a
        # flow must see a flag flipped through a later snapshot of the same id
        from ovos_bus_client.session import Session
        from ovos_bus_client.message import Message
        held = self.SessionManager.get_default_session()
        held.is_speaking = False

        speaking = Session("default")
        speaking.is_speaking = True
        self.SessionManager.update(speaking)
        # the early reference observes the mutation without being re-fetched
        self.assertTrue(held.is_speaking)
        self.assertIs(self.SessionManager.get_default_session(), held)

    def test_forward_stamps_live_bus_session(self):
        # bus-client land: get -> mutate -> forward; the derived message carries
        # the LIVE bus Session for the default id (refresh, not the pre-mutation
        # copy), because the store is the only session this process is
        # authoritative for.
        from ovos_bus_client.session import Session
        from ovos_bus_client.message import Message
        live = self.SessionManager.get_default_session()
        live.activate_skill("my.skill")
        derived = Message("utt", context={"session": {"session_id": "default"}}
                          ).forward("my.skill.activate")
        skills = [s[0] for s in
                  Session.deserialize(derived.context["session"]).active_skills]
        self.assertIn("my.skill", skills)

    def test_forward_carries_a_named_session_verbatim(self):
        # OVOS-SESSION-2 §2.5: a named session is client-owned, so the carrier
        # on the message is the only authority this process has for it
        from ovos_bus_client.session import Session
        from ovos_bus_client.message import Message
        carrier = Session("sid-named", lang="pt-PT").serialize()
        derived = Message("utt", context={"session": carrier}).forward("x")
        self.assertEqual(
            Session.deserialize(derived.context["session"]).lang, "pt-PT")

    def test_update_from_present_empty_overrides(self):
        # SESSION-1 §2: a snapshot that carries an (empty) value for a field
        # overrides the live session's value — folding is spec-deserialization,
        # not a self-preserving merge that keeps stale state.
        from ovos_bus_client.session import Session
        from ovos_bus_client.message import Message
        held = self.SessionManager.get_default_session()
        held.activate_skill("skill.foo")
        self.assertTrue(held.active_skills)

        # a snapshot that carries a value for a field replaces the stored one
        self.SessionManager.update(Session("default", lang="pt-PT"))
        self.assertEqual(held.lang, "pt-PT")
        # OVOS-SESSION-2 §2.6: a write is authoritative whole state, so the
        # skills the snapshot does not carry are gone from the store too
        self.assertEqual(held.active_skills, [])

    def test_update_from_does_not_alias_nested_state(self):
        # round-tripping through (de)serialize rebuilds nested objects, so the
        # live singleton never shares mutable sub-objects with the snapshot.
        from ovos_bus_client.session import Session
        live = Session("sid-alias")
        snapshot = Session("sid-alias")
        snapshot.activate_skill("skill.bar")
        live.update_from(snapshot)
        self.assertTrue(live.active_skills)
        # mutating the snapshot afterwards must not leak into the live object
        snapshot.active_handlers.clear()
        self.assertTrue(live.active_skills)

    def test_touch(self):
        # TODO
        pass
