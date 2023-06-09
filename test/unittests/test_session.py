import unittest

from time import time, sleep


class TestSessionModule(unittest.TestCase):
    def test_utterance_state(self):
        from ovos_bus_client.session import UtteranceState
        for state in UtteranceState:
            self.assertIsInstance(state, UtteranceState)
            self.assertIsInstance(state, str)

    def test_get_valid_langs(self):
        from ovos_bus_client.session import _get_valid_langs
        # TODO


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
        self.assertIsInstance(context_manager.keywords, list)
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
        self.assertIsInstance(session.valid_languages, list)
        self.assertEqual(session.active_skills, list())
        self.assertEqual(session.history, list())
        self.assertEqual(session.utterance_states, dict())
        self.assertIsInstance(session.max_time, int)
        self.assertIsInstance(session.max_messages, int)
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

    def test_prune_history(self):
        # TODO
        pass

    def test_clear(self):
        # TODO
        pass

    def test_serialize(self):
        # TODO
        pass

    def test_update_history(self):
        # TODO
        pass

    def test_deserialize(self):
        # TODO
        pass

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
        # TODO
        pass

    def test_get(self):
        from ovos_bus_client.session import Session
        session = self.SessionManager.get()
        self.assertIsInstance(session, Session)
        # TODO

    def test_touch(self):
        # TODO
        pass