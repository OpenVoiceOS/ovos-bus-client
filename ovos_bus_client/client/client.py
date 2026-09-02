
import time

from ovos_utils import json_dumps

from os import getpid
from threading import Event, Thread
from typing import Union, Callable, Any, List, Optional
from uuid import uuid4

from ovos_utils.log import LOG
try:
    from pyee import ExecutorEventEmitter
except (ImportError, ModuleNotFoundError):
    from pyee.executor import ExecutorEventEmitter

from websocket import (WebSocketApp,
                       WebSocketConnectionClosedException,
                       WebSocketException)

from ovos_bus_client.client.collector import MessageCollector
from ovos_bus_client.client.waiter import MessageWaiter
from ovos_bus_client.conf import load_message_bus_config, MessageBusClientConf, load_gui_message_bus_config
from ovos_bus_client.message import Message, CollectionMessage, GUIMessage, MalformedMessage
from ovos_bus_client.session import SessionManager, Session, MalformedSession
from ovos_spec_tools.messages import SpecMessage


class MessageBusClient:
    """The Mycroft Messagebus Client

    The Messagebus client connects to the Mycroft messagebus service
    and allows communication with the system. It has been extended to work
    like the pyee EventEmitter and tries to offer as much convenience as
    possible to the developer.
    """
    # minimize reading of the .conf
    _config_cache = None
    # class-level default so a test double built via __new__ (bypassing
    # __init__) still reads a real bool instead of raising AttributeError.
    _closing = False

    def __init__(self, host=None, port=None, route=None, ssl=None,
                 emitter=None, cache=False, session=None):
        config_overrides = dict(host=host, port=port, route=route, ssl=ssl)
        if cache and self._config_cache:
            config = self._config_cache
        else:
            config = load_message_bus_config(**config_overrides)
            if cache:
                MessageBusClient._config_cache = config

        self.config = MessageBusClientConf(config.host, config.port,
                                           config.route, config.ssl)
        self.emitter = emitter or ExecutorEventEmitter()
        self.client = self.create_client()
        self.retry = 5
        self.connected_event = Event()
        self.started_running = False
        # Set by close() to short-circuit the reconnect-backoff recursion in
        # on_error(): that handler sleeps, recreates the websocket and
        # recurses into run_forever() on the SAME thread run_in_thread()
        # started, so close()ing only the currently-active websocket object
        # does not stop a client that is mid-backoff -- it just reconnects
        # again after close() has already returned control to the caller.
        self._closing = False
        self._run_thread = None
        self.wrapped_funcs = {}
        if session:
            SessionManager.update(session)
        else:
            session = SessionManager.default_session

        self.session_id = session.session_id
        self.on("ovos.session.update_default",
                self.on_default_session_update)

    @staticmethod
    def build_url(host: str, port: int, route: str, ssl: bool) -> str:
        """
        Build a websocket url.
        """
        return f"{'wss' if ssl else 'ws'}://{host}:{port}{route}"

    def create_client(self) -> WebSocketApp:
        """
        Setup websocket client.
        """
        url = self.build_url(ssl=self.config.ssl,
                             host=self.config.host,
                             port=self.config.port,
                             route=self.config.route)
        return WebSocketApp(url, on_open=self.on_open, on_close=self.on_close,
                            on_error=self.on_error, on_message=self.on_message)

    def on_open(self, *args):
        """
        Handle the "open" event from the websocket.
        A Basic message with the name "open" is forwarded to the emitter.
        """
        LOG.debug("Connected")
        self.connected_event.set()
        try:
            self.emitter.emit("open")
        except RuntimeError as e:
            LOG.debug(f'Emitter refused open event during shutdown: {e}')
            return
        # Restore reconnect timer to 5 seconds on sucessful connect
        self.retry = 5
        self.emit(Message(SpecMessage.SESSION_SYNC)) # request default session update

    def on_close(self, *args):
        """
        Handle the "close" event from the websocket.
        A Basic message with the name "close" is forwarded to the emitter.
        """
        self.connected_event.clear()
        try:
            self.emitter.emit("close")
        except RuntimeError as e:
            LOG.debug(f'Emitter refused close event during shutdown: {e}')

    def on_error(self, *args):
        """
        On error start trying to reconnect to the websocket.
        """
        if len(args) == 1:
            error = args[0]
        else:
            error = args[1]
        # websocket-client may invoke on_error with a non-exception object
        # (e.g. a websocket._abnf.ABNF control frame) that is not a connection
        # error. Wrapping it in a RuntimeError and re-emitting used to trigger a
        # spurious close()+reconnect cycle that could stall an in-progress
        # handshake. A non-exception callback is not a fatal error: log and
        # return without tearing the connection down.
        if not isinstance(error, BaseException):
            LOG.debug("ignoring non-exception websocket error callback: %r",
                      error)
            return
        self.connected_event.clear()
        if isinstance(error, WebSocketConnectionClosedException):
            LOG.warning('Could not send message because connection has closed')
        elif isinstance(error, ConnectionRefusedError):
            LOG.warning('Connection Refused. Is Messagebus Service running?')
        elif isinstance(error, ConnectionResetError):
            LOG.warning('Connection Reset. Did the Messagebus Service stop?')
        else:
            LOG.exception('=== %s ===', repr(error))
            try:
                self.emitter.emit('error', error)
            except RuntimeError as e:
                LOG.debug(f'Emitter refused error event during shutdown: {e}')
            except Exception as e:
                LOG.exception(f'Failed to emit error event: {e}')

        try:
            if self.client.keep_running:
                self.client.close()
        except Exception as e:
            LOG.error(f'Exception closing websocket at {self.client.url}: {e}')

        if self._closing:
            return

        LOG.warning("Message Bus Client "
                    "will reconnect in %.1f seconds.", self.retry)
        time.sleep(self.retry)
        if self._closing:
            return
        self.retry = min(self.retry * 2, 60)
        try:
            if self._closing:
                return
            self.emitter.emit('reconnecting')
            if self._closing:
                return
            self.client = self.create_client()
            self.run_forever()
        except RuntimeError as e:
            LOG.debug(f'Emitter refused reconnecting event during shutdown: {e}')
        except WebSocketException:
            pass

    def on_message(self, *args):
        """
        Handle an incoming websocket message
        @param args:
            message (str): serialized Message
        """
        if len(args) == 1:
            message = args[0]
        else:
            message = args[1]
        try:
            parsed_message = Message.deserialize(message)
        except MalformedMessage as e:
            # A malformed frame is a per-message fault, not a transport fault:
            # discard it and keep the connection. Letting it propagate would
            # reach on_error, which tears the socket down and reconnects — so a
            # single bad message (e.g. a non-conformant server greeting) would
            # otherwise trigger an endless reconnect loop.
            LOG.warning("discarding malformed bus message: %s", e)
            return
        try:
            sess = Session.from_message(parsed_message)
        except MalformedSession as e:
            # A non-object session carrier is a per-message producer fault, not a
            # transport fault (SESSION-1 §2.5): drop this one message and keep the
            # connection. Letting it propagate would reach on_error and reconnect,
            # so a single bad producer could hold the client in a reconnect loop.
            LOG.warning("discarding bus message with malformed session: %s", e)
            return
        if sess.session_id != "default": # 'default' can only be updated by core
            SessionManager.update(sess)
        try:
            self.emitter.emit('message', message)
            self.emitter.emit(parsed_message.msg_type, parsed_message)
        except RuntimeError as e:
            LOG.debug(f'Emitter refused message dispatch during shutdown: {e}')

    def on_default_session_update(self, message):
        new_session = message.data["session_data"]
        sess = Session.deserialize(new_session)
        # the broadcast payload is default_session.serialize(), so it already
        # carries session_id == "default"; the singleton store syncs
        # default_session by id (no make_default rewrite needed).
        SessionManager.update(sess)
        LOG.debug("synced default_session")

    def emit(self, message: Message):
        """
        Send a message onto the message bus.

        This will both send the message to the local process using the
        event emitter and onto the Mycroft websocket for other processes.

        Args:
            message (Message): Message to send
        """
        if "session" not in message.context:
            sess = SessionManager.sessions.get(self.session_id) or \
                   Session(self.session_id)
            message.context["session"] = sess.serialize()
        self._send(message)

    def _send(self, message: Message):
        """Serialize and send a single message over the websocket."""
        if not self.connected_event.wait(10):
            if not self.started_running:
                raise ValueError('You must execute run_forever() '
                                 'before emitting messages')
            self.connected_event.wait()

        if hasattr(message, 'serialize'):
            msg = message.serialize()
        else:
            msg = json_dumps(message.__dict__)
        try:
            self.client.send(msg)
        except WebSocketConnectionClosedException:
            LOG.warning(f'Could not send {message.msg_type} message because connection '
                        'has been closed')
        except Exception as e:
            LOG.exception(f"failed to emit message {message.msg_type} with len {len(msg)}")

    def collect_responses(self, message: Message,
                          min_timeout: Union[int, float] = 0.2,
                          max_timeout: Union[int, float] = 3.0,
                          direct_return_func: Callable[[Message], Any] =
                          lambda msg: False) -> List[Message]:
        """
        Collect responses from multiple handlers.

        This sets up a collect-call (pun intended) expecting multiple handlers
        to respond.

        Args:
            message (Message): message to send
            min_timeout (int/float): Minimum time to wait for a response
            max_timeout (int/float): Maximum allowed time to wait for an answer
            direct_return_func (callable): Optional function for allowing an
                early return (not all registered handlers need to respond)

            Returns:
                (list) collected response messages.
        """
        collector = MessageCollector(self, message,
                                     min_timeout, max_timeout,
                                     direct_return_func)
        return collector.collect()

    def on_collect(self, event_name: str,
                   func: Callable[[CollectionMessage], Any],
                   timeout: Union[int, float] = 2):
        """
        Create a handler for a collect_responses call.

        This immeditely responds with an ack to register the handler with
        the caller, promising to return a response.

        The handler function then needs to send a response.

        Args:
            event_name (str): Message type to listen for.
            func (callable): function / method do be called for processing the
                             message.
            timeout (int/float): optional timeout of the handler
        """

        def wrapper(msg):
            collect_id = msg.context['__collect_id__']
            handler_id = str(uuid4())
            # Immediately respond that something is working on the issue
            acknowledge = msg.reply(msg.msg_type + '.handling',
                                    data={'query': collect_id,
                                          'handler': handler_id,
                                          'timeout': timeout})
            self.emit(acknowledge)
            func(CollectionMessage.from_message(msg, handler_id, collect_id))

        self.wrapped_funcs[func] = wrapper
        self.on(event_name, wrapper)

    def wait_for_message(self, message_type: str,
                         timeout: Union[int, float] = 3.0) -> Optional[Message]:
        """
        Wait for a message of a specific type.

        Arguments:
            message_type (str): the message type of the expected message
            timeout: seconds to wait before timeout, defaults to 3

        Returns:
            The received message or None if the response timed out
        """

        return MessageWaiter(self, message_type).wait(timeout)

    def wait_for_response(self, message: Message,
                          reply_type: Optional[Union[str, List[str]]] = None,
                          timeout: Union[float, int] = 3.0) -> \
            Optional[Message]:
        """
        Send a message and wait for a response.

        Arguments:
            message (Message): message to send
            reply_type (str | List[str]): the message type(s) of the expected reply.
                              Defaults to "<message.msg_type>.response".
            timeout: seconds to wait before timeout, defaults to 3

        Returns:
            The received message or None if the response timed out
        """
        message_type = None
        if isinstance(reply_type, list):
            message_type = reply_type
        elif isinstance(reply_type, str):
            message_type = [reply_type]
        elif reply_type is None:
            message_type = [message.msg_type + '.response']
        waiter = MessageWaiter(self, message_type)  # Setup response handler
        # Send message and wait for its response
        self.emit(message)
        return waiter.wait(timeout)

    def on(self, event_name: str, func: Callable[[Message], Any]):
        """Register callback with event emitter.

        Args:
            event_name (str): message type to map to the callback
            func (callable): callback function
        """
        self.emitter.on(event_name, func)

    def once(self, event_name: str, func: Callable[[Message], Any]):
        """Register callback with event emitter for a single call.

        Args:
            event_name (str): message type to map to the callback
            func (callable): callback function
        """
        self.emitter.once(event_name, func)

    def remove(self, event_name: str, func: Callable[[Message], Any]):
        """Remove registered event.

        Args:
            event_name (str): message type to map to the callback
            func (callable): callback function
        """
        # on_collect() registers a collector wrapper (tracked in wrapped_funcs);
        # resolve it so a on_collect() subscription is also torn down through
        # the wrapper pyee actually holds.
        if func in self.wrapped_funcs:
            self._remove_wrapped(event_name, func)
        else:
            self._remove_normal(event_name, func)

    def _remove_wrapped(self, event_name, external_func):
        """Remove a wrapped function."""

        wrapper = self.wrapped_funcs.pop(external_func)
        self._remove_normal(event_name, wrapper)

    def _remove_normal(self, event_name, func):
        try:
            if event_name not in self.emitter._events:
                LOG.debug("Not able to find '%s'", event_name)
            self.emitter.remove_listener(event_name, func)
        except (ValueError, KeyError):
            LOG.warning('Failed to remove event %s: %s',
                        event_name, str(func))
            if event_name not in self.emitter._events:
                LOG.debug("Not able to find '%s'", event_name)

    def remove_all_listeners(self, event_name: str):
        """
        Remove all listeners connected to event_name.

        Arguments:
            event_name: event from which to remove listeners
        """
        if event_name is None:
            raise ValueError
        self.emitter.remove_all_listeners(event_name)

    def run_forever(self):
        """
        Start the websocket handling.
        """
        self.started_running = True
        self.client.run_forever()

    def close(self):
        """
        Close the websocket connection.

        Also stops a client that is currently inside on_error()'s
        reconnect-backoff (sleep -> recreate websocket -> recurse into
        run_forever(), all on the same thread): without this flag that
        recursion is unaffected by closing the momentarily-active websocket
        object, and the receiver thread survives close() indefinitely.
        """
        self._closing = True
        self.client.close()
        self.connected_event.clear()
        if self._run_thread is not None:
            self._run_thread.join(timeout=2)
            self._run_thread = None
        # Shut the emitter down LAST, after client.close() has had its join
        # window to let the real on_close/on_error callbacks fire while the
        # emitter is still alive -- otherwise a normal, synchronous close()
        # would silently drop the 'close'/'error' event a live disconnect
        # legitimately delivers. This still guarantees the emitter is torn
        # down before close() returns for the embedder-forgets-to-wait case
        # that motivated this fix: ExecutorEventEmitter's
        # ThreadPoolExecutor.submit() otherwise survives until interpreter
        # shutdown, where it raises "cannot schedule new futures after
        # interpreter shutdown".
        if hasattr(self.emitter, "shutdown"):
            self.emitter.shutdown(wait=False)

    def run_in_thread(self):
        """Launches the run_forever in a separate daemon thread."""
        # Reset BEFORE the thread starts, not inside run_forever(): if the
        # reset happened in run_forever() a close() landing between the
        # thread's creation and it actually reaching run_forever() would be
        # undone the instant the thread got there, and the client would
        # reconnect right after being told to close.
        self._closing = False
        t = Thread(target=self.run_forever)
        # daemon=True so an embedder that forgets to close() is never blocked
        # at interpreter exit; close() still joins it with a timeout when the
        # caller does the right thing.
        t.daemon = True
        t.start()
        self._run_thread = t
        return t


