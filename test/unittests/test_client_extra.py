"""Coverage tests for ovos_bus_client.client.client — MessageBusClient
handler/emit/wait helpers and GUIWebsocketClient construction."""
import json
import unittest
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

from websocket import WebSocketConnectionClosedException

from ovos_bus_client.client.client import (GUIWebsocketClient,
                                           MessageBusClient)
from ovos_bus_client.message import GUIMessage, Message


class TestBuildUrl(TestCase):
    def test_ws_scheme(self):
        self.assertEqual(
            MessageBusClient.build_url("h", 1, "/r", False),
            "ws://h:1/r",
        )

    def test_wss_scheme(self):
        self.assertEqual(
            MessageBusClient.build_url("h", 1, "/r", True),
            "wss://h:1/r",
        )


class TestHandlerRegistration(TestCase):
    def setUp(self):
        self.client = MessageBusClient()
        # ensure no socket activity
        self.client.client = MagicMock()
        self.client.connected_event.set()

    def test_on_then_emit_invokes_handler(self):
        called = []
        self.client.on("hi", lambda m: called.append(m))
        # emit dispatches via socket — bypass and call emitter directly
        msg = Message("hi", {"x": 1})
        self.client.emitter.emit("hi", msg)
        self.assertEqual(len(called), 1)
        self.assertIs(called[0], msg)

    def test_once_fires_only_once(self):
        called = []
        self.client.once("evt", lambda m: called.append(m))
        self.client.emitter.emit("evt", Message("evt"))
        self.client.emitter.emit("evt", Message("evt"))
        self.assertEqual(len(called), 1)

    def test_remove_normal_handler(self):
        cb = lambda m: None
        self.client.on("evt", cb)
        self.client.remove("evt", cb)
        # second remove should not raise
        self.client.remove("evt", cb)

    def test_remove_all_listeners(self):
        self.client.on("evt", lambda m: None)
        self.client.on("evt", lambda m: None)
        self.client.remove_all_listeners("evt")
        self.assertEqual(len(self.client.emitter.listeners("evt")), 0)

    def test_remove_all_listeners_none_raises(self):
        with self.assertRaises(ValueError):
            self.client.remove_all_listeners(None)


class TestEmit(TestCase):
    def setUp(self):
        self.client = MessageBusClient()
        self.client.client = MagicMock()
        self.client.connected_event.set()

    def test_emit_sends_serialized_message(self):
        # a non-migrated topic, so namespace translation does not add a second send
        self.client.emit(Message("test.message", {"utterance": "hi"}))
        self.assertTrue(self.client.client.send.called)
        payload = self.client.client.send.call_args[0][0]
        decoded = json.loads(payload)
        self.assertEqual(decoded["type"], "test.message")
        self.assertEqual(decoded["data"]["utterance"], "hi")

    def test_emit_uses_send_lock(self):
        class _Lock:
            entered = False

            def __enter__(self):
                self.entered = True

            def __exit__(self, exc_type, exc, tb):
                return False

        lock = _Lock()
        self.client._send_lock = lock

        self.client.emit(Message("test.message"))

        self.assertTrue(lock.entered)

    def test_emit_checked_raises_send_failures(self):
        self.client.client.send.side_effect = WebSocketConnectionClosedException()

        with self.assertRaises(WebSocketConnectionClosedException):
            self.client.emit_checked(Message("test.message"))


class TestWaitForMessage(TestCase):
    def setUp(self):
        self.client = MessageBusClient()
        self.client.client = MagicMock()
        self.client.connected_event.set()

    def test_wait_returns_matching_message(self):
        from threading import Thread, Event
        ready = Event()

        def feed():
            ready.wait(timeout=2)
            self.client.emitter.emit("evt", Message("evt", {"v": 1}))

        Thread(target=feed, daemon=True).start()
        ready.set()
        got = self.client.wait_for_message("evt", timeout=1.0)
        self.assertIsNotNone(got)
        self.assertEqual(got.msg_type, "evt")

    def test_wait_returns_none_on_timeout(self):
        self.assertIsNone(self.client.wait_for_message("nothing", timeout=0.1))


