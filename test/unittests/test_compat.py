from ovos_bus_client.message import Message
from mycroft_bus_client.message import Message as _MycroftMessage
import unittest


class TestInterface(unittest.TestCase):
    def test_msg(self):
        m1 = _MycroftMessage("")
        m2 = Message("")
        self.assertTrue(m1 == m2)
        self.assertTrue(m2 == m1)
        self.assertTrue(isinstance(m1, _MycroftMessage))
        self.assertTrue(isinstance(m1, Message))
        self.assertTrue(isinstance(m2, _MycroftMessage))
        self.assertTrue(isinstance(m2, Message))
