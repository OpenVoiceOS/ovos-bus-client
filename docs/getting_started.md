# Getting started

A 10-minute tour from `pip install` to receiving and emitting bus messages.

## Install

```bash
pip install ovos-bus-client
```

## Before you start: the bus is private

The OVOS bus is the assistant's **nervous system**. It has no authentication
— every connected client has full access to every message and can issue any
command. Keep it bound to `127.0.0.1` (the default), never expose it on a
network interface, never put it behind a reverse proxy. For remote access to
an OVOS device, use [HiveMind](https://github.com/JarbasHiveMind), which adds
encryption, identity, and policy enforcement on top.

See [Core concepts → The bus is OVOS's nervous system](concepts.md#the-bus-is-ovoss-nervous-system--and-it-is-private)
for the full reasoning.

## Hello, bus

You need a running OVOS messagebus to talk to. On a standard OVOS install, that
is the WebSocket server at `ws://127.0.0.1:8181/core`. If you have `ovos-core`
running locally, you already have it.

The smallest possible program: connect, say "hello", disconnect.

```python
from ovos_bus_client import MessageBusClient, Message

bus = MessageBusClient()
bus.run_in_thread()
bus.connected_event.wait()

bus.emit(Message("speak", {"utterance": "hello world"}))

bus.close()
```

Walking through it:

- `MessageBusClient()` reads host/port from your OVOS configuration
  (`mycroft.conf`, `websocket` section). With no config it defaults to
  `127.0.0.1:8181/core` (`ovos_bus_client/conf.py:18`).
- `run_in_thread()` starts the WebSocket loop on a background thread.
- `connected_event.wait()` blocks until the socket handshake is done.
- `emit(Message(...))` sends a serialized message over the wire.
- `close()` tears down the connection cleanly.

## Listening for messages

The bus is bidirectional. Register a handler with `bus.on(event, callback)`
and your function runs every time a matching message arrives.

```python
from ovos_bus_client import MessageBusClient

bus = MessageBusClient()

def on_speak(message):
    print("OVOS just said:", message.data.get("utterance"))

bus.on("speak", on_speak)
bus.run_forever()  # blocks the calling thread
```

`run_forever()` blocks. `run_in_thread()` does not — use the latter when your
program does its own work on the main thread.

## A one-shot send without a long-lived client

If you only want to fire a single message and exit (CLI scripts, cron jobs),
skip the client lifecycle and use `send`:

```python
from ovos_bus_client import send

send("speak", {"utterance": "fire and forget"})
```

`send` opens a transient WebSocket, dispatches the message, and closes
(`ovos_bus_client/send_func.py:8`). It is **not** appropriate for any flow that
needs to receive replies.

## Request and wait for a reply

The most common interactive pattern. Ask the bus a question, block for the
answer.

```python
from ovos_bus_client import MessageBusClient, Message

bus = MessageBusClient()
bus.run_in_thread()
bus.connected_event.wait()

reply = bus.wait_for_response(
    Message("ovos.languages.stt"),
    reply_type="ovos.languages.stt.response",
    timeout=3.0,
)
print(reply.data if reply else "timed out")
```

`wait_for_response` emits your message, then blocks on a one-shot handler for
the reply (`ovos_bus_client/client/client.py:272`). If no reply arrives within
`timeout`, you get `None`.

The default `reply_type` is `<your.msg_type>.response`, which matches the OVOS
convention used by every "query" message type.

## Collect multiple replies

When several handlers might answer one message (`common_query.question` is the
canonical example), use `collect_responses`:

```python
results = bus.collect_responses(
    Message("question:query", {"phrase": "what is six times seven"}),
    min_timeout=0.5,
    max_timeout=3.0,
)
for r in results:
    print(r.data)
```

This implements the OVOS "extend timeout" protocol: handlers ack first, can
extend the wait, and the caller gets every reply
(`ovos_bus_client/client/client.py:199`,
`ovos_bus_client/client/collector.py:20`).

## What to read next

- **Skill authors:** [High-level APIs](apis.md) — `GUIInterface`, `OCPInterface`, `EnclosureAPI`.
- **Backend / pipeline integrators:** [Sessions](session.md) and [Common patterns](patterns.md).
- **Anyone:** [Core concepts](concepts.md) cements the mental model.
