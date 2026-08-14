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

    def test_foreign_echo_copy_dispatches_normally_without_marker(self):
        got = []
        self.c.on(ACK, got.append)
        msg = Message(ACK, {"n": 4}, {LOCAL_ECHO_SRC_KEY: "someone-else"})
        self.c.on_message(None, msg.serialize())
        deadline = time.monotonic() + 2
        while not got and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(len(got), 1, "another process's ack must deliver")
        self.assertNotIn(LOCAL_ECHO_SRC_KEY, got[0].context,
                         "the marker must be POPPED before foreign dispatch")

    def test_foreign_reply_reaches_the_originator(self):
        """A(emit, marked) -> B(handler replies; context deep-copied) ->
        A must DISPATCH the reply, not drop it as its own echo."""
        a = self.c
        b = _client([ACK])
        # B receives A's marked wire frame and replies from it
        wire_from_a = Message(ACK, {"q": 1},
                              {LOCAL_ECHO_SRC_KEY: a._echo_source})
        received_by_b = []
        b.on(ACK, received_by_b.append)
        b.on_message(None, wire_from_a.serialize())
        deadline = time.monotonic() + 2
        while not received_by_b and time.monotonic() < deadline:
            time.sleep(0.005)
        reply = received_by_b[0].reply("unit.test.response", {"a": 2})
        # the popped marker must not have survived the deep-copied context
        self.assertNotIn(LOCAL_ECHO_SRC_KEY, reply.context)
        got_reply = []
        a.on("unit.test.response", got_reply.append)
        a.on_message(None, reply.serialize())
        deadline = time.monotonic() + 2
        while not got_reply and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertEqual(len(got_reply), 1,
                         "the originator must not drop a foreign reply")
        b.close()

    def test_emit_does_not_mutate_caller_message(self):
        msg = Message(ACK, {"n": 9})
        self.c.emit(msg)
        self.assertNotIn(LOCAL_ECHO_SRC_KEY, msg.context,
                         "emit must stamp a wire clone, not the caller object")

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
