# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Async (asyncio) MessageBus client using the websockets library."""

import asyncio
from typing import Any, Callable, List, Optional, Union
from uuid import uuid4

from ovos_utils import json_dumps
from ovos_utils.log import LOG

try:
    from pyee.asyncio import AsyncIOEventEmitter
except ImportError:
    from pyee import AsyncIOEventEmitter

try:
    import websockets
    from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
except ImportError:
    websockets = None  # type: ignore
    ConnectionClosedError = OSError  # type: ignore
    ConnectionClosedOK = OSError  # type: ignore

from ovos_bus_client.conf import load_message_bus_config, MessageBusClientConf
from ovos_bus_client.message import Message, CollectionMessage
from ovos_bus_client.session import SessionManager, Session


class AsyncMessageWaiter:
    """Wait for a single message asynchronously.

    Registers a one-shot handler before emitting a query, then awaits the
    reply without blocking the event loop.

    Arguments:
        bus: AsyncMessageBusClient instance
        message_type: message type(s) to wait for
    """

    def __init__(self, bus: "AsyncMessageBusClient",
                 message_type: Union[str, List[str]]):
        self.bus = bus
        if not isinstance(message_type, list):
            message_type = [message_type]
        self.msg_type = message_type
        self.received_msg: Optional[Message] = None
        self._event = asyncio.Event()
        for mt in self.msg_type:
            self.bus.once(mt, self._handler)

    def _handler(self, message: Message):
        self.received_msg = message
        self._event.set()

    async def wait(self, timeout: float = 3.0) -> Optional[Message]:
        """Await the expected message.

        Arguments:
            timeout: seconds before giving up

        Returns:
            The received Message or None on timeout.
        """
        try:
            await asyncio.wait_for(self._event.wait(), timeout)
        except asyncio.TimeoutError:
            for mt in self.msg_type:
                try:
                    self.bus.remove(mt, self._handler)
                except (ValueError, KeyError):
                    pass
        return self.received_msg


class AsyncMessageCollector:
    """Collect multiple responses to a single query asynchronously.

    Mirrors the synchronous MessageCollector interface but uses asyncio
    primitives so the event loop is never blocked.

    Arguments:
        bus: AsyncMessageBusClient instance
        message: query message to send
        min_timeout: minimum seconds to wait after sending
        max_timeout: maximum seconds to wait for all handlers
        direct_return_func: optional callable; if it returns True for a
            response the collection ends immediately
    """

    def __init__(self, bus: "AsyncMessageBusClient", message: Message,
                 min_timeout: float = 0.2, max_timeout: float = 3.0,
                 direct_return_func: Callable[[Message], Any] = None):
        self.bus = bus
        self.min_timeout = min_timeout
        self.max_timeout = max_timeout
        self.direct_return_func = direct_return_func or (lambda msg: False)
        self.collect_id = str(uuid4())
        self.handlers: dict = {}
        self.responses: dict = {}
        self._lock = asyncio.Lock()
        self._all_collected = asyncio.Event()
        self._queue: asyncio.Queue = asyncio.Queue()
        self.message = message
        self.message.context["__collect_id__"] = self.collect_id

    def _register_handler(self, msg: Message):
        handler_id = msg.data["handler"]
        timeout = msg.data["timeout"]
        if (msg.data["query"] == self.collect_id and
                handler_id not in self.handlers):
            self.handlers[handler_id] = timeout

    def _receive_response(self, msg: Message):
        if msg.data["query"] == self.collect_id:
            self.responses[msg.data["handler"]] = msg
            self.handlers[msg.data["handler"]] = 0
            self._queue.put_nowait(msg)
            if (len(self.responses) == len(self.handlers) or
                    self.direct_return_func(msg)):
                self._queue.put_nowait(None)
                self._all_collected.set()

    def _setup(self):
        base = self.message.msg_type
        self.bus.on(base + ".handling", self._register_handler)
        self.bus.on(base + ".response", self._receive_response)

    def _teardown(self):
        base = self.message.msg_type
        self.bus.remove(base + ".handling", self._register_handler)
        self.bus.remove(base + ".response", self._receive_response)

    async def collect(self) -> List[Message]:
        """Emit the query and wait for all registered handlers to respond."""
        self._setup()
        await self.bus.emit(self.message)
        await asyncio.sleep(self.min_timeout)

        if not self.handlers:
            self._teardown()
            return []

        # Wait for all handlers up to max_timeout
        time_waited = self.min_timeout
        deadline = self.max_timeout - self.min_timeout
        try:
            await asyncio.wait_for(self._all_collected.wait(), deadline)
        except asyncio.TimeoutError:
            pass

        self._teardown()
        return list(self.responses.values())


