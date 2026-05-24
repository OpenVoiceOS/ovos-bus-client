# Copyright 2017 Mycroft AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Bus message classes for ``ovos-bus-client``.

The OVOS-MSG-1 envelope lives in :mod:`ovos_spec_tools.message`; this
module re-exports it directly — no subclass, no wrapping — and attaches
the one legacy convenience method downstream still uses
(:meth:`Message.publish`) to the class at import time. Everything else
(:class:`CollectionMessage`, :class:`GUIMessage`, :func:`dig_for_message`,
the encryption helpers) stays in this module on top of the same class.

Transport-layer encryption is no longer wired into :meth:`Message.serialize`
/ :meth:`Message.deserialize` — encryption is a transport concern that
belongs in the websocket :class:`~ovos_bus_client.client.client.MessageBusClient`,
not on the envelope (OVOS-MSG-1 §7 non-goals). The
:func:`encrypt_as_dict` / :func:`decrypt_from_dict` helpers stay at
module level for any consumer that imported them directly.
"""
import inspect
import json
import warnings
from binascii import hexlify, unhexlify
from typing import Any, Dict, Optional

from ovos_spec_tools.message import (
    DEFAULT_SESSION_ID,
    MalformedMessage,
    Message,
)
from ovos_utils import json_dumps
from ovos_utils.log import deprecated
from ovos_utils.security import encrypt, decrypt

from ovos_bus_client.version import VERSION_MAJOR

# ``MalformedMessage`` in ovos-spec-tools >= 0.5.1a1 multi-inherits
# ``ValueError`` and ``AssertionError``, so legacy ``except
# AssertionError`` handlers around Message construction continue to
# catch validation failures without any local re-declaration here.
# ``Message.as_dict`` is also a property on the spec-tools class itself.

# OVOS-MSG-1 defines forward / reply / response as the three normative
# derivations (§5). ``publish`` is a bus-client tradition outside the
# spec; it survives as an attached method for one more major release so
# downstream consumers can migrate.
_PUBLISH_REMOVAL_VERSION = f"{VERSION_MAJOR + 1}.0.0"


__all__ = [
    "Message",
    "MalformedMessage",
    "DEFAULT_SESSION_ID",
    "CollectionMessage",
    "GUIMessage",
    "dig_for_message",
    "encrypt_as_dict",
    "decrypt_from_dict",
]


@deprecated(
    "Message.publish is deprecated; use Message.forward (relay under a "
    "new topic, preserves context) or Message.reply (§5.2 swap) — both "
    "are OVOS-MSG-1 normative",
    _PUBLISH_REMOVAL_VERSION)
def _publish(self, msg_type: str, data: Dict[str, Any],
             context: Optional[Dict[str, Any]] = None) -> Message:
    """Relay a Message under a new topic without the §5.2 swap.

    Copies ``self.context``, overlays the optional ``context`` argument,
    drops any ``target`` key, and emits a new Message with the supplied
    ``msg_type`` and ``data``. ``data`` is **not** deep-copied.

    Attached to :class:`ovos_spec_tools.Message` at import time so this
    bus-client method appears on the class downstream code already
    imports — no subclass, no isinstance surprises.

    .. deprecated::
        Not part of OVOS-MSG-1 (the spec defines ``forward`` /
        ``reply`` / ``response`` as the only normative derivations).
        Slated for removal in the next major; use :meth:`forward`
        when you do not want the routing-key swap, or :meth:`reply`
        when you do.
    """
    # stacklevel=3: warn() -> body -> @deprecated wrapper -> caller
    warnings.warn(
        "Message.publish is deprecated; use Message.forward (no §5.2 "
        "swap) or Message.reply (with swap) instead — both are "
        "OVOS-MSG-1 normative derivations. ``publish`` will be removed "
        f"in ovos-bus-client {_PUBLISH_REMOVAL_VERSION}.",
        DeprecationWarning, stacklevel=3)
    context = context or {}
    new_context = dict(self.context)
    new_context.update(context)
    new_context.pop("target", None)
    # Always return a plain Message — CollectionMessage and GUIMessage
    # have incompatible constructors so self.__class__(...) would raise.
    return Message(msg_type, data, new_context)


# Attach the legacy publish() to the spec-tools Message. This makes the
# method available on every Message instance — including those
# constructed by code that imports Message from ovos_spec_tools directly,
# or from ovos_utils.fakebus — without forcing a subclass or breaking
# isinstance checks across the ecosystem.
Message.publish = _publish


def encrypt_as_dict(key: str, data: str, nonce=None) -> dict:
    """AES-encrypt ``data`` under ``key``, returning the
    ``{ciphertext, tag, nonce}`` dict the websocket transport uses.

    Kept at module level for downstream consumers that imported it
    directly. No longer wired into :meth:`Message.serialize` —
    encryption is the transport's concern.
    """
    ciphertext, tag, nonce = encrypt(key, data, nonce=nonce)
    return {"ciphertext": hexlify(ciphertext).decode('utf-8'),
            "tag": hexlify(tag).decode('utf-8'),
            "nonce": hexlify(nonce).decode('utf-8')}


def decrypt_from_dict(key: str, data: dict) -> str:
    """Reverse of :func:`encrypt_as_dict`. Accepts the legacy "web
    crypto" form too (no separate ``tag``, GCM tag concatenated to the
    ciphertext)."""
    ciphertext = unhexlify(data["ciphertext"])
    if data.get("tag") is None:  # web crypto
        ciphertext, tag = ciphertext[:-16], ciphertext[-16:]
    else:
        tag = unhexlify(data["tag"])
    nonce = unhexlify(data["nonce"])
    return decrypt(key, ciphertext, tag, nonce)


def dig_for_message(max_records: int = 10) -> Optional[Message]:
    """Walk the call stack looking for a :class:`Message` argument.

    Used inside handlers that don't receive the triggering message
    explicitly but still want to read its context — typically for
    :attr:`session` / :attr:`lang` propagation.

    Returns the first ``Message`` found in any positional argument of
    the surrounding frames, or ``None``.
    """
    stack = inspect.stack()[1:]  # first frame is this function call
    stack = stack if len(stack) <= max_records else stack[:max_records]
    for record in stack:
        args = inspect.getargvalues(record.frame)
        if args.args:
            for arg in args.args:
                if isinstance(args.locals[arg], Message):
                    return args.locals[arg]
    return None


class CollectionMessage(Message):
    """Extension of :class:`Message` for use with collect handlers.

    Adds :meth:`success`, :meth:`failure`, and :meth:`extend` convenience
    methods that emit the right ``.response`` / ``.handling`` topics
    with the collect-protocol payload (``query``, ``handler``,
    ``succeeded``, ``timeout``).
    """

    def __init__(self, msg_type, handler_id, query_id, data=None, context=None):
        super().__init__(msg_type, data, context)
        self.handler_id = handler_id
        self.query_id = query_id

    @classmethod
    def from_message(cls, message: Message, handler_id: str,
                     query_id: str) -> "CollectionMessage":
        """Wrap an existing :class:`Message` into a
        :class:`CollectionMessage` with the same ``msg_type`` / ``data``
        / ``context`` plus the collect-protocol ``handler_id`` and
        ``query_id``."""
        return cls(message.msg_type, handler_id, query_id,
                   message.data, message.context)

    def success(self, data=None, context=None) -> Message:
        """Emit a ``<msg_type>.response`` carrying
        ``{query, handler, succeeded: True}`` (plus any provided ``data``).
        Routing keys are swapped per OVOS-MSG-1 §5.2 via
        :meth:`Message.reply`."""
        data = data or {}
        data['query'] = self.query_id
        data['handler'] = self.handler_id
        data['succeeded'] = True
        return self.reply(self.msg_type + '.response',
                          data, context or self.context)

    def failure(self) -> Message:
        """Emit a ``<msg_type>.response`` carrying
        ``{query, handler, succeeded: False}``."""
        data = {
            'query': self.query_id,
            'handler': self.handler_id,
            'succeeded': False,
        }
        return self.reply(self.msg_type + '.response', data, self.context)

    def extend(self, timeout) -> Message:
        """Emit a ``<msg_type>.handling`` extension carrying
        ``{query, handler, timeout}``."""
        data = {
            'query': self.query_id,
            'handler': self.handler_id,
            'timeout': timeout,
        }
        return self.reply(self.msg_type + '.handling', data, self.context)

    # Spec-tools Message.{forward,reply,response} call self.__class__(...)
    # to preserve subclass identity through derivation chains, but
    # CollectionMessage's __init__ takes extra positional args
    # (handler_id, query_id) that the derivations don't know about.
    # Override the three to drop back to a plain Message — matching the
    # legacy behaviour where reply() returned a Message, not a
    # CollectionMessage.
    def forward(self, msg_type, data=None):  # type: ignore[override]
        from copy import deepcopy
        return Message(msg_type, data or {}, deepcopy(self.context))

    def reply(self, msg_type, data=None, context=None):  # type: ignore[override]
        from copy import deepcopy
        new_context = deepcopy(self.context)
        if context:
            new_context.update(context)
        src = new_context.get("source")
        dst = new_context.get("destination")
        if dst is not None:
            new_context["source"] = (
                dst[0] if isinstance(dst, list) and dst else dst)
        if src is not None:
            new_context["destination"] = src
        return Message(msg_type, data or {}, new_context)

    def response(self, data=None, context=None):  # type: ignore[override]
        return self.reply(self.msg_type + ".response", data, context)


class GUIMessage(Message):
    """A :class:`Message` whose serialization flattens ``data`` keys
    into the top-level JSON object instead of nesting them under
    ``"data"``. Used by the OVOS GUI bus where the wire format predates
    the standard ``type`` / ``data`` / ``context`` envelope and expects
    its fields at the top level.

    Constructor takes keyword arguments which become the ``data`` dict:

    >>> GUIMessage("gui.show", page="main", url="...").data
    {'page': 'main', 'url': '...'}
    """

    def __init__(self, msg_type, **kwargs):
        super().__init__(msg_type, data=kwargs)

    def serialize(self) -> str:
        """Flatten ``data`` into the top-level object — the GUI wire
        format. Nested objects with a ``.serialize()`` method are
        converted via :meth:`Message._to_jsonable` first."""
        data = Message._to_jsonable(self.data)
        return json_dumps({"type": self.msg_type, **data})

    @classmethod
    def deserialize(cls, value) -> "GUIMessage":
        """Inverse of :meth:`serialize` — takes a flat JSON object with
        ``type`` at top-level, returns a :class:`GUIMessage`."""
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8")
        if isinstance(value, str):
            obj = json.loads(value)
        else:
            obj = dict(value)
        msg_type = obj.pop("type", None)
        if msg_type is None:
            raise MalformedMessage("GUIMessage requires a 'type' key (§2.1)")
        return cls(msg_type, **obj)

    # GUIMessage.__init__ takes **kwargs, not the standard
    # (msg_type, data, context) shape. Override the derivations so
    # they drop back to plain Message rather than trying to construct
    # a GUIMessage with positional args that won't fit.
    def forward(self, msg_type, data=None):  # type: ignore[override]
        from copy import deepcopy
        return Message(msg_type, data or {}, deepcopy(self.context))

    def reply(self, msg_type, data=None, context=None):  # type: ignore[override]
        return CollectionMessage.reply(  # reuse the swap logic
            self, msg_type, data, context)  # type: ignore[arg-type]

    def response(self, data=None, context=None):  # type: ignore[override]
        return self.reply(self.msg_type + ".response", data, context)
