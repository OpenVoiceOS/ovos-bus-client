"""The console scripts must not hang when the messagebus is unreachable.

These are one-shot troubleshooting commands. `ovos-listen` in particular is
the first thing the technical manual tells a user to run when the assistant
stops responding, so blocking forever on a blank terminal is the worst
available failure mode: it looks exactly like a command that is working.
"""
import sys
import unittest
from unittest import mock

from ovos_bus_client import scripts

SCRIPTS = [
    ("ovos-listen", scripts.ovos_listen, ["ovos-listen"]),
    ("ovos-speak", scripts.ovos_speak, ["ovos-speak", "hello"]),
    ("ovos-say-to", scripts.ovos_say_to, ["ovos-say-to", "hello"]),
    ("ovos-simple-cli", scripts.simple_cli, ["ovos-simple-cli"]),
]


class _NeverConnects:
    """A client whose connected_event is never set."""

    def __init__(self, *args, **kwargs):
        self.connected_event = mock.Mock()
        self.connected_event.is_set.return_value = False
        self.closed = False

    def run_in_thread(self):
        pass

    def close(self):
        self.closed = True


class TestScripts(unittest.TestCase):
    def test_scripts_exit_when_bus_unreachable(self):
        for name, func, argv in SCRIPTS:
            with self.subTest(script=name):
                client = _NeverConnects()
                with mock.patch.object(scripts, "MessageBusClient",
                                       return_value=client), \
                        mock.patch.object(sys, "argv", argv):
                    with self.assertRaises(SystemExit) as ctx:
                        func()
                self.assertEqual(ctx.exception.code, 1)
                # the wait has to be bounded, not open ended
                client.connected_event.wait.assert_called_once_with(
                    scripts.CONNECT_TIMEOUT)
                self.assertTrue(client.closed,
                                "client must be closed before exiting")

    def test_connect_timeout_is_bounded(self):
        self.assertIsInstance(scripts.CONNECT_TIMEOUT, (int, float))
        self.assertGreater(scripts.CONNECT_TIMEOUT, 0)

    def test_help_never_touches_the_bus(self):
        # `ovos-speak --help` used to treat "--help" as the utterance, so on a
        # device with a reachable bus it said "--help" out loud.
        for name, func, _ in SCRIPTS:
            for flag in ("-h", "--help"):
                with self.subTest(script=name, flag=flag):
                    with mock.patch.object(scripts, "MessageBusClient") as bus, \
                            mock.patch.object(sys, "argv", [name, flag]):
                        with self.assertRaises(SystemExit) as ctx:
                            func()
                    self.assertEqual(ctx.exception.code, 0)
                    bus.assert_not_called()

    def test_scripts_emit_expected_message(self):
        expected = {
            "ovos-listen": "mycroft.mic.listen",
            "ovos-speak": "speak",
            "ovos-say-to": "recognizer_loop:utterance",
        }
        for name, func, argv in SCRIPTS:
            if name not in expected:
                continue
            with self.subTest(script=name):
                client = mock.Mock()
                client.connected_event.is_set.return_value = True
                with mock.patch.object(scripts, "MessageBusClient",
                                       return_value=client), \
                        mock.patch.object(sys, "argv", argv), \
                        mock.patch.object(scripts.time, "sleep"):
                    func()
                client.emit.assert_called_once()
                message = client.emit.call_args[0][0]
                self.assertEqual(message.msg_type, expected[name])
                client.close.assert_called_once()

    def test_bad_usage_exits_two(self):
        for name, func, _ in [s for s in SCRIPTS if s[0] != "ovos-listen"]:
            with self.subTest(script=name):
                argv = [name, "too", "many", "args"]
                with mock.patch.object(scripts, "MessageBusClient") as bus, \
                        mock.patch.object(sys, "argv", argv):
                    if name == "ovos-simple-cli":
                        # returns rather than raising, by long-standing habit
                        func()
                    else:
                        with self.assertRaises(SystemExit) as ctx:
                            func()
                        self.assertEqual(ctx.exception.code, 2)
                    bus.assert_not_called()
