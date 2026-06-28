
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
from ovos_bus_client.message import (Message, CollectionMessage, GUIMessage,
                                     encrypt_as_dict, decrypt_from_dict)
from ovos_bus_client.session import SessionManager, Session
from ovos_spec_tools.messages import NamespaceTranslator, SpecMessage

# --- Layer-2 encryption at the transport edge (deprecated) -----------------
#
# OVOS-MSG-1 is transport-agnostic (§7 non-goals): the on-the-wire envelope
# does not define encryption. The client below bolts an AES (GCM) wrapper
# on top of the spec at its send/receive edges, controlled by the
# ``websocket.secret_key`` config. Its matching key-setup half was never
# formally implemented, so the scheme is **deprecated** — a
# :class:`DeprecationWarning` fires whenever it engages.

import json as _json
import warnings as _warnings
from os import environ


def _bus_flag(env_var: str, config_key: str, default: bool = False) -> bool:
    """Read a boolean bus flag from an env var, else the websocket config.

    The env var wins when set; otherwise ``websocket.<config_key>`` is read,
    falling back to ``default``.
    """
    val = environ.get(env_var)
    if val is not None:
        return val.strip().lower() in ("1", "true", "yes", "on")
    try:
        from ovos_config import Configuration
        return bool(Configuration().get("websocket", {}).get(config_key, default))
    except Exception:
        return default


def _encryption_keys():
    """Return ``(secret_key, allow_unencrypted)`` from current config."""
    from ovos_config.config import Configuration
    cfg = Configuration().get("websocket", {})
    secret = cfg.get("secret_key")
    # Empty/missing secret are equivalent — both disable the scheme;
    # honour the same default for ``allow_unencrypted`` in either case.
    allow_clear = cfg.get("allow_unencrypted", not secret)
    return secret, allow_clear


def _maybe_encrypt(serialized: str) -> str:
    """Wrap ``serialized`` in the legacy AES envelope when
    ``websocket.secret_key`` is configured; otherwise pass through."""
    secret, _ = _encryption_keys()
    if not secret:
        return serialized
    _warnings.warn(
        "Layer-2 envelope encryption on the websocket transport is "
        "deprecated; its key-setup half was never formally implemented "
        "and the wrapper will be removed in a future major. Suppress by "
        "unsetting `websocket.secret_key`.",
        DeprecationWarning, stacklevel=3)
    return json_dumps(encrypt_as_dict(secret, serialized))


def _maybe_decrypt(raw):
    """Unwrap the legacy AES envelope on inbound frames when
    ``websocket.secret_key`` is configured. Returns a plain JSON string
    suitable for :meth:`Message.deserialize`. Honours
    ``websocket.allow_unencrypted``."""
    secret, allow_clear = _encryption_keys()
    if not secret:
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    try:
        obj = _json.loads(raw) if isinstance(raw, str) else raw
    except _json.JSONDecodeError:
        if allow_clear:
            return raw
        raise
    if isinstance(obj, dict) and "ciphertext" in obj:
        _warnings.warn(
            "Layer-2 envelope decryption on the websocket transport is "
            "deprecated; its key-setup half was never formally "
            "implemented and the wrapper will be removed in a future "
            "major.",
            DeprecationWarning, stacklevel=3)
        return decrypt_from_dict(secret, obj)
    if not allow_clear:
        raise RuntimeError(
            "received an unencrypted Message but "
            "`websocket.allow_unencrypted` is False")
    return raw


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
        # namespace translation on emit (orthogonal, both ON by default during
        # the migration window so every migrated event travels on BOTH the
        # legacy and the ovos.* topic — any repo can flip its emit OR its listen
        # to ovos.* in any order, with no coordination):
        #  emit_legacy: emitting an ovos.* spec topic also emits the legacy one.
        #  modernize  : emitting a legacy topic also emits the ovos.* spec one.
        self._translator = NamespaceTranslator(
            modernize=_bus_flag("OVOS_BUS_MODERNIZE", "modernize", default=True),
            emit_legacy=_bus_flag("OVOS_BUS_EMIT_LEGACY", "emit_legacy", default=True))
        self._handler_guards = {}        # func -> shared mirror-guard
        self._dedup_registrations = {}   # func -> [(event_name, wrapped), ...]
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
        self.emit(Message(SpecMessage.SESSION_SYNC)) # request default session update

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
        parsed_message = Message.deserialize(_maybe_decrypt(message))
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

        self._send(message)

        # also put the namespace counterpart(s) on the wire (per the flags); the
        # mirrored payload is reshaped into the counterpart topic's shape (identity
        # for payload-compatible renames, a per-topic transform for shape-changing
        # ones) so a consumer on the counterpart topic receives it in *its* shape.
        for topic in self._translator.counterpart_topics(message.msg_type):
            translated = self._translator.translate_payload(
                from_topic=message.msg_type, to_topic=topic, data=message.data)
            self._send(message.forward(topic, translated))

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
        msg = _maybe_encrypt(msg)
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
        # Topics that participate in the namespace migration are wrapped with the
        # shared mirror-guard so a handler subscribed to BOTH the legacy and the
        # ovos.* topic runs once (the migration window's mirror is dropped).
        # Everything else registers straight through.
        if self._translator.is_migrated(event_name):
            # one guard per handler, shared across its registrations, so its
            # legacy on() and its ovos.* on() dedupe against each other.
            guard = self._handler_guards.get(func)
            if guard is None:
                guard = self._translator.new_mirror_guard()
                self._handler_guards[func] = guard

            def wrapped(message=None):
                if guard(message):
                    return
                return func(message)

            self.emitter.on(event_name, wrapped)
            self._dedup_registrations.setdefault(func, []).append((event_name, wrapped))
            return
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
        # resolve it so a migrated on_collect() subscription is also torn down at
        # the dedup layer (whose registration is keyed by the collector wrapper,
        # not the public func).
        target = self.wrapped_funcs.get(func, func)
        if target in self._dedup_registrations:
            regs = self._dedup_registrations[target]
            for ev, wrapped in [r for r in regs if r[0] == event_name]:
                self._remove_normal(ev, wrapped)
                regs.remove((ev, wrapped))
            if not regs:
                self._dedup_registrations.pop(target, None)
                self._handler_guards.pop(target, None)
                if target is not func:
                    self.wrapped_funcs.pop(func, None)
        elif func in self.wrapped_funcs:
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
                self.client.send(_maybe_encrypt(message.serialize()))
            else:
                self.client.send(_maybe_encrypt(json_dumps(message.__dict__)))
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

        parsed_message = GUIMessage.deserialize(_maybe_decrypt(message))
        self.emitter.emit(parsed_message.msg_type, parsed_message)