class GUIWebsocketClient(MessageBusClient):

    def __init__(self, host=None, port=None, route=None, ssl=None,
                 emitter=None, cache=False, client_name="ovos-gui-client"):
        self.gui_id = f"{client_name}_{getpid()}"
        config_overrides = dict(host=host, port=port, route=route, ssl=ssl)
        config = load_gui_message_bus_config(**config_overrides)
        super().__init__(host=config.host, port=config.port, route=config.route,
                         ssl=config.ssl, emitter=emitter, cache=cache)

    def emit(self, message: GUIMessage):
        """
        Send a message onto the message bus.

        This will both send the message to the local process using the
        event emitter and onto the Mycroft websocket for other processes.

        Args:
            message (GUIMessage): Message to send
        """

        if not self.connected_event.wait(10):
            if not self.started_running:
                raise ValueError('You must execute run_forever() '
                                 'before emitting messages')
            self.connected_event.wait()

        try:
            if hasattr(message, 'serialize'):
                self.client.send(message.serialize())
            else:
                self.client.send(json_dumps(message.__dict__))
        except WebSocketConnectionClosedException:
            LOG.warning('Could not send %s message because connection '
                        'has been closed', message.msg_type)

    def on_open(self, *args):
        super().on_open(*args)
        self.emit(GUIMessage("mycroft.gui.connected",
                             gui_id=self.gui_id))

    def on_message(self, *args):
        """
        Handle an incoming websocket message
        @param args:
            message (str): serialized Message
        """
        if len(args) == 1:
            message = args[0]
        else:
            message = args[1]

        self.emitter.emit('message', message)

        try:
            parsed_message = GUIMessage.deserialize(message)
        except MalformedMessage as e:
            # Discard a malformed frame instead of letting it tear down the
            # GUI websocket via on_error (see the core on_message handler).
            LOG.warning("discarding malformed GUI message: %s", e)
            return
        self.emitter.emit(parsed_message.msg_type, parsed_message)
