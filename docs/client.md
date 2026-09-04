# The client

`MessageBusClient` is the WebSocket client. Every OVOS process that talks to
the bus uses one (or several, for things like the GUI bus).

## Construction

```python
from ovos_bus_client import MessageBusClient

bus = MessageBusClient(
    host=None, port=None, route=None, ssl=None,
    emitter=None,
    on_open=None, on_close=None, on_error=None, on_message=None,
)
```

All connection arguments default to `None`. When `None`, they come from
`load_message_bus_config()` — see [Configuration](configuration.md). The
constructor signature lives at `ovos_bus_client/client/client.py:39`.

### Custom emitter

`emitter` is an optional `pyee.EventEmitter` (or compatible). If omitted, a
fresh `EventEmitter` is created. Inject your own when you need to share an
emitter between several bus clients or your own code.

### Callback overrides

`on_open` / `on_close` / `on_error` / `on_message` let you replace the
WebSocket lifecycle callbacks at construction time. Most users do **not** need
this; subclass `MessageBusClient` if you need deep customisation.

## Running the client

You have two ways to start the WebSocket loop.

| Call | Blocks? | When to use |
|---|---|---|
| `bus.run_in_thread()` | No | Programs that do their own work on the main thread (most). |
| `bus.run_forever()` | Yes | Daemons whose only job is to react to bus events. |

`run_in_thread()` spawns a background thread for the WebSocket loop and
returns to the caller immediately. `run_forever()` runs the loop on the
**calling** thread and only returns once the connection ends.

`bus.connected_event` is a `threading.Event` that is set when the WebSocket
handshake completes (`client.py:90`) and cleared on disconnect
(`client.py:370`). Always `wait()` on it before emitting if you might race the
handshake:

```python
bus.run_in_thread()
bus.connected_event.wait()         # block until socket is up
bus.emit(Message("speak", {"utterance": "ready"}))
```

`bus.emit()` already calls `connected_event.wait(10)` internally
(`client.py:181`) so a quick race won't drop messages, but explicit waits give
you a fail-fast point at startup.

## Registering handlers

```python
bus.on(event_name, handler)        # every matching message
bus.once(event_name, handler)      # fire exactly once, then unsubscribe
bus.remove(event_name, handler)    # unregister
bus.remove_all_listeners(event_name)
```

Handlers are plain callables that take a single `Message` argument
(`client.py:300-356`). They run on the bus thread; long blocking work in a
handler blocks the bus loop — spin up a worker thread for anything heavy.

```python
def on_utt(message):
    utt = message.data["utterances"][0]
    print("heard:", utt)

bus.on("recognizer_loop:utterance", on_utt)
```

### One-shot handlers via `once`

`once` is implemented as a wrapper that calls `remove` after the first match
(`client.py:309`). Good for "subscribe to the response, then unsubscribe."

### Catch-all listener

The literal event name `"message"` is special on the underlying emitter: every
message dispatched by `MessageBusClient.on_message` first emits a `"message"`
event with the **raw JSON string**, then emits the parsed event for handlers
that subscribed by `msg_type` (`client.py:143-158`). Subscribe to `"message"`
when you want to log or intercept every single message.

## Emitting

```python
bus.emit(Message("speak", {"utterance": "hi"}))
```

`emit` serialises the message (pure JSON), optionally wraps it in the
deprecated AES envelope (see below), waits up to 10 seconds for the socket to
be up, then `socket.send`s the result (`ovos_bus_client/client/client.py:261`).

There is no `emit`-with-return; if you want a reply, use one of the helpers
below.

## Request / reply

```python
reply = bus.wait_for_response(
    Message("ovos.languages.stt"),
    reply_type="ovos.languages.stt.response",  # optional
    timeout=3.0,
)
```

`wait_for_response` (`client.py:272`):

1. Builds a `MessageWaiter` for the reply type(s).
2. Emits your message.
3. Blocks on the waiter for at most `timeout` seconds.
4. Returns the matching `Message`, or `None` on timeout.

If you do not pass `reply_type`, it defaults to
`<your.msg_type>.response`. If you pass a list, the first matching type wins.

`wait_for_message` (`client.py:257`) is the same primitive without the
"emit first" step: just blocks for the next matching message.

See [Waiters and collectors](waiter_and_collector.md) for `MessageWaiter`
details and the multi-handler `collect_responses` flow.

## Lifecycle

| Call | Effect |
|---|---|
| `bus.close()` | Initiate disconnect; safe to call multiple times (`client.py:365`). |
| Socket dropped by server | `on_close` clears `connected_event`. `run_forever` returns. |
| `on_error` | Closes the client, waits, and reconnects itself (see below). |

