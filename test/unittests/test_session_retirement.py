"""OVOS-SESSION-2 §2.7/§7 — retirement of the session-push topics.

§2.7: "This specification defines no topic on which any participant pushes
a session at another." §7: "defines no bus topic." appendix/divergences.md
§5.2.1 and §5.5 mark ``ovos.session.update_default`` and
``ovos.session.sync`` retired; the owner ruling is to retire them with a
one-cycle deprecation shim rather than a hard break.

The connect-time REQUEST is gone as of T-6516. It could not be sent
harmlessly: ``emit`` stamps ``context["session"]`` with ``_own_session()``
on any message carrying none, so a default-id client sent its OWN
config-derived default session, and an orchestrator folds a present field
onto its store per §5.1. Measured on a real bus: one connect by a process
without the user's ``mycroft.conf`` moved an ``nl-NL`` store to ``en-US``,
with no utterance, and core then broadcast that to every process.

§2.7 rules the request out and supplies the replacement: "Each derives its
initial view of that session from the deployment configuration ... no
handshake, bootstrap request, or announcement is needed to make them
agree." appendix/divergences.md §5.5 already scoped the removal, naming the
bare-sync bootstrap on connect. What is given up is the pre-spec round trip
-- a core older than ovos-spec-tools answers
``ovos.session.update_default`` only when asked -- so against such a core a
fresh client keeps its own configured default until the next broadcast.

- neither client sends anything on connect;
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


class TestAsyncClientConnectSendsNothing(unittest.IsolatedAsyncioTestCase):
    async def test_connect_emits_no_session_sync(self):
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

        ws_mock.send.assert_not_awaited()
        connect_warnings = [c for c in mock_warn.call_args_list
                           if "ovos.session.sync request" in c.args[0]]
        self.assertEqual(connect_warnings, [])


class TestSyncClientConnectSendsNothing(unittest.TestCase):
    def test_on_open_sends_nothing(self):
        client = MessageBusClient()
        client.client = MagicMock()
        client.client.send = MagicMock()
        client.on_open()
        client.flush()
        self.assertEqual(client.client.send.call_count, 0,
                         f"connect sent {client.client.send.call_args_list}")


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
