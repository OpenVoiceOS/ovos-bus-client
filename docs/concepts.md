# Core concepts

What the OVOS bus actually is, what travels over it, and which pieces live in
`ovos-bus-client`.

> **Specification.** The Message envelope, routing keys, session carrier and
> derivations described on this page are normatively defined by
> [**OVOS-MSG-1**](https://github.com/OpenVoiceOS/architecture/blob/dev/msg-1.md)
> in the [OVOS Architecture](https://github.com/OpenVoiceOS/architecture) repo.
> `ovos_bus_client.Message` re-exports the reference implementation in
> [`ovos-spec-tools`](https://github.com/OpenVoiceOS/ovos-spec-tools) directly,
> so the envelope shape, unknown-key tolerance, and the `forward` / `reply` /
> `response` derivations match the spec. The session-carrier wire shape is now
> normatively owned by [**OVOS-SESSION-1**](https://github.com/OpenVoiceOS/architecture/blob/dev/session-1.md),
> and the `IntentContextManager` sub-object by
> [**OVOS-CONTEXT-1**](https://github.com/OpenVoiceOS/architecture/blob/dev/intent-context.md) —
> both merged. Known divergences from full conformance are catalogued in the
> [architecture appendix §5](https://github.com/OpenVoiceOS/architecture/blob/dev/appendix/divergences.md#5-where-the-specs-differ-from-the-reference-implementation):
> `destination` still accepts an array, the `ovos.session.sync` /
> `ovos.session.update_default` push topics are slated for V1 removal, and
> `context.utterance_id` poll correlation is not yet implemented.

## The bus is OVOS's nervous system — and it is private

Before anything else: **the OVOS bus is the nervous system of the assistant**.
Every component — STT, intent parsing, skills, TTS, audio playback, GUI —
reads and writes on it. It is how OVOS thinks.

That has one critical consequence:

> **The bus has no authentication and no authorisation. Every connected
> client has full access to every message. Treat it as private to the
> device.**

Anything that can speak on the bus can:

- Issue any natural-language command (`recognizer_loop:utterance`).
- Speak arbitrary text out of the speakers (`speak`).
- Start and stop audio playback, navigate menus, change settings.
- Activate or deactivate any skill, override any persona.
- Read every other client's messages, including transcribed user speech.

That is by design — the components inside an OVOS install have to trust each
other. It is also why you do **not** expose the bus port outside the device:

- **Bind to `127.0.0.1`**, never `0.0.0.0`. The default config already does this.
- **Never port-forward** the bus across NAT.
- **Never put the bus behind a public reverse proxy.**
- **Never share bus credentials** — there are no credentials, so any "sharing"
  is "give them full root over the assistant."

For anything that needs **remote access** to an OVOS device — a satellite, a
phone app, a third-party integration — use [HiveMind](https://github.com/JarbasHiveMind).
HiveMind sits in front of the bus, terminates encrypted client connections,
authenticates by `api_key`, applies ACLs and policy plugins, and forwards
only what the policy allows onto the bus. That is the right tool for external
clients, including ones running on the same LAN.

You would not give a stranger a direct connection to your brainstem. Do not
give a stranger a direct connection to the OVOS bus.

## The bus is a JSON-over-WebSocket pub/sub

OVOS components don't call each other directly. They open a WebSocket to a
central server (the **messagebus**) and exchange newline-delimited JSON
messages on it.

- Anyone can publish any message type.
- Anyone can subscribe to any message type.
- There is no schema enforcement at the transport layer.

The protocol is named after Mycroft (OVOS's predecessor); you will see the
term "mycroft bus" used interchangeably.

`ovos-bus-client` is the Python client for that server. It does not contain
the server itself — that lives in `ovos-core` (currently
`ovos-messagebus`). Nothing in this package assumes a particular server
implementation; any WebSocket endpoint that speaks the OVOS message format
will do, which is why `FakeBus` in `ovos-utils` is a complete in-memory drop-in
for tests.

## Anatomy of a message

Every bus message is a triple:

| Field      | Type   | Meaning                                                     |
|------------|--------|-------------------------------------------------------------|
| `msg_type` | `str`  | Event name. Dot-delimited convention: `mycroft.mic.listen`. |
| `data`     | `dict` | The payload. Schema is whatever the producer chose.         |
| `context`  | `dict` | Routing/metadata: `session`, `source`, `destination`, etc.  |

The `Message` class wraps these and is defined at
`ovos_bus_client/message.py:32`.

```python
from ovos_bus_client import Message

m = Message(
    "speak",
    {"utterance": "hi", "lang": "en-us"},
    {"source": "demo", "destination": "audio"},
)
```

On the wire the message is JSON:

```json
{"type": "speak", "data": {"utterance": "hi", "lang": "en-us"},
 "context": {"source": "demo", "destination": "audio"}}
```

## Message types are by convention, not declaration

The bus itself enforces no schema. But the OVOS project ships a machine-readable
index of every known message type, with Pydantic v2 models for each, in
[**ovos-pydantic-models**](https://github.com/OpenVoiceOS/ovos-pydantic-models)
(browsable docs: <https://openvoiceos.github.io/ovos-pydantic-models/>). When
you're looking up a message type, that's the authoritative reference.

```python
from ovos_pydantic_models import SpeakMessage, SpeakData

msg = SpeakMessage(data=SpeakData(utterance="hi", lang="en-us"))
# Pydantic-validated; can be .model_dump() onto the bus.
```

`ovos-bus-client` itself does **not** depend on `ovos-pydantic-models` — the
bus uses plain dicts on the wire, by design. The Pydantic models are an
optional validation layer that consumers can opt into for type safety,
documentation generation, or integration testing.

Conventions you will see (validated by the Pydantic models when they apply):

- `mycroft.<component>.<verb>` — internal OVOS events
  (`mycroft.mic.listen`, `mycroft.audio.service.play`).
- `recognizer_loop:<event>` — speech recognition lifecycle
  (`recognizer_loop:utterance`, `recognizer_loop:record_begin`).
- `<your.event>` — your own events. Pick a prefix and stick to it.
- `<your.event>.response` — the standard reply-suffix convention used by
  `wait_for_response` (`ovos_bus_client/client/client.py:296`).
- `<your.event>.handling` — extend-timeout ack used by `collect_responses`
  (`ovos_bus_client/client/client.py:245`).

If you are designing a new message type, prefix it with your component name
and document the `data` schema in your own docs.

## Context is where routing lives

`context` is not "extra junk." It carries everything that routes a message
through the OVOS stack:

- `session` — serialized `Session` object. The single most important field;
  see [Sessions](session.md).
- `source`, `destination` — used by message-relay components (HiveMind,
  multi-bus bridges) to know where this message came from and where to send
  it next.
- `client_name`, `platform`, `ident`, `timing` — assorted breadcrumbs.

`Message.forward()` and `Message.reply()` preserve `context` automatically and
swap `source`/`destination` on `reply` (`ovos_bus_client/message.py:148,166`).
Use them; do not hand-roll context propagation.

## Who lives in this package

`ovos-bus-client` contains:

| Module | Role |
|---|---|
| `client/client.py` | `MessageBusClient`, `GUIWebsocketClient` — WebSocket connection management |
| `client/waiter.py` | `MessageWaiter` — block for one matching reply |
| `client/collector.py` | `MessageCollector` — multi-handler collect-call pattern |
| `message.py` | `Message`, `GUIMessage`, `CollectionMessage`, `dig_for_message`, encryption helpers |
| `session.py` | `Session`, `SessionManager`, `IntentContextManager`, `UtteranceState` |
| `conf.py` | Config loading for host/port/route/ssl |
| `send_func.py` | One-shot `send` helper |
| `scripts.py` | CLI tools (`ovos-speak`, etc.) |
| `apis/gui.py` | `GUIInterface` — high-level GUI page/variable API |
| `apis/ocp.py` | `OCPInterface` — OCP media player control |
| `apis/enclosure.py` | `EnclosureAPI` — Mark 1 enclosure (eyes, mouth) |
| `apis/events.py` | `EventSchedulerInterface` — schedule one-shot and repeating events |

The pattern across `apis/` is the same: thin, typed wrappers that emit the
right `Message` and (when needed) wait for the right reply. Reading any one of
them is a good way to learn idiomatic bus usage.

## The threading model

`MessageBusClient` runs the WebSocket in its own thread when you call
`run_in_thread()`. Your handlers fire on that thread. If you do any blocking
work in a handler, you block the bus loop — wrap long work in your own
threads.

`run_forever()` is the same loop but on the calling thread; the program
exits or proceeds only when the connection ends.

`connected_event` (`threading.Event`) is set when the socket finishes its
handshake, cleared when it disconnects (`ovos_bus_client/client/client.py:54`).
Always `wait()` on it before emitting if you might race the handshake.

## Sessions: the only mandatory concept

You can ignore `data` schemas. You can ignore `context` routing fields. You
cannot ignore `Session` if you are doing anything multi-turn.

A `Session` is the OVOS pipeline's notion of "this user, this device, this
conversation" — language, active skills, intent context, response-mode flags
all live there. Every message that flows through the OVOS pipeline carries a
serialized Session in `context["session"]`.

The `SessionManager` is an in-process singleton that owns the
`session_id -> Session` map and ingests Session updates from every incoming
bus message automatically. Downstream consumers (chat agents, HiveMind
listeners, skills using `MycroftSkill.get_response`) all depend on this.

See [Sessions](session.md) for the full story.
