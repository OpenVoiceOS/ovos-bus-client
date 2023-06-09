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
    # TODO
    pass
