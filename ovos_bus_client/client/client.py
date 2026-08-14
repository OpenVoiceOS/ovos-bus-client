
import time
from copy import deepcopy

from ovos_utils import json_dumps

from os import getpid
import queue as _queue
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
                                     MalformedMessage,
                                     encrypt_as_dict, decrypt_from_dict)
from ovos_bus_client.session import SessionManager, Session, MalformedSession
from ovos_spec_tools.messages import NamespaceTranslator, SpecMessage

# --- legacy intent-topic compat (non-normative migration tooling) ----------
#
# Old ovos-workshop releases built the per-intent dispatch topic from the
# padatious resource FILENAME, so the ``.intent`` extension leaked onto the
# wire: a skill with ``food.order.intent`` listened on
# ``<skill_id>:food.order.intent``. Current workshop is spec-pure and
# registers the canonical ``<skill_id>:food.order`` (OVOS-MSG-1 §2.1.1).
#
# Both halves of the version skew are real, so the bridge is two rules, each
# stateless and each one ``if`` block:
#
#   RULE 1 (send)    -- every canonical intent topic emitted also goes out as
#                       its ``.intent``-suffixed twin, marked as a twin. An old
#                       skill container listens only on the suffixed topic and
#                       runs a bus-client too old to bridge anything, so only a
#                       real wire frame reaches it. The canonical frame is sent
#                       first.
#   RULE 2 (receive) -- every suffixed intent topic received WITHOUT the twin
#                       marker is also dispatched locally under its canonical
#                       spelling, so a spec-pure skill hears an old core.
#
# The marker is the whole deduplication. A canonical frame plus its marked twin
# fires the canonical handlers exactly once, because rule 2 ignores marked
# frames. Unmarked suffixed traffic comes from a genuinely old emitter and is
# modernized. Nothing tracks who listens to what: a twin nobody listens to is a
# few ignored bytes.
#
# Turning the bridge off is deleting the two ``if`` blocks. Until then it rides
# the same ``emit_legacy`` flag as the namespace bridge.
from ovos_spec_tools.intent_topics import (canonical_intent_topic,
                                           intent_topic_counterpart,
                                           is_intent_topic,
                                           legacy_intent_topic)

def _verbatim_copy(message: Message, topic: str) -> Message:
    """Retopic ``message`` onto ``topic``, carrying its context byte-for-byte.

    NOT :meth:`Message.forward`. ``forward()`` re-stamps the session through
    ``SessionManager.sync_message_session``, and for the ``default`` session
    that REPLACES the carried session with the emitting process's own — the
    twin then leaves with a different ``lang`` and an emptied ``active_skills``
    than the canonical frame it is supposed to mirror. A compat twin is the
    same logical dispatch under a second spelling, so its routing, session and
    language must be identical, and its fingerprint must match the canonical
    frame's or the receive-side pair guard cannot pair them.
    """
    return Message(topic, data=deepcopy(message.data),
                   context=deepcopy(message.context))


