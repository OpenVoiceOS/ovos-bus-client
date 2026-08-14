"""Opt-in same-process fast delivery (``websocket.local_echo_topics``).

ovos-core's intent dispatcher waits on handler acks that skills in the SAME
process emit; without echo each ack pays a full bus round-trip to arrive back
where it started (measured ~20-60ms under a 400-client load). With echo the
local listeners fire at emit time and the returning wire copy is dropped.
"""
import json
import time
import unittest
from unittest.mock import MagicMock, patch

from ovos_bus_client.client.client import MessageBusClient, LOCAL_ECHO_SRC_KEY
from ovos_bus_client.message import Message

ACK = "mycroft.skill.handler.complete"


def _client(echo_topics):
    cfg = {"websocket": {"local_echo_topics": echo_topics}}
    with patch("ovos_config.Configuration", return_value=cfg):
        c = MessageBusClient()
    c.client = MagicMock()
    c.started_running = True
    c.connected_event.set()
    return c


class TestLocalEchoOff(unittest.TestCase):
    def test_default_no_echo_machinery(self):
        c = _client([])
        self.assertEqual(c._local_echo_topics, frozenset())
        self.assertIsNone(c._echo_source)
        got = []
        c.on(ACK, got.append)
        c.emit(Message(ACK, {"n": 1}))
        # without echo, local listeners hear nothing until the wire copy
        # comes back through on_message
        self.assertEqual(got, [])


class TestLocalEchoOn(unittest.TestCase):
    def setUp(self):
        self.c = _client([ACK])

    def test_local_listener_fires_at_emit_time(self):
        got = []
        self.c.on(ACK, got.append)
        self.c.emit(Message(ACK, {"n": 1}))
        deadline = time.monotonic() + 2
        while not got and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(len(got), 1, "listener must fire without the wire")
        self.assertEqual(got[0].data["n"], 1)
        # the local copy must NOT carry the wire marker
        self.assertNotIn(LOCAL_ECHO_SRC_KEY, got[0].context)

    def test_wire_copy_carries_marker_and_still_sends(self):
        self.c.emit(Message(ACK, {"n": 2}))
        self.c.client.send.assert_called()
        wire = json.loads(self.c.client.send.call_args.args[0])
        self.assertEqual(wire["context"][LOCAL_ECHO_SRC_KEY], self.c._echo_source)

    def test_own_wire_copy_is_dropped_on_receive(self):
        got = []
        self.c.on(ACK, got.append)
        msg = Message(ACK, {"n": 3}, {LOCAL_ECHO_SRC_KEY: self.c._echo_source})
        self.c.on_message(None, msg.serialize())
        time.sleep(0.05)
        self.assertEqual(got, [], "own echo must not double-deliver")

    def test_foreign_echo_copy_dispatches_normally(self):
        got = []
        self.c.on(ACK, got.append)
        msg = Message(ACK, {"n": 4}, {LOCAL_ECHO_SRC_KEY: "someone-else"})
        self.c.on_message(None, msg.serialize())
        deadline = time.monotonic() + 2
        while not got and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(len(got), 1, "another process's ack must deliver")

    def test_unlisted_topics_unaffected(self):
        got = []
        self.c.on("some.other.topic", got.append)
        self.c.emit(Message("some.other.topic", {"n": 5}))
        self.assertEqual(got, [])
        wire = json.loads(self.c.client.send.call_args.args[0])
        self.assertNotIn(LOCAL_ECHO_SRC_KEY, wire["context"])

    def test_handler_mutation_does_not_leak_to_wire(self):
        def mutate(m):
            m.data["mutated"] = True
        self.c.on(ACK, mutate)
        self.c.emit(Message(ACK, {"n": 6}))
        time.sleep(0.05)
        wire = json.loads(self.c.client.send.call_args.args[0])
        self.assertNotIn("mutated", wire["data"])


if __name__ == "__main__":
    unittest.main()
