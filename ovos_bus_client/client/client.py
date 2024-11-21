import orjson
import time
import traceback
from os import getpid
from threading import Event, Thread
from typing import Union, Callable, Any, List, Optional
from uuid import uuid4

from ovos_utils.log import LOG, deprecated
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
from ovos_bus_client.message import Message, CollectionMessage, GUIMessage
from ovos_bus_client.session import SessionManager, Session


class MessageBusClient:
    """The Mycroft Messagebus Client

    The Messagebus client connects to the Mycroft messagebus service
    and allows communication with the system. It has been extended to work
    like the pyee EventEmitter and tries to offer as much convenience as
    possible to the developer.
    """
    # minimize reading of the .conf
    _config_cache = None

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
        self.emitter.emit("open")
        # Restore reconnect timer to 5 seconds on sucessful connect
        self.retry = 5
        self.emit(Message("ovos.session.sync")) # request default session update

    def on_close(self, *args):
        """
        Handle the "close" event from the websocket.
        A Basic message with the name "close" is forwarded to the emitter.
        """
        self.emitter.emit("close")

    def on_error(self, *args):
        """
        On error start trying to reconnect to the websocket.
        """
        if len(args) == 1:
            error = args[0]
        else:
            error = args[1]
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
            except Exception as e:
                LOG.exception(f'Failed to emit error event: {e}')

        try:
            if self.client.keep_running:
                self.client.close()
        except Exception as e:
            LOG.error(f'Exception closing websocket at {self.client.url}: {e}')

        LOG.warning("Message Bus Client "
                    "will reconnect in %.1f seconds.", self.retry)
        time.sleep(self.retry)
        self.retry = min(self.retry * 2, 60)
        try:
            self.emitter.emit('reconnecting')
            self.client = self.create_client()
            self.run_forever()
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
        parsed_message = Message.deserialize(message)
        sess = Session.from_message(parsed_message)
        if sess.session_id != "default": # 'default' can only be updated by core
            SessionManager.update(sess)
        self.emitter.emit('message', message)
        self.emitter.emit(parsed_message.msg_type, parsed_message)

    def on_default_session_update(self, message):
        new_session = message.data["session_data"]
        sess = Session.deserialize(new_session)
        SessionManager.update(sess, make_default=True)
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

        if not self.connected_event.wait(10):
            if not self.started_running:
                raise ValueError('You must execute run_forever() '
                                 'before emitting messages')
            self.connected_event.wait()

        if hasattr(message, 'serialize'):
            msg = message.serialize()
        else:
            msg = orjson.dumps(message.__dict__).decode("utf-8")
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
        """
        self.client.close()
        self.connected_event.clear()

    def run_in_thread(self):
        """Launches the run_forever in a separate daemon thread."""
        t = Thread(target=self.run_forever)
        t.daemon = True
        t.start()
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
                self.client.send(orjson.dumps(message.__dict__).decode("utf-8"))
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

        parsed_message = GUIMessage.deserialize(message)
        self.emitter.emit(parsed_message.msg_type, parsed_message)