`MessageBusClient` auto-reconnects on any socket error (`client.py:202-246`,
`on_error`): it closes the client, sleeps `self.retry` seconds, then calls
`create_client()` again. The retry delay starts at 5 seconds, doubles on each
further failure up to a 60-second cap, and resets to 5 seconds after a
successful reconnect. You do not need to wrap construction in your own retry
loop for this. See the manual's
[Bus Service: reconnect behavior](https://tigregotico.github.io/ovos-technical-manual/bus-service/#bus-restart-reconnect-behavior)
for the full backoff details, including what happens to in-flight calls and
messages sent during an outage.

## `GUIWebsocketClient` — `client.py:380`

A specialised `MessageBusClient` that talks to the **GUI** bus rather than the
core bus. It deals in `GUIMessage` objects rather than `Message`, and the
default port is different (typically 18181). Used by `GUIInterface` —
applications generally do not instantiate it directly.

## Deprecated transport-edge encryption

[OVOS-MSG-1](https://github.com/OpenVoiceOS/architecture/blob/dev/msg-1.md)
is transport-agnostic
([§1 Scope](https://github.com/OpenVoiceOS/architecture/blob/dev/msg-1.md#1-scope)
explicitly excludes encryption from the spec): the message envelope does not
define encryption. `ovos-bus-client` bolts a legacy AES-GCM wrapper on top at
the transport edge, controlled by `websocket.secret_key` in your OVOS config.

**This scheme is deprecated.** Its matching key-setup half was never formally
implemented. A `DeprecationWarning` fires every time it engages. Remove
`websocket.secret_key` from your config to suppress the warning and opt out.

### How it works

Two module-level helpers in `ovos_bus_client/client/client.py` do the work:

| Helper | Direction | Source |
|---|---|---|
| `_maybe_encrypt(serialized: str) -> str` | outbound (post-serialize, pre-send) | `client.py:52` |
| `_maybe_decrypt(raw) -> str` | inbound (post-receive, pre-deserialize) | `client.py:67` |

Both are no-ops when `websocket.secret_key` is absent or empty. When a
non-empty key is present, `_maybe_encrypt` wraps the JSON string in an AES-GCM
envelope and `_maybe_decrypt` unwraps it.

`_maybe_decrypt` also reads `websocket.allow_unencrypted` (defaults to `True`
when no key is set, `False` when a key is set). If `allow_unencrypted` is
`False` and an incoming frame is not encrypted, a `RuntimeError` is raised.

### Where it is wired

| Call path | Hook point |
|---|---|
| `MessageBusClient.emit` | `_maybe_encrypt` called post-`serialize()`, pre-`socket.send` (`client.py:261`) |
| `MessageBusClient.on_message` | `_maybe_decrypt` called post-receive, pre-`Message.deserialize` (`client.py:223`) |
| `GUIWebsocketClient.emit` | same `_maybe_encrypt` hook (`client.py:480,482`) |
| `GUIWebsocketClient.on_message` | same `_maybe_decrypt` hook (`client.py:505`) |

`send_func.py` does **not** apply encryption; deployments that need the scheme
must route traffic through `MessageBusClient`.

### Config keys (deprecated)

| Key | Type | Default | Effect |
|---|---|---|---|
| `websocket.secret_key` | `str` | *(absent)* | AES-GCM key. Empty string or missing both disable encryption. |
| `websocket.allow_unencrypted` | `bool` | `True` when no key; `False` when a key is set | Allow plaintext frames through when a key is configured. |

See [Configuration → Deprecated: `websocket.secret_key` and `websocket.allow_unencrypted`](configuration.md#deprecated-websocketsecret_key-and-websocketallow_unencrypted)
for the same keys in context of the wider config block.

## Subclassing

The standard customisation point is the constructor's callback overrides plus
custom `emit` / `on_message` if you really need it. `GUIWebsocketClient` is a
worked example of a subclass that overrides `emit`, `on_open`, and
`on_message`.

If you find yourself reaching for deeper hooks, ask whether a transport plugin
is what you actually want — that is what `hivemind-ovos-agent-plugin` and the
GUI client are doing structurally.

## Async alternative

`MessageBusClient` is synchronous. For an asyncio-native client with the same
shape, see [`AsyncMessageBusClient`](async_client.md). It's an optional extra:

```bash
pip install ovos-bus-client[async]
```

Use it when your application is already async (FastAPI, aiohttp, Discord bots,
etc.) and you want `await bus.emit(...)`, `await bus.wait_for_response(...)`,
`async for msg in collector` instead of threads and blocking calls.
