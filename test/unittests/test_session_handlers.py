"""Coverage for SessionManager.wait_while_* full paths and remaining lang methods."""
import unittest
from unittest import TestCase
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message
from ovos_bus_client.session import Session, SessionManager


def _reset():
    default = Session("default")
    SessionManager.default_session = default
    SessionManager.sessions = {"default": default}
    SessionManager.bus = None


class TestWaitWhileSpeakingPath(TestCase):
    def setUp(self):
        _reset()

    def test_wait_while_speaking_registers_and_removes_listener(self):
        bus = MagicMock()
        SessionManager.bus = bus
        sess = SessionManager.default_session
        sess.is_speaking = True
        SessionManager.update(sess)
        try:
            with patch("ovos_bus_client.session.Event") as ev_cls:
                ev_cls.return_value.wait = MagicMock(return_value=True)
                ev_cls.return_value.is_set = MagicMock(return_value=True)
                SessionManager.wait_while_speaking(timeout=1)
            # registered then removed
            self.assertTrue(bus.on.called)
            self.assertTrue(bus.remove.called)
        finally:
            sess.is_speaking = False
            SessionManager.bus = None

    def test_wait_while_recording_registers_and_removes_listener(self):
        bus = MagicMock()
        SessionManager.bus = bus
        sess = SessionManager.default_session
        sess.is_recording = True
        SessionManager.update(sess)
        try:
            with patch("ovos_bus_client.session.Event") as ev_cls:
                ev_cls.return_value.wait = MagicMock(return_value=True)
                SessionManager.wait_while_recording(timeout=1)
            self.assertTrue(bus.on.called)
            self.assertTrue(bus.remove.called)
        finally:
            sess.is_recording = False
            SessionManager.bus = None


class TestSessionManagerStaticHandlers(TestCase):
    def setUp(self):
        _reset()

    def test_is_speaking_with_explicit_session(self):
        s = Session("x", is_speaking=True)
        SessionManager.update(s)
        self.assertTrue(SessionManager.is_speaking(s))

    def test_is_recording_with_explicit_session(self):
        s = Session("y", is_recording=True)
        SessionManager.update(s)
        self.assertTrue(SessionManager.is_recording(s))


class TestSessionExpiredPath(TestCase):
    def setUp(self):
        _reset()

    def test_from_message_expired_session_logged_and_returned(self):
        s = Session("exp", expiration_seconds=1)
        import time as _t
        s.touch_time = int(_t.time()) - 100
        msg = Message("t", context={"session": s.serialize()})
        sess = Session.from_message(msg)
        # expired sessions are still returned, just logged
        self.assertEqual(sess.session_id, "exp")


if __name__ == "__main__":
    unittest.main()
