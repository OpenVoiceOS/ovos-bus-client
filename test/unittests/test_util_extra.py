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

    def test_non_string_lang_in_data_falls_through_to_session(self):
        """A non-string `lang` is not a declared value, so it resolves like an
        absent one (OVOS-PIPELINE-1 §9.1) instead of raising."""
        s = Session(lang="es-ES")
        for bad in (5, ["en-US"], {"tag": "en-US"}, 3.5, True):
            with self.subTest(bad=bad):
                msg = Message("recognizer_loop:utterance",
                              data={"utterances": ["hello world"], "lang": bad},
                              context={"session": s.serialize()})
                with patch("ovos_bus_client.util.LOG") as log:
                    self.assertEqual(get_message_lang(msg).lower(), "es-es")
                warned = " ".join(str(c) for c in log.warning.call_args_list)
                self.assertIn(type(bad).__name__, warned,
                              f"warning does not name the received type: {warned}")

    def test_non_string_lang_in_context_falls_through_to_session(self):
        s = Session(lang="es-ES")
        msg = Message("recognizer_loop:utterance",
                      data={"utterances": ["hello world"]},
                      context={"lang": 5, "session": s.serialize()})
        with patch("ovos_bus_client.util.LOG") as log:
            self.assertEqual(get_message_lang(msg).lower(), "es-es")
        self.assertTrue(log.warning.called)

    def test_non_string_lang_in_data_falls_through_to_context(self):
        """A non-string data `lang` yields to a valid context `lang` before the
        session answers (OVOS-PIPELINE-1 §9.1)."""
        s = Session(lang="es-ES")
        msg = Message("ovos.utterance.handle",
                      data={"utterances": ["hello world"], "lang": 5},
                      context={"lang": "de-DE", "session": s.serialize()})
        with patch("ovos_bus_client.util.LOG") as log:
            self.assertEqual(get_message_lang(msg), "de-DE")
        self.assertTrue(log.warning.called)

    def test_non_string_lang_with_no_session_falls_through_to_default(self):
        """With no session evidence the default lang answers, still no raise."""
        msg = Message("recognizer_loop:utterance",
                      data={"utterances": ["hello world"], "lang": 5})
        with patch("ovos_bus_client.util.LOG") as log:
            returned = get_message_lang(msg)
        self.assertTrue(log.warning.called)
        self.assertIsInstance(returned, str)
        self.assertEqual(returned, get_message_lang(
            Message("recognizer_loop:utterance", data={"utterances": ["hi"]})))

    def test_empty_string_lang_still_falls_through(self):
        """The pre-existing falsy path is unchanged and warns about nothing."""
        s = Session(lang="es-ES")
        msg = Message("t", data={"lang": ""}, context={"session": s.serialize()})
        with patch("ovos_bus_client.util.LOG") as log:
            self.assertEqual(get_message_lang(msg).lower(), "es-es")
        self.assertFalse(log.warning.called)


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
