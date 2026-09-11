"""Coverage tests for ovos_bus_client.util.__init__ — bus helpers and lang/binary utilities."""
import unittest
from unittest import TestCase
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message
from ovos_bus_client.session import Session, SessionManager
from ovos_bus_client.util import (decode_binary_message, get_message_lang,
                                  get_mycroft_bus, get_websocket,
                                  listen_for_message,
                                  listen_once_for_message,
                                  send_binary_data_message,
                                  send_binary_file_message, send_message,
                                  wait_for_reply)


class TestGetMessageLang(TestCase):
    def test_lang_from_data(self):
        msg = Message("t", data={"lang": "de-de"})
        self.assertEqual(get_message_lang(msg), "de-DE")

    def test_lang_from_context(self):
        msg = Message("t", context={"lang": "fr-fr"})
        self.assertEqual(get_message_lang(msg), "fr-FR")

    def test_lang_from_session_in_context(self):
        s = Session(lang="es-es")
        msg = Message("t", context={"session": s.serialize()})
        self.assertEqual(get_message_lang(msg).lower(), "es-es")

    def test_none_message_returns_none(self):
        self.assertIsNone(get_message_lang(None))


class TestGetWebsocketAndBus(TestCase):
    def test_get_websocket_threaded(self):
        with patch("ovos_bus_client.util.MessageBusClient") as MBC:
            instance = MagicMock()
            MBC.return_value = instance
            client = get_websocket("h", 1, "/r", False, threaded=True)
            instance.run_in_thread.assert_called_once()
            self.assertIs(client, instance)

    def test_get_websocket_no_thread(self):
        with patch("ovos_bus_client.util.MessageBusClient") as MBC:
            instance = MagicMock()
            MBC.return_value = instance
            get_websocket("h", 1, "/r", False, threaded=False)
            instance.run_in_thread.assert_not_called()

    def test_get_mycroft_bus(self):
        with patch("ovos_bus_client.util.get_websocket") as gws, \
             patch("ovos_bus_client.util.read_mycroft_config",
                   return_value={"websocket": {"host": "h", "port": 9999,
                                                "route": "/x", "ssl": False}}):
            get_mycroft_bus()
            gws.assert_called_once()
            args = gws.call_args[0]
            self.assertEqual(args, ("h", 9999, "/x", False))


class TestListeners(TestCase):
    def test_listen_for_message_attaches_handler(self):
        bus = MagicMock()
        handler = MagicMock()
        listen_for_message("foo", handler, bus=bus)
        bus.on.assert_called_once_with("foo", handler)

    def test_listen_once_uses_once(self):
        bus = MagicMock()
        handler = MagicMock()
        listen_once_for_message("foo", handler, bus=bus)
        bus.once.assert_called_once()


class TestSendMessage(TestCase):
    def test_send_message_string_only(self):
        bus = MagicMock()
        send_message("hello", bus=bus)
        emitted = bus.emit.call_args[0][0]
        self.assertEqual(emitted.msg_type, "hello")

    def test_send_message_string_with_data(self):
        bus = MagicMock()
        send_message("hello", data={"x": 1}, context={"c": 1}, bus=bus)
        emitted = bus.emit.call_args[0][0]
        self.assertEqual(emitted.data, {"x": 1})
        self.assertEqual(emitted.context, {"c": 1})

    def test_send_message_dict(self):
        bus = MagicMock()
        send_message({"type": "hi", "data": {"a": 1}, "context": {}}, bus=bus)
        emitted = bus.emit.call_args[0][0]
        self.assertEqual(emitted.msg_type, "hi")

    def test_send_message_message_object(self):
        bus = MagicMock()
        msg = Message("greet", {"u": "world"})
        send_message(msg, bus=bus)
        bus.emit.assert_called_once_with(msg)

    def test_send_message_invalid_raises(self):
        bus = MagicMock()
        with self.assertRaises(ValueError):
            send_message(42, bus=bus)

    def test_send_message_json_string(self):
        import json
        bus = MagicMock()
        send_message(json.dumps({"type": "hi", "data": {}}), bus=bus)
        emitted = bus.emit.call_args[0][0]
        self.assertEqual(emitted.msg_type, "hi")


class TestWaitForReply(TestCase):
    def test_wait_for_reply_passes_through(self):
        bus = MagicMock()
        bus.wait_for_response.return_value = Message("r")
        msg = Message("ping")
        out = wait_for_reply(msg, reply_type="pong", timeout=1, bus=bus)
        bus.wait_for_response.assert_called_once()
        self.assertIsInstance(out, Message)

    def test_wait_for_reply_string_input(self):
        bus = MagicMock()
        bus.wait_for_response.return_value = None
        wait_for_reply("ping", bus=bus)
        bus.wait_for_response.assert_called_once()

    def test_wait_for_reply_dict_input(self):
        bus = MagicMock()
        bus.wait_for_response.return_value = None
        wait_for_reply({"type": "ping"}, bus=bus)
        bus.wait_for_response.assert_called_once()

    def test_wait_for_reply_invalid_raises(self):
        bus = MagicMock()
        with self.assertRaises(ValueError):
            wait_for_reply(42, bus=bus)


class TestBinaryHelpers(TestCase):
    def test_send_binary_data_message(self):
        bus = MagicMock()
        send_binary_data_message(b"\x00\xff\x10", bus=bus)
        emitted = bus.emit.call_args[0][0]
        self.assertEqual(emitted.msg_type, "mycroft.binary.data")
        self.assertIn("binary", emitted.data)

    def test_send_binary_file_message(self, tmp_filename="/tmp/bus_test_bin"):
        with open(tmp_filename, "wb") as f:
            f.write(b"\x01\x02\x03")
        bus = MagicMock()
        send_binary_file_message(tmp_filename, bus=bus)
        emitted = bus.emit.call_args[0][0]
        self.assertEqual(emitted.msg_type, "mycroft.binary.file")
        self.assertEqual(emitted.data["path"], tmp_filename)

    def test_decode_binary_message_from_message_object(self):
        from ovos_bus_client.message import Message
        m = Message("mycroft.binary.data", {"binary": "00ff10"})
        decoded = decode_binary_message(m)
        self.assertEqual(decoded, bytearray.fromhex("00ff10"))

    def test_decode_binary_message_from_dict(self):
        decoded = decode_binary_message({"binary": "abcd"})
        self.assertEqual(decoded, bytearray.fromhex("abcd"))

    def test_decode_binary_message_from_hex_string(self):
        decoded = decode_binary_message("abcd")
        self.assertEqual(decoded, bytearray.fromhex("abcd"))


if __name__ == "__main__":
    unittest.main()
