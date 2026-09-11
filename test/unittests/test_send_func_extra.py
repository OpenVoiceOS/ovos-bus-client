"""Coverage tests for ovos_bus_client.send_func."""
import unittest
from unittest import TestCase
from unittest.mock import MagicMock, patch


class TestSend(TestCase):
    @patch("ovos_bus_client.send_func.create_connection")
    @patch("ovos_bus_client.send_func.Configuration")
    def test_send_opens_ws_and_sends(self, Cfg, create_conn):
        Cfg.return_value = {"websocket": {"host": "h", "port": 1, "route": "/r", "ssl": False}}
        ws = MagicMock()
        create_conn.return_value = ws

        from ovos_bus_client.send_func import send
        send("hello", {"x": 1})

        # connection opened with correct URL
        create_conn.assert_called_once_with("ws://h:1/r")
        # message sent
        ws.send.assert_called_once()
        payload = ws.send.call_args[0][0]
        self.assertIn("hello", payload)
        ws.close.assert_called_once()

    @patch("ovos_bus_client.send_func.create_connection")
    @patch("ovos_bus_client.send_func.Configuration")
    def test_send_default_data(self, Cfg, create_conn):
        Cfg.return_value = {"websocket": {"host": "h", "port": 1, "route": "/r", "ssl": False}}
        ws = MagicMock()
        create_conn.return_value = ws
        from ovos_bus_client.send_func import send
        send("hello")
        ws.send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