#: Context flag stamped on a twin intent frame. Its presence means the
#: canonical spelling of this dispatch was sent alongside it, so a receiver
#: that understands the bridge must not modernize the twin a second time.
INTENT_COMPAT_TWIN_KEY = "_intent_compat_twin"

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
    # class-level defaults so instances built without __init__ (tests,
    # partial constructions) keep the synchronous sender
    _sender_queue = None
    _sender_thread = None

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
        # Optional single-writer outbound queue (``websocket.async_sender``).
        # Every emitter thread otherwise serializes on the websocket's send
        # lock: in a process with many worker threads (ovos-core runs ~20)
        # each emit costs GIL churn plus lock wait -- measured ~23ms per emit
        # under a 400-client load vs ~1ms idle. With the async sender, emit()
        # enqueues the serialized frame (microseconds) and one daemon thread
        # owns the socket; ordering is preserved (writes were serialized
        # anyway) and total throughput is unchanged. Trade-off: send errors
        # surface in the sender thread's log instead of the caller -- which
        # matches the real delivery contract (a successful socket write never
        # guaranteed processing).
        self._sender_queue = None
        self._sender_thread = None
        if _bus_flag("OVOS_BUS_ASYNC_SENDER", "async_sender", default=False):
            self._sender_queue = _queue.Queue(maxsize=5000)
            self._sender_thread = Thread(target=self._drain_sender,
                                         name="bus-sender", daemon=True)
            self._sender_thread.start()
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
        # Intent-topic compat guards are keyed by the TOPIC PAIR, not by the
        # handler: ovos-workshop binds a FRESH wrapper closure per spelling, so
        # a per-handler guard never sees both frames of a dual-bound intent and
        # the handler runs twice. See on().
        self._intent_pair_guards = {}    # frozenset({canonical, suffixed}) -> guard
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
        self.connected_event.clear()
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
        try:
            parsed_message = Message.deserialize(_maybe_decrypt(message))
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
        # RULE 2 dedup marker: read it, then POP it before any local dispatch.
        # The marker rode the wire (a different process's RULE 2 needs it to skip
        # the twin), but once here it must not survive onto descendant frames:
        # Message.forward()/reply() deep-copy the whole context, so a handler that
        # forwards this frame's context to emit an UNRELATED suffixed intent would
        # otherwise brand that frame a twin and silently suppress its modernization.
        is_intent_twin = parsed_message.context.pop(INTENT_COMPAT_TWIN_KEY, False)
        self.emitter.emit('message', message)
        self.emitter.emit(parsed_message.msg_type, parsed_message)
        # namespace migration bridge: also dispatch the counterpart topic(s) to
        # LOCAL listeners so a handler on either namespace receives the event
        # (consumers dedupe via the on() mirror-guard). This is a listener-delivery
        # convenience, not a second logical bus message: the counterpart is NOT put
        # back on the wire and does NOT re-fire the 'message' firehose, so one
        # logical emit yields exactly one captured message. The mirrored payload is
        # reshaped into the counterpart topic's shape (identity for payload-compatible
        # renames, a per-topic transform for shape-changing ones).
        for topic in self._translator.counterpart_topics(parsed_message.msg_type):
            translated = self._translator.translate_payload(
                from_topic=parsed_message.msg_type, to_topic=topic,
                data=parsed_message.data)
            self.emitter.emit(topic, parsed_message.forward(topic, translated))
        self._modernize_intent_topic(parsed_message, is_twin=is_intent_twin)

    # ------------------------------------------------------------------
    # legacy intent-topic bridge -- RULE 2 (receive)
    # ------------------------------------------------------------------

    def _modernize_intent_topic(self, message: Message, is_twin: bool = False):
        """Dispatch the canonical spelling of a suffixed intent frame.

        RULE 2. A suffixed frame WITHOUT the twin marker came from an emitter
        old enough to still put the authoring-file extension on the wire, so
        nothing canonical was sent alongside it and a spec-pure handler in this
        process would never hear the intent. A frame WITH the marker was
        already accompanied by its canonical twin, which this client dispatched
        on arrival, so modernizing it again would run the handler twice.

        ``is_twin`` carries the marker decision made in :meth:`on_message`, which
        pops the marker off the context before dispatch so it cannot leak onto
        descendant frames. The marker is therefore never read from ``context``
        here — only the popped value is trusted.

        The canonical copy stays local: it is never put back on the wire, so
        the broadcast server has nothing to echo.
        """
        if not self._translator.modernize:
            return
        if is_twin:
            return
        if not is_intent_topic(message.msg_type):
            return
        canonical = canonical_intent_topic(message.msg_type)
        if canonical == message.msg_type:
            return
        self.emitter.emit(canonical, _verbatim_copy(message, canonical))

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

        # a single logical emit puts exactly ONE message on the wire. The
        # namespace counterpart is bridged to listeners on the RECEIVE side
        # (see on_message) in every process, so both namespaces are delivered
        # without a second wire copy that the broadcast server would echo back
        # and double in the capture firehose.
        self._send(message)
        # ... with ONE exception: the legacy intent twin, which must reach a
        # process whose bus-client is too old to bridge anything. It goes after
        # the canonical dispatch, so a receiver that bridges both spellings
        # sees the canonical one first and drops the twin as the duplicate.
        self._send_legacy_intent_twin(message)

    def _send_legacy_intent_twin(self, message: Message):
        """Put the ``.intent``-suffixed twin of an intent dispatch on the wire.

        RULE 1, and the primary compat path. An outdated standalone skill
        container runs an old bus-client and an old workshop: it holds no
        bridge of its own and listens only on the suffixed topic, so nothing
        but a real wire frame reaches it. The twin carries the same payload and
        context plus :data:`INTENT_COMPAT_TWIN_KEY`, which tells a receiver
        that does understand the bridge that the canonical frame is already on
        its way.

        Every canonical intent dispatch is twinned. Which listeners exist in
        which process is unknowable from here, and a twin nobody listens to is
        a few ignored bytes. An already-suffixed dispatch is never twinned, so
        the mirror cannot cascade.
        """
        if not self._translator.emit_legacy:
            return
        if not is_intent_topic(message.msg_type):
            return
        topic = legacy_intent_topic(message.msg_type)
        if topic == message.msg_type:
            return
        twin = _verbatim_copy(message, topic)
        twin.context[INTENT_COMPAT_TWIN_KEY] = True
        self._send(twin)

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
        if self._sender_queue is not None:
            try:
                # bounded: a stuck socket applies backpressure to callers
                # instead of buffering frames without limit
                self._sender_queue.put((msg, message.msg_type), timeout=30)
            except _queue.Full:
                LOG.error(f"outbound bus queue full; dropping {message.msg_type}")
            return
        try:
            self.client.send(msg)
        except WebSocketConnectionClosedException:
            LOG.warning(f'Could not send {message.msg_type} message because connection '
                        'has been closed')
        except Exception as e:
            LOG.exception(f"failed to emit message {message.msg_type} with len {len(msg)}")

    _SENDER_STOP = object()

    def _drain_sender(self):
        """Single-writer loop for the optional async sender.

        Mirrors the synchronous error handling: connection loss and send
        failures are logged per frame and never kill the thread. Holds a
        LOCAL reference to the queue so a concurrent close() clearing the
        attribute can never crash the loop mid-drain, and acknowledges each
        frame via task_done() only after the socket write completed or
        failed -- which is what flush() waits on.
        """
        q = self._sender_queue
        while True:
            item = q.get()
            try:
                if item is self._SENDER_STOP:
                    return
                msg, msg_type = item
                try:
                    self.connected_event.wait(10)
                    self.client.send(msg)
                except WebSocketConnectionClosedException:
                    LOG.warning(f'Could not send {msg_type} message because '
                                'connection has been closed')
                except Exception:
                    LOG.exception(f"failed to emit message {msg_type} "
                                  f"with len {len(msg)}")
            finally:
                q.task_done()

    def flush(self, timeout: float = 5.0) -> bool:
        """Block until queued outbound frames completed their socket write.

        Waits on the queue's unfinished-task count (acknowledged by the
        sender AFTER ``client.send()`` returns or fails), not on queue
        emptiness -- a dequeued frame may still be inside the socket write.
        Returns True if everything was acknowledged within ``timeout``.
        No-op (True) for the default synchronous sender.
        """
        q = self._sender_queue
        if q is None:
            return True
        deadline = time.monotonic() + timeout
        with q.all_tasks_done:
            while q.unfinished_tasks:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                q.all_tasks_done.wait(remaining)
        return True

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
        guard = self._mirror_guard_for(event_name, func)
        if guard is not None:
            # Re-registering the SAME (event_name, func) pair used to be
            # harmless: pyee's EventEmitter keys its listener OrderedDict by
            # the handler object, so an equal bound method collapsed onto
            # the same slot instead of firing twice. Minting a fresh
            # ``wrapped`` closure on every call broke that -- pyee saw a new,
            # distinct object each time and fired both. Reuse the existing
            # wrapper for this exact (event_name, func) pair so
            # re-registering it re-adds the SAME closure pyee already knows,
            # restoring the original idempotent-registration behaviour.
            existing = self._dedup_registrations.get(func, [])
            for ev, wrapped in existing:
                if ev == event_name:
                    self.emitter.on(event_name, wrapped)
                    return

            def wrapped(message=None):
                if guard(message):
                    return
                return func(message)

            self.emitter.on(event_name, wrapped)
            self._dedup_registrations.setdefault(func, []).append((event_name, wrapped))
            return
        self.emitter.on(event_name, func)

    def _mirror_guard_for(self, event_name: str, func: Callable) -> Optional[Callable]:
        """The mirror guard a registration on ``event_name`` must wrap with.

        Two bridges deliver one logical event twice, and each needs a different
        guard SCOPE:

        - **namespace migration** (legacy ↔ ``ovos.*``): the guard is per
          HANDLER, shared across that handler's registrations, so its legacy
          ``on()`` and its ``ovos.*`` ``on()`` dedupe against each other.
        - **intent-topic compat** (canonical ↔ ``.intent``-suffixed): the guard
          is per TOPIC PAIR, shared by every registration on either spelling.

        The intent guard cannot be keyed by handler. ``ovos-workshop`` 9.3.2a1+
        binds the same skill method to both spellings through a FRESH wrapper
        closure per binding, so the two registrations are two distinct ``func``
        objects and a per-handler guard would hand each its own private state —
        the canonical frame runs one closure, the twin runs the other, and the
        skill handler fires twice for a single dispatch. Keying on the pair
        collapses them.

        Sharing one guard across different handlers on the SAME spelling is
        safe: the guard suppresses only a counterpart re-delivery, never a
        repeat on the same topic, so two independent handlers on the canonical
        topic each still run once per dispatch.

        Sharing it across different handlers on DIFFERENT spellings is not
        free. A process holding handler A on the canonical topic and an
        unrelated handler B on the suffixed one starves B: A's canonical frame
        arms the guard, and the twin B waits for is dropped as the mirror. This
        is accepted. A skill container runs ONE workshop version, which binds
        one spelling or both, so the mixed case is unreachable from a single
        version; and the alternative — a per-handler guard — reintroduces the
        double dispatch for the dual-binding case that is real and common.
        """
        counterpart = intent_topic_counterpart(event_name)
        if counterpart is not None:
            pair_key = frozenset({event_name, counterpart})
            guard = self._intent_pair_guards.get(pair_key)
            if guard is None:
                guard = self._translator.new_mirror_guard()
                self._intent_pair_guards[pair_key] = guard
            return guard
        if self._translator.is_migrated(event_name):
            guard = self._handler_guards.get(func)
            if guard is None:
                guard = self._translator.new_mirror_guard()
                self._handler_guards[func] = guard
            return guard
        return None

    def once(self, event_name: str, func: Callable[[Message], Any]):
        """Register callback with event emitter for a single call.

        Args:
            event_name (str): message type to map to the callback
            func (callable): callback function
        """
        # Route once() through the same guard-selection as on() (see
        # _mirror_guard_for): a handler that hears both spellings of a
        # mirrored dispatch via once() must still fire exactly once, not
        # twice.
        guard = self._mirror_guard_for(event_name, func)
        if guard is not None:
            existing = self._dedup_registrations.get(func, [])
            for ev, wrapped in existing:
                if ev == event_name:
                    # A once() re-registration of a still-pending
                    # (event_name, func) pair reuses the SAME wrapper
                    # pyee already knows -- same rationale as on()'s
                    # reuse branch.
                    self.emitter.once(event_name, wrapped)
                    return

            def wrapped(message=None):
                # pyee's once() already removes this closure from the
                # emitter the instant it fires (whether or not the guard
                # below goes on to suppress the call), so drop our own
                # bookkeeping for it here too -- otherwise a later on()/
                # once() for this (event_name, func) pair would try to
                # reuse a wrapper pyee no longer holds.
                self._forget_dedup_entry(func, event_name, wrapped)
                if guard(message):
                    return
                return func(message)

            self.emitter.once(event_name, wrapped)
            self._dedup_registrations.setdefault(func, []).append((event_name, wrapped))
            return
        self.emitter.once(event_name, func)

    def _forget_dedup_entry(self, func, event_name, wrapped):
        """Drop one wrapper's bookkeeping after pyee auto-removes it (once()).

        Mirrors the cleanup ``remove()`` does for an explicit teardown, so a
        fired once() registration leaves no stale entry for a later on()/
        once() call on the same (event_name, func) pair to (mis)reuse.
        """
        regs = self._dedup_registrations.get(func)
        if not regs:
            return
        try:
            regs.remove((event_name, wrapped))
        except ValueError:
            return
        if not regs:
            self._dedup_registrations.pop(func, None)
            self._handler_guards.pop(func, None)
        self._release_intent_pair_guard(event_name)

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
            self._release_intent_pair_guard(event_name)
        elif func in self.wrapped_funcs:
            self._remove_wrapped(event_name, func)
        else:
            self._remove_normal(event_name, func)

    def _release_intent_pair_guard(self, event_name: str):
        """Drop the pair guard once nothing is registered on either spelling.

        The guard is keyed by topic pair rather than by handler, so no single
        handler's teardown owns it. It is released when the LAST registration
        on either spelling goes away — otherwise a client that churns through
        intent subscriptions accumulates one dead guard per intent it ever saw.
        """
        counterpart = intent_topic_counterpart(event_name)
        if counterpart is None:
            return
        pair_key = frozenset({event_name, counterpart})
        for regs in self._dedup_registrations.values():
            if any(ev in pair_key for ev, _ in regs):
                return
        self._intent_pair_guards.pop(pair_key, None)

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
        sender = self._sender_thread
        if sender is not None:
            self.flush(timeout=5.0)
            try:
                # never block close() behind a full queue: the sender holds
                # its own reference and drains regardless; a lost sentinel
                # only means the daemon thread parks on get() until process
                # exit instead of returning early
                self._sender_queue.put_nowait(self._SENDER_STOP)
            except _queue.Full:
                LOG.warning("outbound bus queue still full at close(); "
                            "sender thread will not be stopped explicitly")
            sender.join(timeout=2.0)
            if sender.is_alive():
                LOG.warning("bus sender still draining at close(); "
                            "leaving it to finish in the background")
            # the drain loop keeps a local queue reference, so clearing the
            # attributes is safe even if the thread is still finishing
            self._sender_thread = None
            self._sender_queue = None
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

        try:
            parsed_message = GUIMessage.deserialize(_maybe_decrypt(message))
        except MalformedMessage as e:
            # Discard a malformed frame instead of letting it tear down the
            # GUI websocket via on_error (see the core on_message handler).
            LOG.warning("discarding malformed GUI message: %s", e)
            return
        self.emitter.emit(parsed_message.msg_type, parsed_message)
