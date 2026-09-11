# AsyncMessageBusClient

**Module:** `ovos_bus_client.client.async_client`

`AsyncMessageBusClient` is an asyncio-native WebSocket client that provides the same interface as `MessageBusClient` but with `async/await` instead of blocking threads. It uses the [`websockets`](https://websockets.readthedocs.io/) library under the hood.

> **Optional dependency** — `websockets` is not installed by default.
> Install it with: `pip install "ovos-bus-client[async]"`
>
> If `websockets` is not installed the rest of the package works normally;
> `AsyncMessageBusClient` is `None` in that case and calling `connect()`
> raises `ImportError` with a clear message.

---

## When to use the async client

| Situation | Recommended client |
|---|---|
| Stand-alone script, daemon, OVOS plugin | `MessageBusClient` (sync, thread-based) |
| Inside an existing asyncio application | `AsyncMessageBusClient` |
| Emitting many messages concurrently (fan-out) | `AsyncMessageBusClient` |
| `await`-ing multiple bus replies in parallel | `AsyncMessageBusClient` |

The async client is a **complement**, not a replacement. Both classes share the same `Message`, `Session`, and event-registration API.

---

## Installation

```bash
# async support
pip install "ovos-bus-client[async]"

# or add to pyproject.toml
dependencies = ["ovos-bus-client[async]"]
```

---

## Quick Start

```python
import asyncio
from ovos_bus_client import AsyncMessageBusClient
from ovos_bus_client.message import Message

async def main():
    bus = AsyncMessageBusClient()
    await bus.connect()

    # Listen for events
    bus.on("speak", lambda msg: print("OVOS said:", msg.data.get("utterance")))

    # Emit a message
    await bus.emit(Message("speak", {"utterance": "Hello world"}))

    # Request / response
    response = await bus.wait_for_response(
        Message("intent.service.intent.get", {"utterance": "what time is it"}),
        reply_type="intent.service.intent.reply",
        timeout=5.0,
    )
    if response:
        print(response.data)

    await bus.close()

asyncio.run(main())
```

---

## Connection Lifecycle

```python
bus = AsyncMessageBusClient(
    host="127.0.0.1",   # default from mycroft.conf
    port=8181,
    route="/core",
    ssl=False,
    cache=False,        # cache config object across instances
)

# Connect (starts internal receive task)
await bus.connect()

# Connect with retry disabled
await bus.connect(retry=False)

# Close
await bus.close()
```

`connect()` starts an internal `asyncio.Task` that reads from the WebSocket indefinitely. There is no `run_forever()` or `run_in_thread()` — the receive loop runs as a cooperative coroutine inside your existing event loop.

On connection, `ovos.session.sync` is automatically emitted to synchronise the default session state with `ovos-core`.

### Reconnection

The current implementation does **not** auto-reconnect on drop (unlike the sync client). Reconnect by catching `ConnectionClosedError` in your application and calling `await bus.connect()` again, or wrap the lifecycle in a retry loop.

---

## Emitting Messages

```python
await bus.emit(Message("speak", {"utterance": "Hello"}))
```

The session is automatically injected into `message.context["session"]` if not already present — identical behaviour to `MessageBusClient.emit`.

`emit` raises `ValueError` (or `asyncio.TimeoutError`) if called before `connect()` is awaited.

---

## Listening

Handler registration is **synchronous** — same API as `MessageBusClient`:

```python
# Persistent listener
bus.on("my.event", handler)

# One-shot listener
bus.once("my.event", handler)

# Remove a specific listener
bus.remove("my.event", handler)

# Remove all listeners for an event
bus.remove_all_listeners("my.event")
```

Handlers may be plain functions or `async` coroutines — `AsyncIOEventEmitter` from `pyee` handles both.

---

## Waiting for a Response

### `wait_for_response`

Send a message and await a matching reply.

```python
response = await bus.wait_for_response(
    Message("my.query"),
    reply_type="my.query.response",   # default: msg_type + ".response"
    timeout=3.0,
)
if response:
    print(response.data)
```

`reply_type` accepts `str` or `List[str]`.

### `wait_for_message`

Await a message of a given type without sending anything first.

```python
msg = await bus.wait_for_message("mycroft.skills.initialized", timeout=30)
```

---

## Collecting Responses from Multiple Handlers

Used when multiple services may respond to a single request (CommonQuery, OCP search, etc.).

### Sender side

```python
responses = await bus.collect_responses(
    Message("question:query", {"phrase": "capital of France"}),
    min_timeout=0.2,    # wait at least this long after emitting
    max_timeout=3.0,    # give up after this long
    direct_return_func=lambda msg: msg.data.get("conf", 0) > 0.9,
)
for r in responses:
    print(r.data)
```

### Handler side

Handler registration uses the same `on_collect` pattern as the sync client (register via `MessageBusClient.on_collect` — the async client does not add a separate `on_collect`; use `bus.on(event + ".handling", ...)` and `bus.on(event + ".response", ...)` directly if needed inside async code).

---

## Fan-out: Concurrent Emits

A key advantage of the async client is the ability to fire many messages concurrently without spawning threads:

```python
import asyncio
from ovos_bus_client import AsyncMessageBusClient
from ovos_bus_client.message import Message

async def main():
    bus = AsyncMessageBusClient()
    await bus.connect()

    messages = [Message(f"plugin.query.{i}", {"i": i}) for i in range(100)]
    await asyncio.gather(*[bus.emit(m) for m in messages])

    await bus.close()
```

---

## Connection Lifecycle Events

These events are emitted on the local `AsyncIOEventEmitter` (not sent over the bus):

| Event | When |
|---|---|
| `open` | WebSocket connection established |
| `close` | WebSocket connection closed (normally or on error) |
| `message` | Raw message string received |

```python
bus.on("open", lambda: print("connected"))
bus.on("close", lambda: print("disconnected"))
```

---

## API Reference

### `AsyncMessageBusClient`

**`ovos_bus_client.client.async_client.AsyncMessageBusClient`** — `client.py:AsyncMessageBusClient`

| Method / attribute | Signature | Description |
|---|---|---|
| `__init__` | `(host, port, route, ssl, cache, session)` | Constructor; reads config from `mycroft.conf` |
| `url` | property → `str` | WebSocket URL |
| `connect` | `async (retry=True, retry_delay=5.0)` | Open connection, start receive loop |
| `close` | `async ()` | Close connection and cancel receive task |
| `emit` | `async (message: Message)` | Send message; injects session if absent |
| `wait_for_message` | `async (message_type, timeout=3.0) → Message \| None` | Await a message type |
| `wait_for_response` | `async (message, reply_type=None, timeout=3.0) → Message \| None` | Emit then await reply |
| `collect_responses` | `async (message, min_timeout=0.2, max_timeout=3.0, direct_return_func=None) → List[Message]` | Collect multi-handler responses |
| `on` | `(event_name, func)` | Register persistent handler |
| `once` | `(event_name, func)` | Register one-shot handler |
| `remove` | `(event_name, func)` | Remove handler |
| `remove_all_listeners` | `(event_name)` | Remove all handlers for event |
| `build_url` | `static (host, port, route, ssl) → str` | Build ws:// or wss:// URL |

### `AsyncMessageWaiter`

**`ovos_bus_client.client.async_client.AsyncMessageWaiter`**

Encapsulates the wait-for-message logic. Usually accessed via `bus.wait_for_message` / `bus.wait_for_response`.

```python
from ovos_bus_client.client.async_client import AsyncMessageWaiter

waiter = AsyncMessageWaiter(bus, ["type.a", "type.b"])
# ... emit something ...
msg = await waiter.wait(timeout=5.0)
```

### `AsyncMessageCollector`

**`ovos_bus_client.client.async_client.AsyncMessageCollector`**

Encapsulates the collect-responses logic. Usually accessed via `bus.collect_responses`.

```python
from ovos_bus_client.client.async_client import AsyncMessageCollector

collector = AsyncMessageCollector(bus, message, min_timeout=0.2, max_timeout=3.0)
responses = await collector.collect()
```

---

## Benchmarks

Two benchmark scripts ship under `benchmarks/`. They measure different things,
and both numbers matter.

### In-process — library overhead only

`benchmarks/bench_async_vs_sync.py` stubs out the websocket transport with
mocks. What it measures: coroutine dispatch, emitter routing, message
serialization, waiter/collector coordination. What it does **not** measure:
sockets, JSON over the wire, scheduler context switches between the bus
loop and your code.

Use it to spot library-level overhead regressions, not to predict real
runtime.

Python 3.11, n=2 000:

| Benchmark | Sync | Async | Notes |
|---|---|---|---|
| `emit()` — mean | 0.017 ms | 0.042 ms | async carries coroutine-dispatch overhead |
| `wait_for_message()` — mean | 0.023 ms | 0.018 ms | async is faster (no `threading.Event` wakeup) |
| `Message` serialize+deserialize | 0.005 ms | 0.005 ms | shared baseline |
| Concurrent emit ×200 fan-out | n/a | 7 ms / round (≈28 000 msg/s) | only async can do this in one process |

### Real transport — loopback WebSocket echo server

`benchmarks/bench_async_vs_sync_ws.py` spins up a real
`websockets`-based echo server on a free localhost port and exercises both
clients end-to-end. This is "real transport, no network" — add latency
yourself if you care about remote scenarios.

Python 3.11, n=500:

| Benchmark | Sync | Async | Notes |
|---|---|---|---|
| `emit()` — mean | 0.35 ms | 0.26 ms | async is **25% faster** end-to-end |
| `emit()` — p95 | 0.98 ms | 0.38 ms | async tail latency is much tighter |
| Round-trip via `wait_for_response()` — mean | 0.45 ms | 0.34 ms | async is **24% faster** |
| Round-trip — p95 | 0.61 ms | 0.42 ms | tighter tail again |
| Concurrent `wait_for_response()` ×500 fan-out | n/a | 113 ms total (≈4 400 req/s) | async wins biggest here |

### Key takeaways

- **Per-call latency** is comparable between the two clients, with async
  slightly faster on real transport because it avoids the `threading.Event`
  wakeup path on every reply.
- **Tail latency** is significantly tighter for async — the GIL contention
  that occasionally spikes sync max latency is gone.
- **Concurrent workloads are where async pulls ahead.** 500 concurrent
  round-trips in 113 ms is something the sync client structurally cannot
  match without spawning a thread per query.
- **For one-shot scripts**, the sync client is fine and slightly simpler.
  The async client is the right pick for FastAPI / aiohttp / Discord-bot
  style applications and for anything doing many parallel queries.

### Run the benchmarks yourself

```bash
# Library overhead only — fast, no transport
python benchmarks/bench_async_vs_sync.py --n 2000 --concurrency 200

# End-to-end via a real WebSocket echo server on localhost
python benchmarks/bench_async_vs_sync_ws.py --n 500
```

---

## Differences from `MessageBusClient`

| Feature | `MessageBusClient` | `AsyncMessageBusClient` |
|---|---|---|
| WebSocket library | `websocket-client` | `websockets` |
| I/O model | blocking threads | asyncio coroutines |
| `emit()` | sync | `async` |
| `wait_for_response()` | sync (blocks) | `async` |
| `run_forever()` | required | not needed |
| `run_in_thread()` | available | not needed |
| Auto-reconnect | yes (exponential back-off) | not built-in |
| GUI bus subclass | `GUIWebsocketClient` | not yet provided |
| Event emitter | `ExecutorEventEmitter` | `AsyncIOEventEmitter` |
| Extra dependency | none | `websockets>=10` |
