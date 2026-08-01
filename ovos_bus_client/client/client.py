
import json
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
# Both halves of the version skew are real, so the bridge runs in both
# directions (old/new skill x old/new core):
#
# * a NEW core dispatches the canonical topic, and an OLD skill container -
#   its own bus-client too old to hold this bridge - listens on the suffixed
#   topic. Only the WIRE can reach it, so the twin is sent over the wire from
#   the emitting process. This is the primary path.
# * an OLD core dispatches the suffixed topic, and a NEW skill listens on the
#   canonical one. The receiving client modernizes on arrival.
# * both processes new: the canonical dispatch and its wire twin both arrive.
#   Delivery is deduplicated per dispatch so each local handler runs once.
#
# ``ovos_spec_tools.intent_topics`` is the whole compat surface for that gap.
# It is newer than the spec-tools floor this client declares, so the import is
# guarded: against an older spec-tools the bridge behaves exactly as before and
# no intent twin is ever sent or mirrored.
try:
    from ovos_spec_tools.intent_topics import (IntentAliasRegistry,
                                               canonical_intent_topic,
                                               is_intent_topic,
                                               legacy_reemit_targets)
    _HAS_INTENT_TOPICS = True
except ImportError:  # spec-tools without OVOS-INTENT-4 compat helpers
    IntentAliasRegistry = None
    canonical_intent_topic = None
    is_intent_topic = None
    legacy_reemit_targets = None
    _HAS_INTENT_TOPICS = False

#: Context flag stamped on a twin intent dispatch, on the wire and locally. A
#: message carrying it is a twin of a dispatch that also exists in its
#: canonical spelling, so the receiver may drop it as a duplicate once the
#: handlers it targets have already run.
INTENT_REEMIT_CONTEXT_KEY = "__legacy_intent_reemit__"

