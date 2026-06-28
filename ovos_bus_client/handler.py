"""Shared framework -> orchestrator handler-completion reporting.

This module generalizes the *internal* done-signal that ovos-workshop has
historically emitted around every skill handler invocation
(``OVOSSkill._on_event_start`` / ``_on_event_end`` / ``_on_event_error``) so
that **any** in-process dispatcher can report handler completion uniformly.

Why this exists
---------------
ovos-core (the orchestrator) is the *sole* emitter of the authoritative
PIPELINE-1 §8 handler-lifecycle trio
(``ovos.intent.handler.{start,complete,error}``). It does **not** time handler
execution directly; instead it observes a private *framework done-signal* — the
legacy ``mycroft.skill.handler.{start,complete,error}`` topics — emitted around
each handler invocation, and translates the observed completion into the spec
trio (see ``ovos_core.intent_services.dispatcher``).

Until now only ``OVOSSkill`` subclasses emitted that done-signal. Pipeline
plugins that run handlers in-process **without** subclassing ``OVOSSkill``
(converse, persona, fallback, common-query, stop — the "polymorphic
dispatches" of PIPELINE-1 §7) emitted nothing, so the orchestrator could not
observe their completion and fell back to a multi-minute timeout.

Because ``ovos-bus-client`` is the common dependency of both ovos-workshop and
every pipeline plugin, the shared reporting helper lives here. This is
**implementation, NOT specification**: the topics emitted by this helper
(``mycroft.skill.handler.*`` by default) are a private framework→orchestrator
synchronization signal and are deliberately *excluded* from the
ovos-spec-tools migration map. The orchestrator remains the only component that
emits the spec ``ovos.intent.handler.*`` trio.

Contract consumed by the orchestrator
-------------------------------------
``ovos_core.intent_services.dispatcher`` correlates a done-signal to an
in-flight dispatch using:

* ``message.context["skill_id"]`` — the dispatching skill/plugin id;
* the session id carried in ``message.context`` (preserved via ``forward``);

and, on the error path, reads the exception text from
``message.data["exception"]`` (falling back to ``message.data["error"]``).

This helper emits in exactly that shape, so the orchestrator needs no change.

Example
-------
>>> from ovos_bus_client.handler import HandlerLifecycle
>>> with HandlerLifecycle(bus, message, skill_id="ovos-persona",
...                        handler_name="ask_persona"):
...     handler(message)  # start emitted on enter, complete on clean exit,
...                        # error emitted (and re-raised) on exception

Each adopter may preserve its own existing payload via ``data=...``. For
example, ovos-workshop keeps its historical ``{"name": ...}`` shape:

>>> with HandlerLifecycle(self.bus, message, skill_id=self.skill_id,
...                        data={"name": get_handler_name(handler)}):
...     handler(message)
"""
from functools import wraps
from typing import Optional, Any, Callable

from ovos_bus_client.message import Message

#: Default base topic for the framework done-signal. Matches ovos-workshop's
#: ``OVOSSkill.add_event(..., handler_info='mycroft.skill.handler')`` exactly,
#: so emissions from this helper are indistinguishable to the orchestrator.
DEFAULT_HANDLER_INFO = "mycroft.skill.handler"


