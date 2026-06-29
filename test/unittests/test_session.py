import unittest
from unittest.mock import patch
from time import time, sleep


class TestSessionModule(unittest.TestCase):
    def test_utterance_state(self):
        from ovos_bus_client.session import UtteranceState
        for state in UtteranceState:
            self.assertIsInstance(state, UtteranceState)
            self.assertIsInstance(state, str)


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
        # TODO
        pass


class TestSessionManager(unittest.TestCase):
    from ovos_bus_client.session import SessionManager

    def test_prune_sessions(self):
        # TODO
        self.SessionManager.prune_sessions()

    def test_reset_default_session(self):
        from ovos_bus_client.session import Session
        session = self.SessionManager.reset_default_session()
        self.assertIsInstance(session, Session)
        self.assertEqual(session, self.SessionManager.default_session)
        # TODO

    def test_update(self):
        from ovos_bus_client.session import Session
        sess = Session("sid-update")
        # update returns the canonical (singleton) object for the id
        canonical = self.SessionManager.update(sess)
        self.assertIs(canonical, sess)
        self.assertIs(self.SessionManager.sessions["sid-update"], sess)

        # a second snapshot for the same id is folded onto the singleton in
        # place — the original object identity is preserved, not replaced
        snapshot = Session("sid-update")
        snapshot.lang = "pt-PT"
        returned = self.SessionManager.update(snapshot)
        self.assertIs(returned, sess)
        self.assertIsNot(returned, snapshot)
        self.assertEqual(sess.lang, "pt-PT")

    def test_get_returns_singleton(self):
        from ovos_bus_client.session import Session
        from ovos_bus_client.message import Message
        sess = Session("sid-get")
        msg = Message("test", context={"session": sess.serialize()})

        first = self.SessionManager.get(msg)
        second = self.SessionManager.get(msg)
        # every get() for the same id hands back the one live object, even
        # though each message carries its own serialized snapshot
        self.assertIs(first, second)
        self.assertIs(self.SessionManager.sessions["sid-get"], first)

    def test_held_reference_observes_later_mutation(self):
        # the corner case the singleton fixes: a reference taken early in a
        # flow must see a flag flipped through a later snapshot of the same id
        from ovos_bus_client.session import Session
        from ovos_bus_client.message import Message
        held = self.SessionManager.get(
            Message("a", context={"session": Session("sid-flag").serialize()}))
        self.assertFalse(held.is_speaking)

        speaking = Session("sid-flag")
        speaking.is_speaking = True
        self.SessionManager.update(speaking)
        # the early reference observes the mutation without being re-fetched
        self.assertTrue(held.is_speaking)

    def test_update_from_present_empty_overrides(self):
        # SESSION-1 §2: a snapshot that carries an (empty) value for a field
        # overrides the live session's value — folding is spec-deserialization,
        # not a self-preserving merge that keeps stale state.
        from ovos_bus_client.session import Session
        from ovos_bus_client.message import Message
        held = self.SessionManager.get(
            Message("a", context={"session": Session("sid-clear").serialize()}))
        held.activate_skill("skill.foo")
        self.assertTrue(held.active_skills)

        # a later snapshot with no active skills must clear the singleton
        self.SessionManager.update(Session("sid-clear"))
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
