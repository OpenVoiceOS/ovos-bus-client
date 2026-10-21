"""MessageBusClient.remove_all_listeners releases the dedup bookkeeping.

PIPELINE-1 appendix divergences: "A transitional deployment MAY subscribe to
both names during migration." on() keeps a registration list, a per-handler
mirror guard and an intent-pair guard for such a handler. Removing every
listener of a topic must release them, as remove() does, so a handler
registered afterwards runs once per emit.
"""
import gc
import socket
import threading
import time
import unittest
import uuid
import weakref

from ovos_messagebus.event_handler import MessageBusEventHandler
from tornado import ioloop, web

from ovos_bus_client.client.client import MessageBusClient
from ovos_bus_client.message import Message

SPEC = "ovos.utterance.handle"
LEGACY = "recognizer_loop:utterance"
INTENT = "sk.x:Greet"
INTENT_SUFFIXED = "sk.x:Greet.intent"
SETTLE = 0.6


class _Bus:
    def __init__(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        ready = threading.Event()

        def run():
            import asyncio
            asyncio.set_event_loop(asyncio.new_event_loop())
            web.Application([("/core", MessageBusEventHandler)]).listen(
                self.port, "127.0.0.1")
            self.io = ioloop.IOLoop.current()
            ready.set()
            self.io.start()

        threading.Thread(target=run, daemon=True).start()
        assert ready.wait(10)

    def stop(self):
        self.io.add_callback(self.io.stop)


def _client(port):
    client = MessageBusClient("127.0.0.1", port, "/core", False)
    client.run_in_thread()
    assert client.connected_event.wait(10)
    return client


class TestSyncRemoveAllListenersGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _Bus()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        self.receiver = _client(self.server.port)
        self.sender = _client(self.server.port)
        self.addCleanup(self.receiver.close)
        self.addCleanup(self.sender.close)

    def _emit(self, topic):
        self.sender.emit(Message(topic, {"id": uuid.uuid4().hex}))
        time.sleep(SETTLE)

    def _assert_released(self):
        self.assertEqual(self.receiver._dedup_registrations, {})
        self.assertEqual(self.receiver._handler_guards, {})
        self.assertEqual(self.receiver._intent_pair_guards, {})

    def test_migrated_topic_pair_is_released(self):
        def stale(message):
            raise AssertionError("a removed handler ran")

        self.receiver.on(SPEC, stale)
        self.receiver.on(LEGACY, stale)
        self.assertEqual(len(self.receiver._dedup_registrations[stale]), 2)
        self.receiver.remove_all_listeners(SPEC)
        self.receiver.remove_all_listeners(LEGACY)
        self._assert_released()

        calls = []
        self.receiver.on(SPEC, calls.append)
        self.receiver.on(LEGACY, calls.append)
        for topic in (LEGACY, SPEC):
            calls.clear()
            self._emit(topic)
            self.assertEqual(len(calls), 1)
            self.assertIn(calls[0].msg_type, (SPEC, LEGACY))

    def test_intent_pair_is_released(self):
        def stale(message):
            raise AssertionError("a removed handler ran")

        self.receiver.on(INTENT, stale)
        self.receiver.on(INTENT_SUFFIXED, stale)
        self.assertEqual(len(self.receiver._intent_pair_guards), 1)
        self.receiver.remove_all_listeners(INTENT)
        self.receiver.remove_all_listeners(INTENT_SUFFIXED)
        self._assert_released()

        calls = []
        # fresh closures per spelling, as a skill's wrapper creates them
        self.receiver.on(INTENT, lambda m: calls.append(m.msg_type))
        self.receiver.on(INTENT_SUFFIXED, lambda m: calls.append(m.msg_type))
        for topic in (INTENT, INTENT_SUFFIXED):
            calls.clear()
            self._emit(topic)
            self.assertEqual(len(calls), 1)
            self.assertIn(calls[0], (INTENT, INTENT_SUFFIXED))

    def test_removing_one_topic_keeps_the_other_registered(self):
        calls = []

        def handler(message):
            calls.append(message.msg_type)

        self.receiver.on(SPEC, handler)
        self.receiver.on(LEGACY, handler)
        self.receiver.remove_all_listeners(SPEC)
        self.assertEqual(self.receiver._dedup_registrations[handler],
                         [(LEGACY, self.receiver._dedup_registrations[handler][0][1])])
        self.assertIn(handler, self.receiver._handler_guards)
        self._emit(LEGACY)
        self.assertEqual(calls, [LEGACY])


    def test_removed_bound_method_does_not_keep_its_owner_alive(self):
        class Owner:
            def handle(self, message):
                pass

        owner = Owner()
        ref = weakref.ref(owner)
        self.receiver.on(SPEC, owner.handle)
        self.receiver.on(LEGACY, owner.handle)
        self.receiver.remove_all_listeners(SPEC)
        self.receiver.remove_all_listeners(LEGACY)
        del owner
        gc.collect()
        self.assertIsNone(ref())

    def test_churned_intent_topic_leaves_no_pair_guard(self):
        for _ in range(50):
            self.receiver.on(INTENT, lambda m: None)
            self.receiver.remove_all_listeners(INTENT)
        self.assertEqual(len(self.receiver._intent_pair_guards), 0)


if __name__ == "__main__":
    unittest.main()