class HandlerLifecycle:
    """Context manager that reports a handler's lifecycle to the orchestrator.

    Emits the framework done-signal around a handler invocation:

    * on ``__enter__``: ``<handler_info>.start``
    * on clean ``__exit__``: ``<handler_info>.complete``
    * on ``__exit__`` with an exception: ``<handler_info>.error`` (then the
      exception is **re-raised** — this helper never suppresses it).

    Every emission is derived from the triggering ``message`` via
    :meth:`Message.forward`, so the originating ``context`` (including the
    session) is preserved, and ``context["skill_id"]`` is stamped so the
    orchestrator can correlate the signal to its in-flight dispatch.

    This helper deliberately does **not** speak an error dialog or otherwise
    influence user-facing control flow — callers keep their own error handling
    (including the spoken-error UX). It only reports completion.

    Args:
        bus: anything exposing ``.emit(Message)`` (a real ``MessageBusClient``
            or ``ovos_utils.fakebus.FakeBus``).
        message: the triggering ``Message``; its context (and session) is
            preserved through ``forward`` on every emission.
        skill_id: the dispatching skill/plugin id, stamped into
            ``context["skill_id"]`` so the orchestrator can correlate.
        handler_name: human/diagnostic name of the handler. When ``data`` is
            not supplied, this becomes the default payload (``{"handler":
            handler_name}``). Ignored when ``data`` is given. Optional.
        data: base payload for every emission, used **verbatim** (a fresh copy
            is forwarded each time, so neither the caller's dict nor this base
            is ever mutated; on the error path ``{"exception": ...}`` is merged
            into that copy). This lets each adopter preserve its existing
            payload — e.g. ovos-workshop passes
            ``data={"name": get_handler_name(handler)}`` to keep its historical
            ``{"name": ...}`` shape. When ``None`` (default), the payload falls
            back to ``{"handler": handler_name}`` (or ``{}`` when no
            ``handler_name`` is given). Core reads neither field — it correlates
            on ``context["skill_id"]`` and reads ``data["exception"]`` on error —
            so this is purely about preserving each adopter's payload.
        handler_info: base topic for the done-signal. Defaults to
            ``mycroft.skill.handler`` to match ovos-workshop. An empty/false
            value disables emission entirely (matching workshop semantics).
    """

    def __init__(self, bus: Any, message: Message,
                 skill_id: Optional[str] = None,
                 handler_name: Optional[str] = None,
                 data: Optional[dict] = None,
                 handler_info: str = DEFAULT_HANDLER_INFO):
        self.bus = bus
        self.message = message
        self.skill_id = skill_id
        self.handler_name = handler_name
        self.data = data
        self.handler_info = handler_info

    def _payload(self, extra: Optional[dict] = None) -> dict:
        # build a fresh dict every call so neither the caller-supplied base
        # (self.data) nor any forwarded payload is mutated across emissions
        if self.data is not None:
            data = dict(self.data)
        elif self.handler_name is not None:
            data = {"handler": self.handler_name}
        else:
            data = {}
        if extra:
            data.update(extra)
        return data

    def _emit(self, suffix: str, data: dict) -> None:
        """Forward a done-signal message, preserving context + session.

        ``forward`` deep-copies ``self.message.context``; we stamp ``skill_id``
        onto the *forwarded* message so the orchestrator's correlation key is
        present without mutating the caller's original message context.
        """
        if not self.handler_info:
            return
        msg = self.message.forward(self.handler_info + suffix, data)
        if self.skill_id is not None:
            msg.context["skill_id"] = self.skill_id
        self.bus.emit(msg)

    def start(self) -> None:
        """Emit ``<handler_info>.start``."""
        self._emit(".start", self._payload())

    def complete(self) -> None:
        """Emit ``<handler_info>.complete``."""
        self._emit(".complete", self._payload())

    def error(self, exception: BaseException) -> None:
        """Emit ``<handler_info>.error`` carrying the exception text.

        The exception text rides in ``data["exception"]`` (the field
        ``ovos_core.intent_services.dispatcher._on_skill_error`` reads first).
        """
        self._emit(".error", self._payload({"exception": repr(exception)}))

    def __enter__(self) -> "HandlerLifecycle":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_val is not None:
            self.error(exc_val)
        else:
            self.complete()
        # never suppress — callers own their error handling / spoken-error UX
        return False


def report_handler_lifecycle(bus: Any,
                             skill_id: Optional[str] = None,
                             handler_name: Optional[str] = None,
                             data: Optional[dict] = None,
                             handler_info: str = DEFAULT_HANDLER_INFO
                             ) -> Callable:
    """Decorator wrapping a ``handler(message)`` with :class:`HandlerLifecycle`.

    The wrapped callable must accept the triggering ``Message`` as its first
    positional argument (the standard OVOS handler signature). The done-signal
    is reported around each invocation; exceptions are re-raised.

    Example::

        @report_handler_lifecycle(bus, skill_id="ovos-stop",
                                  handler_name="handle_stop")
        def handle_stop(message):
            ...

    Args:
        bus: object exposing ``.emit(Message)``.
        skill_id: dispatching skill/plugin id (``context["skill_id"]``).
        handler_name: diagnostic handler name (``data["handler"]`` when ``data``
            is not given); defaults to the wrapped function's ``__name__``.
        data: base payload for every emission, used verbatim (see
            :class:`HandlerLifecycle`). When ``None`` (default) the payload
            falls back to ``{"handler": handler_name}``.
        handler_info: base topic for the done-signal (default
            ``mycroft.skill.handler``).
    """
    def decorator(func: Callable) -> Callable:
        name = handler_name if handler_name is not None else getattr(
            func, "__name__", None)

        @wraps(func)
        def wrapper(message: Message, *args, **kwargs):
            with HandlerLifecycle(bus, message, skill_id=skill_id,
                                  handler_name=name, data=data,
                                  handler_info=handler_info):
                return func(message, *args, **kwargs)

        return wrapper

    return decorator
