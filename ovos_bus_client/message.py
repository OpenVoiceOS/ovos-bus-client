# Copyright 2017 Mycroft AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
"""Bus message classes for ``ovos-bus-client``.

The OVOS-MSG-1 envelope lives in :mod:`ovos_spec_tools.message`; this
module re-exports it directly — no subclass, no wrapping — and attaches
the legacy bus-client conveniences downstream still uses
(:meth:`Message.publish`, plus encryption-aware ``serialize`` /
``deserialize``) to the class at import time. Everything else
(:class:`CollectionMessage`, :class:`GUIMessage`,
:func:`dig_for_message`, the encryption helpers) stays in this module
on top of the same class.

An optional **layer-2 encryption scheme** ships with this package, but it
lives at the **transport edge** (:class:`ovos_bus_client.client.MessageBusClient`
``emit`` / ``on_message``), not on the :class:`Message` class itself.
OVOS-MSG-1 is transport-agnostic and says nothing about encryption (§7);
bolting AES (GCM) onto the JSON envelope is purely additive on top of the
spec, and the matching key-setup / key-exchange side was never formally
implemented — so the scheme is **deprecated** and the websocket client
emits a ``DeprecationWarning`` whenever it fires. The
:func:`encrypt_as_dict` / :func:`decrypt_from_dict` helpers stay at module
level for any consumer that imported them directly.
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


# --- legacy envelope-level encryption helpers (deprecated) -----------------
#
# These are kept at module level for any consumer that imported them
# directly. The websocket transport (:class:`ovos_bus_client.client.
# MessageBusClient`) uses them at its send/receive edges when
# ``websocket.secret_key`` is configured; the :class:`Message` class
# itself stays pure JSON.


def encrypt_as_dict(key: str, data: str, nonce=None) -> dict:
    """
    AES-encrypt data under key and produce the websocket-transport mapping.
    
    Kept at module level for consumers that import this helper directly; encryption is intentionally handled at the transport layer and is no longer integrated into Message.serialize/deserialize.
    
    Parameters:
        key (str): Encryption key passed to the underlying encrypt routine.
        data (str): Plaintext to encrypt.
        nonce (optional): Nonce value forwarded to the underlying encrypt routine; if omitted the encrypt implementation will generate one.
    
    Returns:
        dict: Mapping with hex-encoded fields "ciphertext", "tag", and "nonce" suitable for websocket transport.
    """
    ciphertext, tag, nonce = encrypt(key, data, nonce=nonce)
    return {"ciphertext": hexlify(ciphertext).decode('utf-8'),
            "tag": hexlify(tag).decode('utf-8'),
            "nonce": hexlify(nonce).decode('utf-8')}


def decrypt_from_dict(key: str, data: dict) -> str:
    """
    Decrypt a websocket-transport formatted dict into a plaintext string.
    
    Accepts a dict produced by encrypt_as_dict: hex-encoded "ciphertext" and "nonce", and an optional hex-encoded "tag". If "tag" is missing, treats the last 16 bytes of the decoded ciphertext as the GCM tag (legacy web-crypto format) before decrypting.
    
    Parameters:
        key (str): Key used for decryption.
        data (dict): Mapping with keys:
            - "ciphertext" (str): Hex string of the ciphertext (possibly with tag appended).
            - "nonce" (str): Hex string of the nonce.
            - "tag" (str, optional): Hex string of the authentication tag; if absent, tag is taken from the end of the ciphertext.
    
    Returns:
        str: Decrypted plaintext.
    """
    ciphertext = unhexlify(data["ciphertext"])
    if data.get("tag") is None:  # web crypto
        ciphertext, tag = ciphertext[:-16], ciphertext[-16:]
    else:
        tag = unhexlify(data["tag"])
    nonce = unhexlify(data["nonce"])
    return decrypt(key, ciphertext, tag, nonce)


def dig_for_message(max_records: int = 10) -> Optional[Message]:
    """
    Search the call stack for the first Message instance passed as a positional argument.
    
    Useful for handlers that do not receive the triggering Message directly but need to access its context (for example, to propagate session or language).
    
    Parameters:
        max_records (int): Maximum number of stack frames to inspect (most-recent first).
    
    Returns:
        Message | None: The first Message found among positional arguments in inspected frames, or `None` if none is found.
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
        """
        Initialize a CollectionMessage carrying collect-handler identifiers and the standard message payload.
        
        Parameters:
            msg_type (str): Message type/topic.
            handler_id (str): Identifier of the collect handler servicing this query.
            query_id (str): Identifier of the query being collected.
            data (dict, optional): Message payload data. Defaults to None.
            context (dict, optional): Message routing/context metadata. Defaults to None.
        """
        super().__init__(msg_type, data, context)
        self.handler_id = handler_id
        self.query_id = query_id

    @classmethod
    def from_message(cls, message: Message, handler_id: str,
                     query_id: str) -> "CollectionMessage":
        """
        Create a CollectionMessage from an existing Message, preserving its type, data, and context while attaching collect-protocol identifiers.
        
        Parameters:
            message (Message): Source message whose msg_type, data, and context will be copied.
            handler_id (str): Identifier of the collect handler to attach.
            query_id (str): Identifier of the collect query to attach.
         
        Returns:
            CollectionMessage: A new CollectionMessage with the same msg_type, data, and context as `message` and the provided `handler_id` and `query_id`.
        """
        return cls(message.msg_type, handler_id, query_id,
                   message.data, message.context)

    def success(self, data=None, context=None) -> Message:
        """
        Create a response message for the collect query indicating success.
        
        The returned message has type "<original msg_type>.response" and includes the provided data merged with the keys:
        - "query": the originating query id,
        - "handler": the handler id,
        - "succeeded": True.
        
        Parameters:
            data (dict, optional): Additional payload to include in the response; merged into the message data.
            context (dict, optional): Context to use for the response; if omitted, the message's own context is used.
        
        Returns:
            Message: A message whose data contains the merged payload and the success metadata described above.
        """
        data = data or {}
        data['query'] = self.query_id
        data['handler'] = self.handler_id
        data['succeeded'] = True
        return self.reply(self.msg_type + '.response',
                          data, context or self.context)

    def failure(self) -> Message:
        """
        Create a response Message indicating the query handling failed.
        
        Returns:
            Message: A response Message with msg_type "<original>.response", data containing
            `query` (this message's query_id), `handler` (this message's handler_id), and
            `succeeded` set to `False`; the response uses this message's context.
        """
        data = {
            'query': self.query_id,
            'handler': self.handler_id,
            'succeeded': False,
        }
        return self.reply(self.msg_type + '.response', data, self.context)

    def extend(self, timeout) -> Message:
        """
        Send a handling-extension message for this collection query.
        
        Parameters:
            timeout (int|float): The requested additional handling time (units as used by the protocol).
        
        Returns:
            Message: A reply Message with type "<msg_type>.handling" whose data contains the keys `query`, `handler`, and `timeout`.
        """
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
        """
        Create a new Message with the given type and payload using a deep copy of this message's context.
        
        Parameters:
            msg_type (str): The routing/topic string for the new message.
            data (dict, optional): Payload for the new message; defaults to an empty dict.
        
        Returns:
            Message: A new Message constructed with `msg_type`, `data` (or `{}`), and a deep-copied context from this message.
        """
        from copy import deepcopy
        return Message(msg_type, data or {}, deepcopy(self.context))

    def reply(self, msg_type, data=None, context=None):  # type: ignore[override]
        """
        Create a reply Message with the context merged and OVOS-MSG-1 routing fields swapped.
        
        Merges a deep copy of this message's context with the optional `context` overlay, then applies routing swap semantics: if `destination` exists, sets `source` to the first element of `destination` when it's a list (or to `destination` directly); if `source` exists, sets `destination` to `source`. Constructs and returns a plain `Message` with the provided `msg_type`, `data` (or an empty dict), and the modified context.
        
        Parameters:
            msg_type (str): The message type/topic for the reply.
            data (dict|None): Payload for the reply; an empty dict is used when omitted.
            context (dict|None): Optional context entries to overlay onto a deep copy of this message's context.
        
        Returns:
            Message: A new Message with `msg_type`, the supplied `data` (or `{}`), and the merged context where `source` and `destination` have been swapped for routing.
        """
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
        """
        Create a response Message whose message type is this message's type with ".response" appended.
        
        Parameters:
        	data (dict, optional): Payload to include in the response. If omitted, an empty dict is used.
        	context (dict, optional): Context values to merge with this message's context; provided keys overlay the existing context.
        
        Returns:
        	Message: A Message with msg_type equal to this message's msg_type + ".response" and a context derived from this message merged with `context` if provided.
        """
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
        """
        Create a GUIMessage with the provided message type and use keyword arguments as the message payload.
        
        The keyword arguments are stored as the message's data mapping (equivalent to passing a dict via the `data` parameter to the base Message).
        """
        super().__init__(msg_type, data=kwargs)

    def serialize(self) -> str:
        """Serialize the message into the GUI wire-format with message
        fields flattened to the top level. Returns plain JSON; the
        transport layer applies the deprecated envelope encryption when
        ``websocket.secret_key`` is configured."""
        data = Message._to_jsonable(self.data)
        return json_dumps({"type": self.msg_type, **data})

    @classmethod
    def deserialize(cls, value) -> "GUIMessage":
        """
        Deserialize a GUI wire-format message into a GUIMessage instance.
        
        Parameters:
            value (bytes|bytearray|str|Mapping): Message payload in one of:
                - bytes/bytearray: UTF-8 encoded JSON
                - str: JSON text
                - Mapping: a mapping-like object (will be converted to dict)
        
        Returns:
            GUIMessage: a new instance created from the top-level `type` value and remaining fields.
        
        Raises:
            MalformedMessage: if the input does not contain a top-level `type` key.
        """
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
        """
        Create a new Message with the given type and payload using a deep copy of this message's context.
        
        Parameters:
            msg_type (str): The routing/topic string for the new message.
            data (dict, optional): Payload for the new message; defaults to an empty dict.
        
        Returns:
            Message: A new Message constructed with `msg_type`, `data` (or `{}`), and a deep-copied context from this message.
        """
        from copy import deepcopy
        return Message(msg_type, data or {}, deepcopy(self.context))

    def reply(self, msg_type, data=None, context=None):  # type: ignore[override]
        """
        Create a reply that applies the OVOS routing source/destination swap.
        
        Parameters:
            msg_type (str): Topic/type for the reply message.
            data (dict, optional): Payload for the reply; defaults to an empty dict when absent.
            context (dict, optional): Context to overlay onto the message's context before replying.
        
        Returns:
            Message: A plain Message whose routing has had source and destination swapped and whose context includes any provided overlays.
        """
        return CollectionMessage.reply(  # reuse the swap logic
            self, msg_type, data, context)  # type: ignore[arg-type]

    def response(self, data=None, context=None):  # type: ignore[override]
        """
        Create a response Message whose message type is this message's type with ".response" appended.
        
        Parameters:
        	data (dict, optional): Payload to include in the response. If omitted, an empty dict is used.
        	context (dict, optional): Context values to merge with this message's context; provided keys overlay the existing context.
        
        Returns:
        	Message: A Message with msg_type equal to this message's msg_type + ".response" and a context derived from this message merged with `context` if provided.
        """
        return self.reply(self.msg_type + ".response", data, context)
