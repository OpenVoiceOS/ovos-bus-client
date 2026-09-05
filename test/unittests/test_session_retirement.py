"""OVOS-SESSION-2 §2.7/§7 — retirement of the session-push topics.

§2.7: "This specification defines no topic on which any participant pushes
a session at another." §7: "defines no bus topic." appendix/divergences.md
§5.2.1 and §5.5 mark ``ovos.session.update_default`` and
``ovos.session.sync`` retired; the owner ruling is to retire them with a
one-cycle deprecation shim rather than a hard break.

Live-testing against a real messagebus with a pre-spec-tools core (stable
1.3.1) showed that core answers ``ovos.session.update_default`` ONLY when
asked with ``ovos.session.sync`` -- there is no unsolicited push. Dropping
the connect-time REQUEST therefore leaves a fresh client attached to a
long-running old core stuck on its own config-derived default session
forever (it never learns the core's lang, etc). The shim keeps both halves
of the round trip for one cycle:

- both clients still send exactly one deprecated ``ovos.session.sync``
  request on connect, and log a deprecation notice naming the removal
  version;
- the async client keeps its ``ovos.session.update_default`` listener
  (symmetric with the sync client's own long-kept listener) -- the audit's
  objection to #200 was a NEW subscriber shipped with no removal version,
  not the listener as such -- deprecated the same way.

The one surface removed outright is the async client's bare subscriber as
it existed before this change: it now carries a deprecation notice like
every other pre-spec surface in this module, and there is still no
connect-time default-session PUSH (that half was correctly retired by
#328 and stays retired).
"""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch

from ovos_bus_client.client.async_client import AsyncMessageBusClient
from ovos_bus_client.client.client import MessageBusClient
from ovos_bus_client.message import Message
from ovos_bus_client.session import SessionManager


class TestAsyncClientHasUpdateDefaultListener(unittest.TestCase):
    def test_listener_present_and_deprecated(self):
        with patch("ovos_bus_client.client.async_client.load_message_bus_config") as mock_cfg, \
             patch("ovos_bus_client.client.async_client.log_deprecation") as mock_warn:
            mock_cfg.return_value = MagicMock(host="localhost", port=8181,
                                              route="/core", ssl=False)
            bus = AsyncMessageBusClient()
        listeners = bus.emitter.listeners("ovos.session.update_default")
        self.assertEqual(len(listeners), 1)
        self.assertTrue(mock_warn.called)
        self.assertIn("ovos.session.update_default", mock_warn.call_args.args[0])

    def test_handler_method_present(self):
        self.assertTrue(hasattr(AsyncMessageBusClient, "_on_default_session_update"))


class TestAsyncClientConnectRequestsDefaultSession(unittest.IsolatedAsyncioTestCase):
    async def test_connect_emits_exactly_one_session_sync_and_warns(self):
        with patch("ovos_bus_client.client.async_client.load_message_bus_config") as mock_cfg:
            mock_cfg.return_value = MagicMock(host="localhost", port=8181,
                                              route="/core", ssl=False)
            bus = AsyncMessageBusClient()

        ws_mock = AsyncMock()
        ws_mock.send = AsyncMock()
        ws_mock.__aiter__.return_value = iter([])  # empty recv loop

        with patch("ovos_bus_client.client.async_client.websockets.connect",
                   AsyncMock(return_value=ws_mock)), \
             patch("ovos_bus_client.client.async_client.log_deprecation") as mock_warn:
            await bus.connect(retry=False)

        ws_mock.send.assert_awaited_once()
        sent = json.loads(ws_mock.send.await_args.args[0])
        self.assertEqual(sent["type"], "ovos.session.sync")
        connect_warnings = [c for c in mock_warn.call_args_list
                           if "ovos.session.sync request" in c.args[0]]
        self.assertEqual(len(connect_warnings), 1)


class TestSyncClientConnectRequestsDefaultSession(unittest.TestCase):
    def test_on_open_sends_exactly_one_session_sync_and_warns(self):
        client = MessageBusClient()
        client.client = MagicMock()
        client.client.send = MagicMock()
        with patch("ovos_bus_client.client.client.log_deprecation") as mock_warn:
            client.on_open()
        self.assertEqual(client.client.send.call_count, 1)
        sent = json.loads(client.client.send.call_args[0][0])
        self.assertEqual(sent["type"], "ovos.session.sync")
        connect_warnings = [c for c in mock_warn.call_args_list
                           if "ovos.session.sync request" in c.args[0]]
        self.assertEqual(len(connect_warnings), 1)


class TestBareSessionSyncDeprecation(unittest.TestCase):
    def setUp(self):
        self._sessions = dict(SessionManager.sessions)
        self._bus = SessionManager.bus
        SessionManager.bus = None

    def tearDown(self):
        SessionManager.sessions.clear()
        SessionManager.sessions.update(self._sessions)
        SessionManager.bus = self._bus

    def test_bare_sync_still_echoes_and_warns_every_call(self):
        emitted = []

        class FakeBus:
            def emit(self, msg):
                emitted.append(msg)

        SessionManager.bus = FakeBus()
        with patch("ovos_bus_client.session.log_deprecation") as mock_warn:
            SessionManager.handle_session_sync(Message("ovos.session.sync"))
            SessionManager.handle_session_sync(Message("ovos.session.sync"))
        # the echo still fires -- pre-spec surface kept for one cycle
        self.assertEqual([m.msg_type for m in emitted],
                         ["ovos.session.update_default",
                          "ovos.session.update_default"])
        # mocking log_deprecation bypasses ovos_utils' own real-process
        # once-per-caller dedup, so both calls here are observed directly;
        # the assertion is on the exact count (not just "called"), and every
        # call names the removal version.
        self.assertEqual(mock_warn.call_count, 2)
        for c in mock_warn.call_args_list:
            self.assertIn("ovos.session.sync", c.args[0])
            self.assertRegex(c.args[1], r"^\d+\.0\.0$")


if __name__ == "__main__":
    unittest.main()
