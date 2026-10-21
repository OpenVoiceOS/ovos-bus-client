"""A handler bound to both spellings of a migrated topic runs once per emit
on a real message bus, whatever session the message carries.

PIPELINE-1 appendix divergences: "A transitional deployment MAY subscribe to
both names during migration." The client builds the counterpart of every
received frame for local listeners and the per-handler mirror guard collapses
the pair, so a counterpart must be a verbatim copy of the frame it mirrors:
same context, same session, same fingerprint.
"""
import asyncio
import socket
import threading
import time
import unittest
import uuid

from tornado import web, ioloop
from ovos_messagebus.event_handler import MessageBusEventHandler

from ovos_bus_client.client.async_client import AsyncMessageBusClient
from ovos_bus_client.client.client import MessageBusClient
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
        self.loop = None
        ready = threading.Event()

        def run():
            import asyncio as aio
            self.loop = aio.new_event_loop()
            aio.set_event_loop(self.loop)
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


def _session_lang(message):
    return message.context.get("session", {}).get("lang")


def _sync_client(port):
    client = MessageBusClient("127.0.0.1", port, "/core", False)
    client.run_in_thread()
    assert client.connected_event.wait(10)
    return client


class _RealBusCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _Bus()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()


class TestSyncClient(_RealBusCase):
    def setUp(self):
        self.receiver = _sync_client(self.server.port)
        self.sender = _sync_client(self.server.port)

    def tearDown(self):
        self.receiver.close()
        self.sender.close()

    def _send(self, topic, data, session, settle=SETTLE):
        self.sender.emit(Message(topic, data, _context(session)))
        time.sleep(settle)

    def test_dual_bound_handler_fires_once_per_emit(self):
        for topic in (SPEC, LEGACY):
            for name, session in SESSIONS.items():
                with self.subTest(topic=topic, session=name):
                    calls = []
                    def handler(m, calls=calls):
                        calls.append(m.msg_type)
                    self.receiver.on(SPEC, handler)
                    self.receiver.on(LEGACY, handler)
                    self._send(topic, _payload(), session)
                    self.receiver.remove(SPEC, handler)
                    self.receiver.remove(LEGACY, handler)
                    self.assertEqual(len(calls), 1, calls)

    def test_counterpart_carries_the_senders_session(self):
        for topic, other in ((SPEC, LEGACY), (LEGACY, SPEC)):
            for name, session in SESSIONS.items():
                if session is None:
                    continue
                with self.subTest(topic=topic, session=name):
                    seen = {}
                    self.receiver.on(topic, lambda m: seen.setdefault("own", m))
                    self.receiver.on(other, lambda m: seen.setdefault("counterpart", m))
                    self._send(topic, _payload(), session)
                    self.assertEqual(_session_lang(seen["own"]), "pt-PT")
                    self.assertEqual(_session_lang(seen["counterpart"]), "pt-PT")
                    self.assertEqual(seen["counterpart"].context.get("session"),
                                     seen["own"].context.get("session"))

    def test_genuine_repeat_on_the_counterpart_topic_is_delivered(self):
        for first, second in ((LEGACY, SPEC), (SPEC, LEGACY)):
            for name, session in SESSIONS.items():
                with self.subTest(first=first, session=name):
                    calls = []
                    def handler(m, calls=calls):
                        calls.append(m.msg_type)
                    self.receiver.on(SPEC, handler)
                    self.receiver.on(LEGACY, handler)
                    data = _payload()
                    self._send(first, data, session, settle=0.1)
                    after_first = len(calls)
                    self._send(second, data, session)
                    self.receiver.remove(SPEC, handler)
                    self.receiver.remove(LEGACY, handler)
                    self.assertEqual(
                        (after_first, len(calls) - after_first), (1, 1), calls)


class TestAsyncClient(_RealBusCase):
    """The async client has no per-handler mirror guard, so only the
    counterpart's fidelity to the frame it mirrors is asserted."""

    def _run(self, coro):
        return asyncio.run(coro)

    async def _clients(self):
        receiver = AsyncMessageBusClient("127.0.0.1", self.server.port, "/core", False)
        sender = AsyncMessageBusClient("127.0.0.1", self.server.port, "/core", False)
        await receiver.connect()
        await sender.connect()
        return receiver, sender

    def test_counterpart_carries_the_senders_session(self):
        async def scenario():
            receiver, sender = await self._clients()
            out = {}
            for topic, other in ((SPEC, LEGACY), (LEGACY, SPEC)):
                for name, session in SESSIONS.items():
                    if session is None:
                        continue
                    seen = {}
                    def h1(m, seen=seen):
                        seen.setdefault("own", m)
                    def h2(m, seen=seen):
                        seen.setdefault("counterpart", m)
                    receiver.on(topic, h1)
                    receiver.on(other, h2)
                    await sender.emit(Message(topic, _payload(), _context(session)))
                    await asyncio.sleep(SETTLE)
                    receiver.remove(topic, h1)
                    receiver.remove(other, h2)
                    out[(topic, name)] = seen
            await receiver.close()
            await sender.close()
            return out

        for (topic, name), seen in self._run(scenario()).items():
            with self.subTest(topic=topic, session=name):
                self.assertEqual(_session_lang(seen["own"]), "pt-PT")
                self.assertEqual(_session_lang(seen["counterpart"]), "pt-PT")
                self.assertEqual(seen["counterpart"].context.get("session"),
                                 seen["own"].context.get("session"))

    def test_shape_changing_counterpart_is_reshaped(self):
        async def scenario():
            receiver, sender = await self._clients()
            got = []
            receiver.on("ovos.intent.deregister", lambda m: got.append(m.data))
            await sender.emit(Message("detach_intent",
                                      {"intent_name": "skill.weather:forecast"}))
            await asyncio.sleep(SETTLE)
            await receiver.close()
            await sender.close()
            return got

        self.assertEqual(self._run(scenario()),
                         [{"skill_id": "skill.weather", "intent_name": "forecast"}])