class AsyncMessageBusClient:
    """Async (asyncio) OVOS MessageBus client.

    Drop-in async equivalent of MessageBusClient.  All I/O methods are
    coroutines; sync helpers (on/once/remove) remain synchronous so that
    existing-style handler registration still works.

    Usage::

        async def main():
            bus = AsyncMessageBusClient()
            await bus.connect()
            await bus.emit(Message("speak", {"utterance": "hello"}))
            reply = await bus.wait_for_message("speak", timeout=5)
            await bus.close()

        asyncio.run(main())
    """

    _config_cache = None

    def __init__(self, host: str = None, port: int = None,
                 route: str = None, ssl: bool = None,
                 cache: bool = False, session=None):
        config_overrides = dict(host=host, port=port, route=route, ssl=ssl)
        if cache and self._config_cache:
            config = self._config_cache
        else:
            config = load_message_bus_config(**config_overrides)
            if cache:
                AsyncMessageBusClient._config_cache = config

        self.config = MessageBusClientConf(config.host, config.port,
                                           config.route, config.ssl)
        self.emitter = AsyncIOEventEmitter()
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._connected = asyncio.Event()
        self._listen_task: Optional[asyncio.Task] = None

        if session:
            SessionManager.update(session)
        else:
            session = SessionManager.default_session
        self.session_id = session.session_id
        self.on("ovos.session.update_default", self._on_default_session_update)

    @staticmethod
    def build_url(host: str, port: int, route: str, ssl: bool) -> str:
        return f"{'wss' if ssl else 'ws'}://{host}:{port}{route}"

    @property
    def url(self) -> str:
        return self.build_url(self.config.host, self.config.port,
                              self.config.route, self.config.ssl)

    @property
    def connected(self) -> bool:
        """Whether the websocket connection is currently up.

        Same attribute name/semantics as ``MessageBusClient.connected_event``
        being set — consumers that only know the sync client's name (e.g.
        ovos-busmon's liveness check) can use ``getattr(bus, "connected",
        None)`` against either client.
        """
        return self._connected.is_set()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self, retry: bool = True, retry_delay: float = 5.0):
        """Open the WebSocket connection and start the receive loop.

        Arguments:
            retry: keep retrying on ConnectionRefused
            retry_delay: seconds between retry attempts
        """
        if websockets is None:
            raise ImportError(
                "AsyncMessageBusClient requires the 'websockets' package. "
                "Install it with: pip install 'ovos-bus-client[async]'"
            )
        while True:
            try:
                self._ws = await websockets.connect(self.url)
                self._connected.set()
                LOG.debug("AsyncMessageBusClient connected to %s", self.url)
                self.emitter.emit("open")
                await self.emit(Message("ovos.session.sync"))
                self._listen_task = asyncio.ensure_future(self._recv_loop())
                return
            except (ConnectionRefusedError, OSError) as e:
                LOG.warning("Connection refused (%s). Retrying in %.1fs…", e, retry_delay)
                if not retry:
                    raise
                await asyncio.sleep(retry_delay)

    async def _recv_loop(self):
        """Receive loop — runs until the connection closes."""
        try:
            async for raw in self._ws:
                await self._on_message(raw)
        except (ConnectionClosedOK, ConnectionClosedError):
            pass
        except Exception as e:
            LOG.exception("AsyncMessageBusClient recv loop error: %s", e)
        finally:
            self._connected.clear()
            self.emitter.emit("close")

    async def _on_message(self, raw: str):
        parsed = Message.deserialize(raw)
        sess = Session.from_message(parsed)
        if sess.session_id != "default":
            SessionManager.update(sess)
        self.emitter.emit("message", raw)
        self.emitter.emit(parsed.msg_type, parsed)

    def _on_default_session_update(self, message: Message):
        new_session = message.data["session_data"]
        sess = Session.deserialize(new_session)
        SessionManager.update(sess, make_default=True)
        LOG.debug("synced default_session")

    async def close(self):
        """Close the WebSocket connection."""
        if self._ws:
            await self._ws.close()
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        self._connected.clear()

    # ------------------------------------------------------------------
    # Emit / wait helpers
    # ------------------------------------------------------------------

    async def emit(self, message: Message):
        """Send a message onto the message bus.

        Arguments:
            message: Message to send
        """
        if "session" not in message.context:
            sess = (SessionManager.sessions.get(self.session_id)
                    or Session(self.session_id))
            message.context["session"] = sess.serialize()

        try:
            await asyncio.wait_for(self._wait_connected(), 10)
        except asyncio.TimeoutError:
            raise ValueError("Timed out waiting for connection")

        await self._send(message)

    async def _send(self, message: Message):
        """Serialize and send a single message over the websocket."""
        if hasattr(message, "serialize"):
            raw = message.serialize()
        else:
            raw = json_dumps(message.__dict__)
        try:
            await self._ws.send(raw)
        except ConnectionClosedError:
            LOG.warning("Could not send %s — connection closed", message.msg_type)
        except Exception:
            LOG.exception("Failed to emit %s", message.msg_type)

    async def _wait_connected(self):
        await self._connected.wait()

    async def wait_for_message(self, message_type: str,
                               timeout: float = 3.0) -> Optional[Message]:
        """Await a message of a specific type.

        Arguments:
            message_type: the message type to wait for
            timeout: seconds before timeout

        Returns:
            The received Message or None on timeout.
        """
        return await AsyncMessageWaiter(self, message_type).wait(timeout)

    async def wait_for_response(self, message: Message,
                                reply_type: Optional[Union[str, List[str]]] = None,
                                timeout: float = 3.0) -> Optional[Message]:
        """Emit a message and await its response.

        Arguments:
            message: message to send
            reply_type: expected response type(s). Defaults to
                        ``<msg_type>.response``.
            timeout: seconds before timeout

        Returns:
            The response Message or None on timeout.
        """
        if isinstance(reply_type, list):
            message_types = reply_type
        elif isinstance(reply_type, str):
            message_types = [reply_type]
        else:
            message_types = [message.msg_type + ".response"]

        waiter = AsyncMessageWaiter(self, message_types)
        await self.emit(message)
        return await waiter.wait(timeout)

    async def collect_responses(self, message: Message,
                                min_timeout: float = 0.2,
                                max_timeout: float = 3.0,
                                direct_return_func: Callable[[Message], Any] =
                                None) -> List[Message]:
        """Emit a query and collect responses from multiple handlers.

        Arguments:
            message: query message
            min_timeout: minimum wait time after emitting
            max_timeout: maximum wait time for all responses
            direct_return_func: optional early-exit predicate

        Returns:
            List of response Messages.
        """
        collector = AsyncMessageCollector(self, message,
                                          min_timeout, max_timeout,
                                          direct_return_func)
        return await collector.collect()

    # ------------------------------------------------------------------
    # Event registration (synchronous — mirrors MessageBusClient)
    # ------------------------------------------------------------------

    def on(self, event_name: str, func: Callable):
        self.emitter.on(event_name, func)

    def once(self, event_name: str, func: Callable):
        self.emitter.once(event_name, func)

    def remove(self, event_name: str, func: Callable):
        try:
            self.emitter.remove_listener(event_name, func)
        except (ValueError, KeyError):
            LOG.warning("Failed to remove listener %s: %s", event_name, func)

    def remove_all_listeners(self, event_name: str):
        self.emitter.remove_all_listeners(event_name)
