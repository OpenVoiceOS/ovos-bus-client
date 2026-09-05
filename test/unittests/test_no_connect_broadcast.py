"""OVOS-SESSION-2 §2.7 — connecting announces nothing.

There is no topic on which a participant pushes its own session at another.
A process joining a bus derives its default session from configuration and
converges by adoption from the traffic it observes, so attaching the session
registry to a bus must be silent. A connect-time broadcast would let any
joining process overwrite the orchestrator's default-session store with a
view built from its own configuration.
"""
import unittest

from ovos_utils.fakebus import FakeBus

from ovos_bus_client.session import SessionManager

# every spelling the default-session push has ever travelled under
BROADCAST_TOPICS = {"ovos.session.update_default",
                    "mycroft.session.update_default",
                    "ovos.session.sync",
                    "mycroft.session.sync"}


class TestConnectDoesNotBroadcast(unittest.TestCase):
    def setUp(self):
        self.bus = FakeBus()
        self.emitted = []
        self.bus.on("message", lambda m: self.emitted.append(m))

    def tearDown(self):
        SessionManager.bus = None
        SessionManager.reset_default_session()

    def test_connect_emits_nothing(self):
        SessionManager.connect_to_bus(self.bus)
        self.assertEqual([m for m in self.emitted
                          if _msg_type(m) in BROADCAST_TOPICS], [])
        self.assertEqual(self.emitted, [])

    def test_default_session_still_resolves_locally(self):
        SessionManager.connect_to_bus(self.bus)
        sess = SessionManager.get_default_session()
        self.assertEqual(sess.session_id, "default")
        self.assertTrue(sess.lang)
        self.assertEqual([], self.emitted)

    def test_orchestrator_echo_is_still_consumable(self):
        """The legacy request/echo path an orchestrator drives is untouched."""
        SessionManager.connect_to_bus(self.bus)
        SessionManager.handle_default_session_request(None)
        self.assertEqual([_msg_type(m) for m in self.emitted],
                         ["ovos.session.update_default"])


def _msg_type(msg):
    if isinstance(msg, str):
        import json
        return json.loads(msg).get("type")
    return msg.msg_type


if __name__ == "__main__":
    unittest.main()
