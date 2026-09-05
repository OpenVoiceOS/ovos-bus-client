
import time
from copy import deepcopy

from ovos_utils import json_dumps

from os import getpid
from threading import Event, Thread
from typing import Union, Callable, Any, List, Optional
from uuid import uuid4

from ovos_utils.log import LOG, log_deprecation
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
from ovos_bus_client.session import (SessionManager, Session, MalformedSession,
                                     DEFAULT_SESSION_ID, LEGACY_SESSION_SYNC,
                                     resolve_session_id, session_carrier,
                                     _NEXT_MAJOR_VERSION)
from ovos_spec_tools.messages import NamespaceTranslator

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
from ovos_spec_tools.messages import MIGRATION_MAP

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

# --- legacy namespace-migration compat (non-normative migration tooling) ---
#
# ``NamespaceTranslator.counterpart_topics()`` already tells a RECEIVING
# client to also dispatch the counterpart of an arriving frame to LOCAL
# listeners (see on_message below) -- that is enough for two processes that
# both run a modern bus-client, because either one delivers both spellings
# from a single wire frame. It is NOT enough for an old pre-spec-tools
# client: it has no translator, so it only ever hears a msg_type it is
# literally subscribed to, and a canonical-only emit never reaches its
# legacy-spelled subscription.
#
# This mirrors RULE 1 of the intent-topic bridge above, generalised from
# intent topics to every :data:`MIGRATION_MAP` topic:
#
#   RULE 1 (send)    -- every canonical (``ovos.*``) emit of a migrated topic
#                       also goes out as a REAL second wire frame on its
#                       legacy spelling, marked as a twin, payload reshaped
#                       for the legacy side. An old satellite listening only
#                       on the legacy topic runs a bus-client too old to
#                       bridge anything, so only a real wire frame reaches it.
#   RULE 2 (receive) -- a legacy emit is already bridged to local canonical
#                       listeners by the existing receive-side
#                       ``counterpart_topics()`` loop; that direction never
#                       needed a second wire frame and still doesn't, so it is
#                       untouched here.
#
# The marker is again the whole deduplication, but the namespace bridge has
# an extra receive-side hazard the intent bridge does not: unlike a suffixed
# intent topic, a legacy namespace topic (e.g. ``speak``) routinely DOES have
# local listeners in a modern process too. So a marked twin is not just
# skipped for re-modernization -- it must skip its OWN direct dispatch and
# the receive-side counterpart loop entirely, because both spellings were
# already delivered locally when the canonical frame that came before it was
# received. See on_message.
NAMESPACE_COMPAT_TWIN_KEY = "_namespace_compat_twin"

#: Escape-hatch flag (env var ``OVOS_BUS_WIRE_LEGACY_TWINS`` / config key
#: ``websocket.wire_legacy_twins``) gating :meth:`MessageBusClient.
#: _send_legacy_namespace_twin`. DEFAULT TRUE -- symmetric with
#: ``OVOS_BUS_EMIT_LEGACY``.
#:
#: #286 made every canonical (``ovos.*``) emit of a :data:`MIGRATION_MAP`
#: topic also put a real second wire frame on the legacy spelling. This is
#: the compat path for the actual supported population: the latest STABLE
#: ``ovos-bus-client`` release (1.5.0, pre-spec-tools) and anything older --
#: those clients have no :class:`NamespaceTranslator` at all, so a
#: canonical-only emit never reaches their legacy-spelled subscription
#: without a real wire twin.
#:
#: A receiver in the 2.2.0a1..2.8.2a1 ALPHA window is not a supported
#: configuration (only the latest prerelease is supported, per project
#: policy): it already bridges both spellings locally from the canonical
#: frame alone (RULE 2 above, via ``counterpart_topics()`` in
#: ``on_message``), so it double-delivers every migrated topic while
#: sharing a bus with a 2.8.3a1+ sender. That is a transient hazard of
#: running an outdated alpha, resolved by updating the receiver to
#: 2.8.3a1+ (which dedups the twin via :data:`NAMESPACE_COMPAT_TWIN_KEY`)
#: -- it is documented here, not defended against.
#:
#: Set this flag to false only on a bus where the operator knows no
#: pre-spec-tools (stable <2.x) wire listeners are present, to get the
#: bandwidth of the second frame back. A relay/hub that knows its own
#: satellite population (e.g. a HiveMind hub bridging to known-old
#: satellites) is the recommended place to scope this translation instead
#: of flipping it bus-wide.

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


def _compute_legacy_intent_twin(message: Message,
                                translator: NamespaceTranslator) -> Optional[Message]:
    """Compute the ``.intent``-suffixed twin of an intent dispatch, or
    ``None`` if this message doesn't need one.

    Pure helper shared by :meth:`MessageBusClient._send_legacy_intent_twin`
    and its async-client equivalent so both wire clients twin identically.
    """
    if not translator.emit_legacy:
        return None
    if not is_intent_topic(message.msg_type):
        return None
    topic = legacy_intent_topic(message.msg_type)
    if topic == message.msg_type:
        return None
    twin = _verbatim_copy(message, topic)
    twin.context[INTENT_COMPAT_TWIN_KEY] = True
    return twin


