"""AsyncMessageBusClient gives a handler bound to both spellings of a
migrated topic the same once-per-emit guarantee as MessageBusClient, on a real
message bus, whatever session the message carries.

PIPELINE-1 appendix divergences: "A transitional deployment MAY subscribe to
both names during migration." The sync client's on() wraps such a handler in
a per-handler mirror guard so the legacy frame and its ovos.* counterpart
reach it once; the async client registers the same handler on both spellings.
"""
import asyncio
import json
import socket
import threading
import unittest
import uuid

import websockets
from ovos_messagebus.event_handler import MessageBusEventHandler
from tornado import ioloop, web

from ovos_bus_client.client.async_client import AsyncMessageBusClient
from ovos_bus_client.message import Message

SPEC = "ovos.utterance.handle"
LEGACY = "recognizer_loop:utterance"
SESSIONS = {
    "absent": None,
    "default": {"session_id": "default", "lang": "pt-PT"},
    "named": {"session_id": "kitchen", "lang": "pt-PT"},
    "nat_default": {"session_id": "nsA:default", "lang": "pt-PT"},
}
SETTLE = 0.6


class _Bus:
    """The production ovos-messagebus handler served on a free local port."""

    def __init__(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        ready = threading.Event()

        def run():
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


def _payload():
    return {"utterances": [uuid.uuid4().hex], "lang": "en-US"}


def _context(session):
    return {} if session is None else {"session": dict(session)}


class TestAsyncMirrorGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _Bus()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def _scenario(self, body):
        async def run():
            receiver = AsyncMessageBusClient(
                "127.0.0.1", self.server.port, "/core", False)
            sender = AsyncMessageBusClient(
                "127.0.0.1", self.server.port, "/core", False)
            await receiver.connect()
            await sender.connect()
            try:
                return await body(receiver, sender)
            finally:
                await receiver.close()
                await sender.close()

        return asyncio.run(run())

    @staticmethod
    async def _send(sender, topic, data, session, settle=SETTLE):
        await sender.emit(Message(topic, data, _context(session)))
        await asyncio.sleep(settle)

    def test_dual_bound_handler_fires_once_per_emit(self):
        async def body(receiver, sender):
            out = {}
            for topic in (SPEC, LEGACY):
                for name, session in SESSIONS.items():
                    calls = []

                    def handler(m, calls=calls):
                        calls.append(m.msg_type)

                    receiver.on(SPEC, handler)
                    receiver.on(LEGACY, handler)
                    await self._send(sender, topic, _payload(), session)
                    receiver.remove(SPEC, handler)
                    receiver.remove(LEGACY, handler)
                    out[(topic, name)] = calls
            return out

        for (topic, name), calls in self._scenario(body).items():
            with self.subTest(topic=topic, session=name):
                self.assertEqual(calls, [topic])

    def test_coroutine_handler_fires_once_per_emit(self):
        async def body(receiver, sender):
            calls = []

            async def handler(message):
                calls.append(message.msg_type)

            receiver.on(SPEC, handler)
            receiver.on(LEGACY, handler)
            await self._send(sender, LEGACY, _payload(), SESSIONS["named"])
            return calls

        self.assertEqual(self._scenario(body), [LEGACY])

    def test_genuine_repeat_on_the_counterpart_topic_is_delivered(self):
        async def body(receiver, sender):
            out = {}
            for first, second in ((LEGACY, SPEC), (SPEC, LEGACY)):
                for name, session in SESSIONS.items():
                    calls = []

                    def handler(m, calls=calls):
                        calls.append(m.msg_type)

                    receiver.on(SPEC, handler)
                    receiver.on(LEGACY, handler)
                    data = _payload()
                    await self._send(sender, first, data, session, settle=0.1)
                    after_first = len(calls)
                    await self._send(sender, second, data, session)
                    receiver.remove(SPEC, handler)
                    receiver.remove(LEGACY, handler)
                    out[(first, name)] = (after_first, len(calls) - after_first)
            return out

        for (first, name), counts in self._scenario(body).items():
            with self.subTest(first=first, session=name):
                self.assertEqual(counts, (1, 1))

    def test_once_on_both_topics_fires_once(self):
        async def body(receiver, sender):
            out = {}
            for topic in (SPEC, LEGACY):
                calls = []

                def handler(m, calls=calls):
                    calls.append(m.msg_type)

                receiver.once(SPEC, handler)
                receiver.once(LEGACY, handler)
                await self._send(sender, topic, _payload(), SESSIONS["default"])
                after_first = list(calls)
                await self._send(sender, topic, _payload(), SESSIONS["default"])
                out[topic] = (after_first, calls)
            return out

        for topic, (after_first, calls) in self._scenario(body).items():
            with self.subTest(topic=topic):
                self.assertEqual(after_first, [topic])
                self.assertEqual(calls, [topic])

    def test_removing_one_topic_leaves_the_other_working(self):
        async def body(receiver, sender):
            calls = []

            def handler(m):
                calls.append(m.msg_type)

            receiver.on(SPEC, handler)
            receiver.on(LEGACY, handler)
            receiver.remove(SPEC, handler)
            await self._send(sender, SPEC, _payload(), SESSIONS["default"])
            await self._send(sender, SPEC, _payload(), SESSIONS["default"])
            return calls

        # the legacy registration still hears the counterpart of each emit
        self.assertEqual(self._scenario(body), [LEGACY, LEGACY])

    def test_removing_every_registration_releases_the_guards(self):
        async def body(receiver, sender):

            def handler(m):
                return None

            receiver.on(SPEC, handler)
            receiver.on(LEGACY, handler)
            receiver.remove(SPEC, handler)
            held = (dict(receiver._handler_guards), dict(receiver._dedup_registrations))
            receiver.remove(LEGACY, handler)
            return held, receiver._handler_guards, receiver._dedup_registrations

        held, guards, registrations = self._scenario(body)
        self.assertEqual((len(held[0]), len(held[1])), (1, 1))
        self.assertEqual((guards, registrations), ({}, {}))

    def test_remove_all_listeners_releases_the_guards(self):
        async def body(receiver, sender):

            def handler(m):
                return None

            receiver.on(SPEC, handler)
            receiver.on(LEGACY, handler)
            receiver.remove_all_listeners(SPEC)
            receiver.remove_all_listeners(LEGACY)
            return receiver._handler_guards, receiver._dedup_registrations

        self.assertEqual(self._scenario(body), ({}, {}))

    def test_intent_pair_bound_by_fresh_closures_runs_the_skill_once(self):
        """A skill method bound to both spellings through two unrelated
        wrapper closures runs once for a suffixed-only frame from an old
        emitter and once for a canonical emit with its suffixed twin."""
        canonical, suffixed = "sk.x:Greet", "sk.x:Greet.intent"

        async def body(receiver, sender):
            calls = []

            def skill_method(message):
                calls.append(message.msg_type)

            def bind_canonical(message):
                return skill_method(message)

            def bind_suffixed(message):
                return skill_method(message)

            receiver.on(canonical, bind_canonical)
            receiver.on(suffixed, bind_suffixed)
            uri = f"ws://127.0.0.1:{self.server.port}/core"
            async with websockets.connect(uri) as old_emitter:
                await old_emitter.send(json.dumps(
                    {"type": suffixed, "data": {}, "context": {}}))
                await asyncio.sleep(SETTLE)
            after_old_emitter = len(calls)
            await sender.emit(Message(canonical, {}, {}))
            await asyncio.sleep(SETTLE)
            return after_old_emitter, len(calls) - after_old_emitter

        self.assertEqual(self._scenario(body), (1, 1))

    def test_intent_pair_is_one_guard_released_with_its_last_registration(self):
        async def body(receiver, sender):

            def first(m):
                return None

            def second(m):
                return None

            receiver.on("my_skill:Greet", first)
            receiver.on("my_skill:Greet.intent", second)
            shared = len(receiver._intent_pair_guards)
            receiver.remove("my_skill:Greet", first)
            after_one = len(receiver._intent_pair_guards)
            receiver.remove("my_skill:Greet.intent", second)
            return shared, after_one, len(receiver._intent_pair_guards)

        self.assertEqual(self._scenario(body), (1, 1, 0))


if __name__ == "__main__":
    unittest.main()
