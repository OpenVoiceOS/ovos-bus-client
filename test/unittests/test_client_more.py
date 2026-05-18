"""More client.py coverage — on_error variants, on_default_session_update,
two-arg dispatchers, GUIWebsocketClient on_open / on_message."""
import json
import unittest
from unittest import TestCase
from unittest.mock import MagicMock, patch

from ovos_bus_client.client.client import (GUIWebsocketClient,
                                           MessageBusClient)
from ovos_bus_client.message import GUIMessage, Message
from ovos_bus_client.session import Session, SessionManager
from websocket import (WebSocketConnectionClosedException, WebSocketException)


def _mocked_client():
    bus = MessageBusClient()
    bus.client = MagicMock()
    bus.client.keep_running = False     # skip the close branch
    bus.connected_event.set()
    bus.retry = 0
    return bus


class TestOnError(TestCase):
    @patch("ovos_bus_client.client.client.time.sleep", MagicMock())
    def test_websocket_closed(self):
        bus = _mocked_client()
        # subsequent reconnect call → patch create_client + run_forever
        bus.create_client = MagicMock(return_value=MagicMock())
        bus.run_forever = MagicMock(side_effect=WebSocketException())
        bus.on_error(WebSocketConnectionClosedException())

    @patch("ovos_bus_client.client.client.time.sleep", MagicMock())
    def test_connection_refused(self):
        bus = _mocked_client()
        bus.create_client = MagicMock(return_value=MagicMock())
        bus.run_forever = MagicMock(side_effect=WebSocketException())
        bus.on_error(ConnectionRefusedError())

    @patch("ovos_bus_client.client.client.time.sleep", MagicMock())
    def test_connection_reset(self):
        bus = _mocked_client()
        bus.create_client = MagicMock(return_value=MagicMock())
        bus.run_forever = MagicMock(side_effect=WebSocketException())
        bus.on_error(ConnectionResetError())

    @patch("ovos_bus_client.client.client.time.sleep", MagicMock())
    def test_generic_exception_emits_error_event(self):
        bus = _mocked_client()
        bus.create_client = MagicMock(return_value=MagicMock())
        bus.run_forever = MagicMock(side_effect=WebSocketException())
        captured = []
        bus.emitter.on("error", lambda e: captured.append(e))
        bus.on_error(RuntimeError("boom"))
        # error event fired
        self.assertTrue(any(isinstance(e, RuntimeError) for e in captured))

    @patch("ovos_bus_client.client.client.time.sleep", MagicMock())
    def test_two_arg_signature(self):
        bus = _mocked_client()
        bus.create_client = MagicMock(return_value=MagicMock())
        bus.run_forever = MagicMock(side_effect=WebSocketException())
        # websocket-client sometimes passes (ws, error); the code reads args[1]
        bus.on_error(MagicMock(), RuntimeError("from-two-args"))


class TestOnDefaultSessionUpdate(TestCase):
    def test_replaces_default_session(self):
        bus = MessageBusClient()
        s = Session("incoming")
        msg = Message("ovos.session.update_default",
                      {"session_data": s.serialize()})
        # capture default before
        bus.on_default_session_update(msg)
        self.assertEqual(SessionManager.default_session.session_id, "default")
        # session_id forced to "default" when make_default=True


class TestOnMessageSessionUpdate(TestCase):
    def test_non_default_session_is_registered(self):
        # default = Session("default")
        SessionManager.sessions = {"default": Session("default")}
        bus = MessageBusClient()
        bus.client = MagicMock()
        bus.connected_event.set()
        s = Session("kitchen", lang="pt-pt")
        raw = Message("speak", {}, {"session": s.serialize()}).serialize()
        bus.on_message(raw)
        self.assertIn("kitchen", SessionManager.sessions)


class TestTwoArgDispatch(TestCase):
    def test_on_message_two_arg(self):
        bus = MessageBusClient()
        bus.client = MagicMock()
        bus.connected_event.set()
        seen = []
        bus.on("hello", lambda m: seen.append(m))
        raw = Message("hello").serialize()
        # WebSocket-client may call with (ws, message)
        bus.on_message(MagicMock(), raw)
        self.assertEqual(len(seen), 1)


class TestRunForever(TestCase):
    def test_sets_started_running(self):
        bus = MessageBusClient()
        bus.client = MagicMock()
        bus.run_forever()
        self.assertTrue(bus.started_running)
        bus.client.run_forever.assert_called_once()


class TestGUIWebsocketClientHandlers(TestCase):
    def test_on_open_emits_open_event(self):
        gui = GUIWebsocketClient()
        gui.client = MagicMock()
        seen = []
        gui.emitter.on("open", lambda *a: seen.append(True))
        gui.on_open()
        self.assertEqual(seen, [True])

    def test_on_message_dispatches_parsed(self):
        gui = GUIWebsocketClient()
        gui.client = MagicMock()
        seen = []
        # GUIMessage uses kwargs as data, but on_message uses GUIMessage.deserialize
        gui.emitter.on("gui.test", lambda m: seen.append(m))
        raw = GUIMessage("gui.test", value=1).serialize()
        gui.on_message(raw)
        self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