def _compute_legacy_namespace_twin(message: Message,
                                   translator: NamespaceTranslator,
                                   wire_legacy_twins: bool) -> Optional[Message]:
    """Compute the legacy-spelled twin of a canonical namespace emit, or
    ``None`` if this message doesn't need one.

    Pure helper shared by :meth:`MessageBusClient._send_legacy_namespace_twin`
    and its async-client equivalent so both wire clients twin identically.
    """
    if not translator.emit_legacy:
        return None
    if not wire_legacy_twins:
        return None
    if message.msg_type in MIGRATION_MAP:
        return None
    if is_intent_topic(message.msg_type):
        return None
    counterparts = translator.counterpart_topics(message.msg_type)
    if not counterparts:
        return None
    topic = counterparts[0]
    if topic == message.msg_type:
        return None
    payload = translator.translate_payload(
        from_topic=message.msg_type, to_topic=topic, data=message.data)
    twin = _verbatim_copy(message, topic)
    twin.data = payload
    twin.context[NAMESPACE_COMPAT_TWIN_KEY] = True
    return twin


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
        # namespace translation on emit (orthogonal, both ON by default during
        # the migration window so every migrated event travels on BOTH the
        # legacy and the ovos.* topic — any repo can flip its emit OR its listen
        # to ovos.* in any order, with no coordination):
        #  emit_legacy: emitting an ovos.* spec topic also emits the legacy one.
        #  modernize  : emitting a legacy topic also emits the ovos.* spec one.
        self._translator = NamespaceTranslator(
            modernize=_bus_flag("OVOS_BUS_MODERNIZE", "modernize", default=True),
            emit_legacy=_bus_flag("OVOS_BUS_EMIT_LEGACY", "emit_legacy", default=True))
        # escape hatch, default ON -- see the docstring above NAMESPACE_COMPAT_TWIN_KEY.
        self._wire_legacy_twins = _bus_flag(
            "OVOS_BUS_WIRE_LEGACY_TWINS", "wire_legacy_twins", default=True)
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
            session = SessionManager.get_default_session()
        # OVOS-SESSION-2 §2.5/§6.4: this client IS the client that owns a named
        # session, so the object it was built with is the authority on it --
        # the orchestrator's registry holds no named session to look it up in.
        self.session = session
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
        # DEPRECATED: ovos.session.sync's bare-request/echo round trip is a
        # pre-spec surface OVOS-SESSION-2 §2.7/§7 retires. It is still the
        # only way a pre-spec-tools core (e.g. stable 1.3.1) ever answers
        # with its default session, so a freshly-connected client kept one
        # cycle behind: without this, it never learns a long-running old
        # core's default session (lang, etc) and is stuck on its own
        # config-derived default.
        log_deprecation("the connect-time ovos.session.sync request is a "
                        "pre-spec surface retired by OVOS-SESSION-2 §2.7 "
                        "and will stop being sent",
                        _NEXT_MAJOR_VERSION)
        self.emit(Message(LEGACY_SESSION_SYNC))  # request default session update

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
            self._take_inbound_session(parsed_message)
        except MalformedSession as e:
            # A non-object session carrier is a per-message producer fault, not a
            # transport fault (SESSION-1 §2.5): drop this one message and keep the
            # connection. Letting it propagate would reach on_error and reconnect,
            # so a single bad producer could hold the client in a reconnect loop.
            LOG.warning("discarding bus message with malformed session: %s", e)
            return
        # RULE 2 dedup marker: read it, then POP it before any local dispatch.
        # The marker rode the wire (a different process's RULE 2 needs it to skip
        # the twin), but once here it must not survive onto descendant frames:
        # Message.forward()/reply() deep-copy the whole context, so a handler that
        # forwards this frame's context to emit an UNRELATED suffixed intent would
        # otherwise brand that frame a twin and silently suppress its modernization.
        is_intent_twin = parsed_message.context.pop(INTENT_COMPAT_TWIN_KEY, False)
        # RULE 1 dedup marker for the namespace bridge: pop it the same way,
        # for the same reason (must not survive onto descendant frames via
        # forward()/reply()).
        is_namespace_twin = parsed_message.context.pop(NAMESPACE_COMPAT_TWIN_KEY, False)
        # The 'message' firehose is the raw wire-capture stream a modern
        # receiver's wildcard/logging listeners see. A marked NAMESPACE twin
        # is the SAME logical dispatch as the canonical frame that preceded
        # it on the wire -- firing the firehose for it too would double-count
        # every migrated topic for any modern listener bound to it, breaking
        # the one-frame-per-logical-emit invariant conformance captures
        # (ovoscope/busmon) rely on. An old client has no notion of "twin"
        # and legitimately sees both raw frames off the wire -- that
        # asymmetry is inherent to wire visibility, and is already true
        # (and accepted) for the pre-existing intent-topic twin, which this
        # gate deliberately leaves untouched to stay within this fix's scope.
        try:
            if not is_namespace_twin:
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
            # else: a marked namespace twin is a REAL second wire frame that only
            # exists to reach an old pre-spec-tools client with no translator of
            # its own. A modern receiver already got both spellings delivered
            # locally from the canonical frame this twin follows (the loop
            # above), so re-running direct dispatch and/or the counterpart loop
            # for the twin itself would deliver both spellings a SECOND time.
            self._modernize_intent_topic(parsed_message, is_twin=is_intent_twin)
        except RuntimeError as e:
            LOG.debug(f'Emitter refused message dispatch during shutdown: {e}')

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

    def _own_session(self) -> Session:
        """The session this client stamps on a message that carries none.

        The default session is the orchestrator's store, so it is read live and
        a reset is picked up. A named session is client-owned (OVOS-SESSION-2
        §2.5) and this client is the client that owns it (§6.4), so the object
        it was constructed with is the authority -- no registry holds a named
        session to look one up in, and fabricating an empty one from the id
        alone would put a session on the wire the client never had.
        """
        if self.session_id == DEFAULT_SESSION_ID:
            return SessionManager.get_default_session()
        return self.session or Session(self.session_id)

    def _take_inbound_session(self, message: Message):
        """Take an arriving message's session into whatever state holds it.

        OVOS-SESSION-2 §5.1's arrival merge is an orchestrator-intake fold: it
        happens exactly once, at the process that owns the default-session
        store, when an utterance is first taken in. This client is a bus
        *consumer* -- a listener, a satellite, a skill container, or the
        orchestrator itself -- and every one of those observes far more
        default-session messages than the single intake per utterance the
        spec merges (replies, handled-acks, forwarded frames all carry a
        session too). Folding on each of those would merge stale field values
        back into the live store on every observed message, not just at
        intake, and would silently overwrite whatever the orchestrator's own
        intake fold just wrote (see OVOS-SESSION-2 §2.6: mutation only at
        lifecycle boundaries, not on every observation). The orchestrator
        process folds for itself, explicitly, at its own intake point; this
        client only needs to be able to *resolve* a session for handlers,
        which ``SessionManager.get`` already does purely off the carrier
        without touching the store.

        A carrier that names no usable id IS the default session (SESSION-1
        §3.1) and is left exactly alone: it dispatches without touching the
        store, whether or not it would otherwise construct into a well-formed
        ``Session`` (an empty/falsy id is unusable but still names the
        default per §3.1, so it must not be rejected as malformed here). A
        named session is client-owned and the orchestrator holds nothing for
        it (§2.2), so it still goes through ``update``, which is a no-op
        wherever the registry honours §2.2 and the utterance-scoped
        registration on older releases.

        @raises MalformedSession: the message carries a non-object session
        """
        carrier = session_carrier(message)
        if resolve_session_id(carrier) == DEFAULT_SESSION_ID:
            return
        sess = Session.from_message(message)
        if sess.session_id != DEFAULT_SESSION_ID:
            SessionManager.update(sess)

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
            message.context["session"] = self._own_session().serialize()

        # a single logical emit puts exactly ONE message on the wire. The
        # namespace counterpart is bridged to listeners on the RECEIVE side
        # (see on_message) in every process, so both namespaces are delivered
        # without a second wire copy that the broadcast server would echo back
        # and double in the capture firehose.
        self._send(message)
        # ... with TWO exceptions: the legacy intent twin and the legacy
        # namespace twin, which must reach a process whose bus-client is too
        # old to bridge anything. Both go after the canonical dispatch, so a
        # receiver that bridges both spellings sees the canonical one first
        # and drops the twin as the duplicate.
        self._send_legacy_intent_twin(message)
        self._send_legacy_namespace_twin(message)

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
        twin = _compute_legacy_intent_twin(message, self._translator)
        if twin is not None:
            self._send(twin)

    def _send_legacy_namespace_twin(self, message: Message):
        """Put the legacy-spelled twin of a canonical namespace emit on the wire.

        RULE 1 of the namespace bridge (see the module comment above
        :data:`NAMESPACE_COMPAT_TWIN_KEY`). Only the forward direction --
        canonical (``ovos.*``) emit gets a real legacy twin -- is handled
        here; a legacy emit's canonical counterpart is already delivered to
        local listeners on the RECEIVE side (see on_message), exactly as the
        intent bridge's RULE 2 handles its own reverse direction, so it is
        not twinned onto the wire a second time.

        An already-legacy emit, or a topic outside :data:`MIGRATION_MAP` /
        the computed ``<skill_id>:stop`` pattern, produces no counterpart and
        is left untouched -- including intent topics, which are twinned by
        :meth:`_send_legacy_intent_twin` instead.
        """
        twin = _compute_legacy_namespace_twin(
            message, self._translator, self._wire_legacy_twins)
        if twin is not None:
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