#: Intent registration topics an old skill container puts on the wire, mapped
#: to the payload field naming the intent. The old adapt / padatious topics
#: carry the fully qualified ``<skill_id>:<name>`` the skill will also listen
#: on - the suffix leaks here first, which is what makes the wire a usable
#: alias source. The OVOS-INTENT-4 family names the parts separately.
_INTENT_REGISTRATION_TOPICS = {
    "padatious:register_intent": "name",
    "register_intent": "name",
    "ovos.intent.register.keyword": "intent_name",
    "ovos.intent.register.template": "intent_name",
}

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
        # legacy intent-topic bridge, gated by the SAME emit_legacy flag above.
        # The alias table is owned by the client and filled from two sources:
        #  * the intent REGISTRATIONS this client sees on the wire - an old
        #    skill container announces its intents under the suffixed name, so
        #    the emitting process learns which twins a real listener wants;
        #  * this client's own on() / once() calls, which cover an old-style
        #    listener living in this very process.
        # Nothing else feeds it, so the bus invents no topic nobody asked for.
        # There is deliberately no global registry - two clients in one process
        # have separate listener sets and must not re-emit on each other's
        # behalf.
        self._intent_aliases = IntentAliasRegistry() if _HAS_INTENT_TOPICS else None
        # canonical topics whose alias came from the wire. remove() must not
        # drop those: no local listener owns them.
        self._wire_intent_aliases = set()
        # per-dispatch delivery record, fingerprint -> (topics, timestamp).
        # Bounds the window in which the canonical dispatch and its twin count
        # as one event, so each local handler runs once however many spellings
        # of the dispatch reach this client.
        self._intent_delivered = {}
        # blanket mode twins EVERY intent dispatch, registered alias or not,
        # for pure-bus listeners that subscribe without registering. It doubles
        # intent traffic on the wire, so it is off unless asked for.
        self._intent_reemit_blanket = _bus_flag("OVOS_BUS_INTENT_REEMIT_BLANKET",
                                                "intent_reemit_blanket",
                                                default=False)
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
        self._observe_intent_registration(parsed_message)
        if self._intent_bridge_active() and is_intent_topic(parsed_message.msg_type):
            delivered = self._intent_delivery_record(parsed_message)
            if (parsed_message.context.get(INTENT_REEMIT_CONTEXT_KEY)
                    and self._twin_is_redundant(parsed_message, delivered)):
                return
            delivered.add(parsed_message.msg_type)
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
        self._bridge_intent_topics(parsed_message)

    # ------------------------------------------------------------------
    # legacy intent-topic bridge
    # ------------------------------------------------------------------

    def _intent_bridge_active(self) -> bool:
        """Whether the intent-topic compat runs at all in this client."""
        return self._intent_aliases is not None and self._translator.emit_legacy

    def _intent_fingerprint(self, message: Message) -> str:
        """Identify the DISPATCH a message carries, whichever spelling it uses.

        The canonical topic plus the payload and context, minus the twin marker
        - so a dispatch and its twin fingerprint the same and the delivery
        record can collapse them.
        """
        context = {key: value for key, value in (message.context or {}).items()
                   if key != INTENT_REEMIT_CONTEXT_KEY}
        return json.dumps([canonical_intent_topic(message.msg_type),
                           message.data, context], sort_keys=True, default=str)

    def _intent_delivery_record(self, message: Message) -> set:
        """The set of topics this dispatch was already delivered under.

        Entries older than the translator's mirror window are dropped first, so
        two deliberate dispatches of one intent spaced apart are two events -
        only a near-simultaneous canonical/twin pair collapses.
        """
        now = time.monotonic()
        window = self._translator.window
        for key in [k for k, (_, ts) in self._intent_delivered.items()
                    if now - ts >= window]:
            self._intent_delivered.pop(key, None)
        fingerprint = self._intent_fingerprint(message)
        topics = self._intent_delivered.get(fingerprint, (set(), now))[0]
        self._intent_delivered[fingerprint] = (topics, now)
        return topics

    def _twin_is_redundant(self, message: Message, delivered: set) -> bool:
        """Whether an incoming twin adds nothing this client has not done.

        A new core puts both spellings of an aliased dispatch on the wire, for
        the sake of processes that cannot bridge. A process that CAN bridge
        must not run a handler twice, so the twin is dropped when:

        * this client already delivered the same spelling itself - the local
          mirror got there first; or
        * the canonical spelling was delivered and nothing here listens on the
          suffixed one - the twin would only duplicate the ``message``
          firehose.

        Anything else is kept: an unbridged listener on the suffixed topic is
        exactly who the twin was sent for.
        """
        if message.msg_type in delivered:
            return True
        return (canonical_intent_topic(message.msg_type) in delivered
                and not self.emitter.listeners(message.msg_type))

    def _bridge_intent_topics(self, message: Message):
        """Deliver an intent dispatch to LOCAL listeners under both spellings.

        Two directions, each the counterpart of one wire producer:

        * a suffixed dispatch (old core) is modernized onto its canonical
          topic, so a spec-pure skill in this process is reached;
        * a canonical dispatch is mirrored onto the suffixed twin of an intent
          this client recorded an alias for, covering an old-style listener
          living in this very process. The WIRE twin sent by :meth:`emit` is
          what reaches an old listener in another process; this half is the
          in-process secondary.

        Nothing here goes back on the wire, so the broadcast server never
        echoes a bridged copy. A topic already in the delivery record is
        skipped, so the pair produced by a new core on both spellings does not
        run a handler twice.
        """
        if not self._intent_bridge_active() or not is_intent_topic(message.msg_type):
            return
        canonical = canonical_intent_topic(message.msg_type)
        if canonical != message.msg_type:
            targets = [canonical]
        else:
            targets = legacy_reemit_targets(message.msg_type,
                                            registry=self._intent_aliases,
                                            blanket=self._intent_reemit_blanket)
        delivered = self._intent_delivery_record(message)
        for topic in targets:
            if topic in delivered:
                continue
            twin = message.forward(topic, message.data)
            twin.context[INTENT_REEMIT_CONTEXT_KEY] = True
            delivered.add(topic)
            self.emitter.emit(topic, twin)

    def _observe_intent_registration(self, message: Message):
        """Learn a legacy alias from an intent registration seen on the wire.

        An old skill container registers its intents under the name it will
        also listen on, so a registration carrying the ``.intent`` suffix is
        proof that a real consumer wants the suffixed twin. This is what lets
        the EMITTING process - which has no such listener of its own - know
        which dispatches to twin onto the wire.

        Canonical registrations teach nothing and are ignored: the twin is only
        ever sent for an intent somebody announced by its suffixed name.
        """
        if self._intent_aliases is None:
            return
        field = _INTENT_REGISTRATION_TOPICS.get(message.msg_type)
        if field is None:
            return
        name = (message.data or {}).get(field)
        if not isinstance(name, str) or not name:
            return
        if ":" not in name:
            # OVOS-INTENT-4 names the parts separately; the old topics carry
            # the qualified name already.
            skill_id = (message.data.get("skill_id")
                        or (message.context or {}).get("skill_id"))
            if not isinstance(skill_id, str) or not skill_id:
                return
            name = f"{skill_id}:{name}"
        if not is_intent_topic(name) or canonical_intent_topic(name) == name:
            return
        self._intent_aliases.register(name)
        self._wire_intent_aliases.add(canonical_intent_topic(name))

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

        The PRIMARY compat path. An outdated standalone skill container runs an
        old bus-client and an old workshop: it holds no bridge of its own and
        listens only on the suffixed topic, so nothing but a real wire frame
        reaches it. The twin carries the same payload and context plus
        :data:`INTENT_REEMIT_CONTEXT_KEY`, which marks it as the duplicate for
        any receiver new enough to have bridged the canonical dispatch itself.

        Only intents with a recorded alias are twinned (or all of them under
        blanket mode), so wire traffic doubles for those intents alone. An
        already-suffixed dispatch is never twinned, so the mirror cannot
        cascade.
        """
        if not self._intent_bridge_active():
            return
        if message.context.get(INTENT_REEMIT_CONTEXT_KEY):
            return
        for topic in legacy_reemit_targets(message.msg_type,
                                           registry=self._intent_aliases,
                                           blanket=self._intent_reemit_blanket):
            twin = message.forward(topic, message.data)
            twin.context[INTENT_REEMIT_CONTEXT_KEY] = True
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
        self._record_intent_alias(event_name)
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
        self._record_intent_alias(event_name)
        self.emitter.once(event_name, func)

    def _record_intent_alias(self, event_name: str):
        """Note that a handler subscribed to ``event_name``.

        Only per-intent dispatch topics are recorded; the registry ignores
        everything else. A subscription written with the legacy ``.intent``
        suffix is what marks the canonical intent as needing the mirror.
        """
        if self._intent_aliases is not None:
            self._intent_aliases.register(event_name)

    def _forget_intent_alias(self, event_name: str):
        """Drop the alias of ``event_name`` once nothing listens on it."""
        if self._intent_aliases is None:
            return
        alias = self._intent_aliases.legacy_alias(event_name)
        if alias is None:
            return
        if canonical_intent_topic(event_name) in self._wire_intent_aliases:
            # learned from a registration on the wire: the listener that wants
            # it lives in another process, so a local remove() says nothing.
            return
        if not self.emitter.listeners(alias):
            self._intent_aliases.deregister(event_name)

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
        self._forget_intent_alias(event_name)

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
        self._forget_intent_alias(event_name)

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

        try:
            parsed_message = GUIMessage.deserialize(_maybe_decrypt(message))
        except MalformedMessage as e:
            # Discard a malformed frame instead of letting it tear down the
            # GUI websocket via on_error (see the core on_message handler).
            LOG.warning("discarding malformed GUI message: %s", e)
            return
        self.emitter.emit(parsed_message.msg_type, parsed_message)