class TestWaitForResponse(TestCase):
    def setUp(self):
        self.client = MessageBusClient()
        self.client.client = MagicMock()
        self.client.connected_event.set()

    def test_default_reply_type_is_msg_type_response(self):
        msg = Message("foo")

        def fake_send(_):
            self.client.emitter.emit("foo.response", Message("foo.response", {"ok": True}))

        self.client.client.send.side_effect = fake_send
        reply = self.client.wait_for_response(msg, timeout=1.0)
        self.assertIsNotNone(reply)
        self.assertEqual(reply.msg_type, "foo.response")

    def test_explicit_string_reply_type(self):
        msg = Message("ping")

        def fake_send(_):
            self.client.emitter.emit("pong", Message("pong"))

        self.client.client.send.side_effect = fake_send
        reply = self.client.wait_for_response(msg, reply_type="pong", timeout=1.0)
        self.assertEqual(reply.msg_type, "pong")

    def test_list_reply_types_first_match_wins(self):
        msg = Message("ping")

        def fake_send(_):
            self.client.emitter.emit("alt", Message("alt"))

        self.client.client.send.side_effect = fake_send
        reply = self.client.wait_for_response(
            msg, reply_type=["main", "alt"], timeout=1.0,
        )
        self.assertEqual(reply.msg_type, "alt")

    def test_returns_none_on_timeout(self):
        self.assertIsNone(self.client.wait_for_response(
            Message("nothing"), timeout=0.1,
        ))


class TestCollectResponses(TestCase):
    def setUp(self):
        self.client = MessageBusClient()
        self.client.client = MagicMock()
        self.client.connected_event.set()

    def test_collect_no_handlers_returns_empty(self):
        results = self.client.collect_responses(
            Message("question:query"),
            min_timeout=0.1, max_timeout=0.3,
        )
        self.assertEqual(results, [])

    def test_on_collect_emits_handling_ack(self):
        import time as _time
        acked = []
        self.client.client.send.side_effect = lambda raw: acked.append(json.loads(raw))

        def handler(cmessage):
            # the wrapper has already emitted the .handling ack
            pass

        self.client.on_collect("question:query", handler, timeout=1)
        msg = Message("question:query", {"phrase": "x"}, {"__collect_id__": "qid"})
        self.client.emitter.emit("question:query", msg)
        # pyee's ExecutorEventEmitter dispatches on a thread pool; wait briefly
        deadline = _time.monotonic() + 1.0
        while _time.monotonic() < deadline and not acked:
            _time.sleep(0.01)
        handling = [m for m in acked if m["type"] == "question:query.handling"]
        self.assertEqual(len(handling), 1)
        self.assertEqual(handling[0]["data"]["query"], "qid")


class TestOnMessageDispatch(TestCase):
    def setUp(self):
        self.client = MessageBusClient()
        self.client.client = MagicMock()
        self.client.connected_event.set()

    def test_on_message_parses_and_dispatches(self):
        got = []
        self.client.on("hello", lambda m: got.append(m))
        raw = Message("hello", {"x": 1}).serialize()
        self.client.on_message(raw)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].msg_type, "hello")

    def test_on_message_emits_raw_message_event(self):
        got_raw = []
        self.client.on("message", lambda raw: got_raw.append(raw))
        raw = Message("hello").serialize()
        self.client.on_message(raw)
        self.assertEqual(got_raw, [raw])


class TestLifecycle(TestCase):
    def test_close_clears_connected_event(self):
        client = MessageBusClient()
        client.client = MagicMock()
        client.connected_event.set()
        client.close()
        self.assertFalse(client.connected_event.is_set())

    def test_run_in_thread_returns_thread(self):
        client = MessageBusClient()
        client.client = MagicMock()
        # run_forever blocks on the underlying client.run_forever
        # which is a MagicMock returning immediately
        t = client.run_in_thread()
        self.assertTrue(t.daemon)

    def test_on_open_sets_connected_and_syncs(self):
        client = MessageBusClient()
        client.client = MagicMock()
        client.client.send = MagicMock()
        client.on_open()
        self.assertTrue(client.connected_event.is_set())
        # ovos.session.sync emit
        self.assertTrue(client.client.send.called)

    def test_on_close_emits_close_event(self):
        client = MessageBusClient()
        seen = []
        client.emitter.on("close", lambda *_: seen.append(True))
        client.on_close()
        self.assertEqual(seen, [True])


class TestGUIWebsocketClient(TestCase):
    def test_construct_and_emit(self):
        gui = GUIWebsocketClient()
        gui.client = MagicMock()
        gui.connected_event.set()
        gui.emit(GUIMessage("gui.value.set", values={"a": 1}))
        self.assertTrue(gui.client.send.called)
        payload = json.loads(gui.client.send.call_args[0][0])
        self.assertEqual(payload["type"], "gui.value.set")

    def test_gui_id_is_set(self):
        gui = GUIWebsocketClient(client_name="my-gui")
        self.assertTrue(gui.gui_id.startswith("my-gui_"))


if __name__ == "__main__":
    unittest.main()
