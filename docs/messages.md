# Messages

Reference for the `Message` class and its helpers. The envelope shape, routing
keys, session carrier and derivations are normatively defined by
[**OVOS-MSG-1**](https://github.com/OpenVoiceOS/architecture/blob/dev/msg-1.md);
the implementation here is a re-export of
[`ovos_spec_tools.message.Message`](https://github.com/OpenVoiceOS/ovos-spec-tools)
with `publish` attached for back-compat.

> **Looking for the catalogue of valid message types?** That lives in
> [**ovos-pydantic-models**](https://github.com/OpenVoiceOS/ovos-pydantic-models)
> (full index: <https://openvoiceos.github.io/ovos-pydantic-models/>). It is
> the authoritative, machine-readable specification of the OVOS MessageBus
> protocol, with a Pydantic v2 model per message type. This document covers
> the **transport** — how `Message` objects work — not the vocabulary.

## `Message` — `ovos_bus_client/message.py:32`

The fundamental unit of communication on the bus.

### Constructor

```python
Message(msg_type: str, data: dict = None, context: dict = None)
```

- `msg_type` — the event name (`"speak"`, `"recognizer_loop:utterance"`, ...).
- `data` — payload; arbitrary JSON-serialisable dict.
- `context` — routing metadata; arbitrary JSON-serialisable dict.

Both `data` and `context` default to `{}` if omitted.

### Serialisation

| Method | Returns | Purpose |
|---|---|---|
| `m.serialize()` | `str` | JSON text suitable to send on the wire (`message.py:70`) |
| `m.as_dict()` | `dict` | Plain dict form (`message.py:90`) |
| `Message.deserialize(str)` | `Message` | Parse JSON text back into a `Message` (`message.py:127`) |

`serialize()` and `deserialize()` are pure JSON — they neither encrypt nor
decrypt. Any envelope encryption is a separate, deprecated layer applied at the
transport edge by [`MessageBusClient`](client.md) — see
[The client → Deprecated transport-edge encryption](client.md#deprecated-transport-edge-encryption).
[`MessageBusClient.emit`](client.md#emitting) calls `serialize()` for you; you
rarely call it directly.

### Reply helpers

These four methods construct a new message from an existing one, propagating
`context` correctly. **Always prefer these over hand-building reply messages.**

| Method | When to use |
|---|---|
| `m.forward(msg_type, data=None)` | Re-emit the message under a new type with the **same** context (no source/destination swap). Use when you are relaying, not responding. (`message.py:148`) |
| `m.reply(msg_type, data=None, context=None)` | Build a reply that swaps `source` ↔ `destination` and copies the rest. The standard "I am responding to this" helper. (`message.py:166`) |
| `m.response(data=None, context=None)` | Shortcut for `m.reply(m.msg_type + ".response", ...)`. Use this to honour the `wait_for_response` convention. (`message.py:201`) |
| `m.publish(msg_type, data, context=None)` | Like `reply` but `data` is **not** deep-copied. Slightly faster for hot paths. (`message.py:216`) |

```python
def on_query(message):
    answer = compute(message.data["q"])
    bus.emit(message.response({"answer": answer}))
```

## Equality

`Message.__eq__` compares `msg_type`, `data`, and `context`. Two messages with
the same triple are equal regardless of object identity (`message.py:63`).

## `dig_for_message(max_records=10)` — `ovos_bus_client/message.py:258`

Walks back through the call stack looking for a local variable named
`message` of type `Message`. Returns the first one found, or `None`.

Used by code that needs the "current" message but does not have it as an
argument — for example, deeply nested skill helpers that want to know which
session their caller was responding to.

```python
from ovos_bus_client.message import dig_for_message

def some_deep_helper():
    msg = dig_for_message()
    if msg is not None:
        ...
```

Treat it as a last resort. Explicitly threading `message` through call chains
is always clearer.

## `CollectionMessage` — `ovos_bus_client/message.py:279`

A specialisation used by the collect-responses protocol. You only see this
inside an `on_collect` handler. It carries:

- `handler_id` — the unique ID of the handler that received the query.
- `query_id` — the ID of the original collection query.

It adds three methods used to report progress back to the caller:

| Method | Purpose |
|---|---|
| `cm.success(data=None, context=None)` | Report a successful answer (`message.py:306`). |
| `cm.failure()` | Decline to answer; remove yourself from the wait set (`message.py:327`). |
| `cm.extend(timeout)` | Ask the caller to wait longer (`message.py:348`). |

See [Waiters and collectors](waiter_and_collector.md) for the full pattern.

## `GUIMessage` — `ovos_bus_client/message.py:371`

A variant used over the GUI-specific WebSocket (port 18181 by default). Same
shape but it serialises arguments directly at the top level rather than under
`data` — the Qt GUI protocol expects this. You only use it via
`GUIWebsocketClient` and `GUIInterface`.

## Encryption helpers

`encrypt_as_dict(key, data, nonce=None)` and `decrypt_from_dict(key, data)` —
`ovos_bus_client/message.py:128,148` — wrap an AES-GCM symmetric cipher. They
are exported from `ovos_bus_client.message` for any direct importer (e.g.
HiveMind), but **are not called by `Message.serialize` or
`Message.deserialize`**, which produce and consume plain JSON.

The OVOS core bus loop wires these helpers at the **transport edge** inside
`MessageBusClient`, not inside `Message`. See
[The client → Deprecated transport encryption](client.md#deprecated-transport-edge-encryption).

## Validating against the protocol spec

For type-safety, integration tests, or generated documentation, validate
your messages against `ovos-pydantic-models`:

```python
from ovos_pydantic_models import SpeakMessage
from ovos_bus_client import Message

raw = Message("speak", {"utterance": "hi", "lang": "en-us"}).as_dict()
# rename the wire field for Pydantic, which uses message_type
raw["message_type"] = raw.pop("type")
SpeakMessage.model_validate(raw)   # raises ValidationError if malformed
```

The dependency is optional — `ovos-bus-client` itself ships plain
`Message` dicts. Add `ovos-pydantic-models` to your test extras when you
want a schema gate at the boundary of your component.

## Conventions for new message types

When you invent a message type:

- Pick a stable prefix and stick to it (your skill_id, your component name).
- Document the `data` schema in your own README, and consider contributing
  a Pydantic model for it to
  [ovos-pydantic-models](https://github.com/OpenVoiceOS/ovos-pydantic-models)
  so the wider ecosystem can validate it.
- If you expect a reply, follow the `.response` suffix convention so callers
  can use `wait_for_response` without specifying `reply_type`.
- If multiple handlers might answer, follow the `.handling` suffix convention
  (`message.py:CollectionMessage` and `client.py:on_collect`).
- Never put session state in `data`; that belongs in `context["session"]`.
