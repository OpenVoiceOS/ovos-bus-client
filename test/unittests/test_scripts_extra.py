"""Coverage tests for ovos_bus_client.scripts — CLI entry points."""
import unittest
from unittest import TestCase
from unittest.mock import MagicMock, patch


class TestOvosSpeak(TestCase):
    @patch("ovos_bus_client.scripts.sys", argv=["ovos-speak", "hi"])
    @patch("ovos_bus_client.scripts.time", MagicMock())
    @patch("ovos_bus_client.scripts.MessageBusClient")
    def test_speak_basic(self, MBC, _sys):
        client = MagicMock()
        client.connected_event.is_set.return_value = True
        MBC.return_value = client

        from ovos_bus_client.scripts import ovos_speak
        ovos_speak()
        emitted = client.emit.call_args[0][0]
        self.assertEqual(emitted.msg_type, "speak")
        self.assertEqual(emitted.data["utterance"], "hi")

    @patch("ovos_bus_client.scripts.sys", argv=["ovos-speak", "hej", "sv-se"])
    @patch("ovos_bus_client.scripts.time", MagicMock())
    @patch("ovos_bus_client.scripts.MessageBusClient")
    def test_speak_with_lang(self, MBC, _sys):
        client = MagicMock()
        client.connected_event.is_set.return_value = True
        MBC.return_value = client

        from ovos_bus_client.scripts import ovos_speak
        ovos_speak()
        emitted = client.emit.call_args[0][0]
        self.assertEqual(emitted.data["lang"], "sv-se")

    @patch("ovos_bus_client.scripts.sys", argv=["ovos-speak"])
    def test_speak_no_args_exits(self, _sys):
        from ovos_bus_client.scripts import ovos_speak
        with self.assertRaises(SystemExit):
            ovos_speak()


class TestOvosSayTo(TestCase):
    @patch("ovos_bus_client.scripts.sys", argv=["ovos-say-to", "what time"])
    @patch("ovos_bus_client.scripts.time", MagicMock())
    @patch("ovos_bus_client.scripts.MessageBusClient")
    def test_say_to_basic(self, MBC, _sys):
        client = MagicMock()
        client.connected_event.is_set.return_value = True
        MBC.return_value = client

        from ovos_bus_client.scripts import ovos_say_to
        ovos_say_to()
        emitted = client.emit.call_args[0][0]
        self.assertEqual(emitted.msg_type, "recognizer_loop:utterance")
        self.assertEqual(emitted.data["utterances"], ["what time"])

    @patch("ovos_bus_client.scripts.sys", argv=["ovos-say-to"])
    def test_say_to_no_args_exits(self, _sys):
        from ovos_bus_client.scripts import ovos_say_to
        with self.assertRaises(SystemExit):
            ovos_say_to()

    @patch("ovos_bus_client.scripts.sys", argv=["ovos-say-to", "qual", "pt-pt"])
    @patch("ovos_bus_client.scripts.time", MagicMock())
    @patch("ovos_bus_client.scripts.MessageBusClient")
    def test_say_to_with_lang(self, MBC, _sys):
        client = MagicMock()
        client.connected_event.is_set.return_value = True
        MBC.return_value = client

        from ovos_bus_client.scripts import ovos_say_to
        ovos_say_to()
        emitted = client.emit.call_args[0][0]
        self.assertEqual(emitted.data["lang"], "pt-pt")


class TestOvosListen(TestCase):
    @patch("ovos_bus_client.scripts.time", MagicMock())
    @patch("ovos_bus_client.scripts.MessageBusClient")
    def test_listen_emits_listen_message(self, MBC):
        client = MagicMock()
        client.connected_event.is_set.return_value = True
        MBC.return_value = client

        from ovos_bus_client.scripts import ovos_listen
        ovos_listen()
        emitted = client.emit.call_args[0][0]
        self.assertEqual(emitted.msg_type, "mycroft.mic.listen")


class TestSimpleCLI(TestCase):
    @patch("ovos_bus_client.scripts.sys", argv=["ovos-simple-cli"])
    @patch("ovos_bus_client.scripts.time", MagicMock())
    @patch("ovos_bus_client.scripts.MessageBusClient")
    @patch("builtins.input", side_effect=[":exit"])
    def test_exit_command_breaks(self, _input, MBC, _sys):
        client = MagicMock()
        client.connected_event.is_set.return_value = True
        MBC.return_value = client

        from ovos_bus_client.scripts import simple_cli
        simple_cli()  # exits immediately
        client.close.assert_called_once()

    @patch("ovos_bus_client.scripts.sys", argv=["ovos-simple-cli"])
    @patch("ovos_bus_client.scripts.time", MagicMock())
    @patch("ovos_bus_client.scripts.MessageBusClient")
    @patch("builtins.input", side_effect=["hello", KeyboardInterrupt])
    def test_emits_utterance_then_keyboard_interrupt(self, _input, MBC, _sys):
        client = MagicMock()
        client.connected_event.is_set.return_value = True
        MBC.return_value = client

        from ovos_bus_client.scripts import simple_cli
        simple_cli()
        types = [c.args[0].msg_type for c in client.emit.call_args_list]
        self.assertIn("recognizer_loop:utterance", types)

    @patch("ovos_bus_client.scripts.sys", argv=["ovos-simple-cli", "extra", "args"])
    def test_too_many_args_prints_usage(self, _sys):
        from ovos_bus_client.scripts import simple_cli
        # USAGE print, return None
        result = simple_cli()
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
