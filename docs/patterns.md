# Common patterns

Recipes for the situations you will hit most often. Each pattern includes the
"why" so you can adapt rather than copy.

## Request / reply

You want a single answer to a single question.

```python
reply = bus.wait_for_response(
    Message("ovos.languages.stt"),
    timeout=3.0,
)
if reply is None:
    raise TimeoutError("no STT service answered")
print(reply.data)
```

Why: avoids hand-managing a one-shot subscription and the inevitable race
where the reply arrives between your `emit` and your `bus.on` call.

Caveat: the convention is that the reply type is `<your.msg_type>.response`.
If the answering component uses a different name, pass `reply_type=`
explicitly.

## Collect from many handlers

Several handlers may answer. You want all of them.

```python
results = bus.collect_responses(
    Message("question:query", {"phrase": "who is socrates"}),
    min_timeout=0.5,
    max_timeout=3.0,
)
ranked = sorted(results, key=lambda m: m.data.get("conf", 0), reverse=True)
print(ranked[0].data["answer"] if ranked else "no answer")
```

Why: implements the OVOS extend-timeout protocol so slow handlers can ask for
more time without forcing you to set a long blanket timeout.

If your selection criterion lets you bail out as soon as one good answer
arrives, pass `direct_return_func=` and stop the whole wait early.

## Broadcast and forget

You want every interested handler to react; you do not care about replies.

```python
bus.emit(Message("my.skill.config_changed", {"key": "lang", "value": "pt-pt"}))
```

Why: the bus is pub/sub by default. Plain `emit` is exactly the right
primitive. Don't reach for `wait_for_response` "just in case" — you would be
adding a timeout that has nothing to wait for.

## Always preserve context — use `reply`/`forward`

You are inside a handler and want to emit a follow-up message.

```python
def on_query(message):
    answer = compute(message.data["q"])
    bus.emit(message.response({"answer": answer}))
```

Why: `message.response` (and `reply` / `forward`) copies `context`,
preserving the session, source, destination, and any custom routing
metadata. Hand-rolling a fresh `Message(...)` strips that, and silently
breaks multi-user / multi-device deployments.

Rule of thumb: if you are reacting to a message, you should be calling one of
`message.reply`, `message.forward`, `message.response`, or `message.publish`.

## Scope by session

You have a multi-user deployment (HiveMind, chat agents, multiple sites). You
need every emitted message to belong to one user's session.

```python
from ovos_bus_client.session import SessionManager

def handle_external_request(external_session_id, utterance):
    sess = SessionManager.sessions.get(external_session_id) \
        or Session(session_id=external_session_id)
    SessionManager.update(sess)

    bus.emit(Message(
        "recognizer_loop:utterance",
        {"utterances": [utterance], "lang": sess.lang},
        {"session": sess.serialize()},
    ))
```

Why: `SessionManager.sessions` is the single store. Looking up by key (rather
than minting a fresh `Session(uuid4())`) keeps multi-turn skill state coherent
across calls. See [Sessions](session.md) for the full picture.

## Wait for a side-effect

You emit an action and want to confirm a downstream event fired.

```python
waiter = MessageWaiter(bus, "mycroft.audio.service.playing")
bus.emit(Message("mycroft.audio.service.play", {"tracks": ["http://..."]}))
played = waiter.wait(timeout=5.0)
```

Why: construct the waiter **before** emitting so you don't miss a fast
response. `bus.wait_for_response` does this for you when the reply type
follows the `.response` convention; for arbitrary downstream events, build
the waiter explicitly.

## Reconnect on disconnect

`MessageBusClient` does not auto-reconnect. For a long-lived daemon:

```python
import time
from ovos_bus_client import MessageBusClient

while True:
    bus = MessageBusClient()
    try:
        bus.run_forever()    # blocks until the socket dies
    except Exception:
        pass
    time.sleep(2)
```

Why: keeping it explicit in your code beats hiding it inside the client — you
control the backoff, you can log, you can decide to give up. For production,
prefer a process supervisor (systemd, Docker restart policies).

## Long-running work in a handler

Handlers run on the bus thread. Anything you do that blocks blocks the bus.

```python
import threading

def on_query(message):
    threading.Thread(target=_handle, args=(message,), daemon=True).start()

def _handle(message):
    result = slow_io()
    bus.emit(message.response({"result": result}))
```

Why: short-circuits the bus loop so the next message can be dispatched while
your handler is still computing. If you spawn lots of these, use a thread pool
(`concurrent.futures.ThreadPoolExecutor`) so you don't unbounded-spawn under
load.

## Catch every message for debugging

```python
def log_everything(raw_json):
    print("BUS:", raw_json)

bus.on("message", log_everything)
```

Why: the literal event name `"message"` receives the raw JSON before the
parsed-type dispatch (`ovos_bus_client/client/client.py:143`). Subscribe here
when you want to log or audit traffic without listing every type by hand.

## Testing without a real bus

Use `ovos_utils.fakebus.FakeBus`. Same API surface as `MessageBusClient`,
purely in-memory, no sockets. See [Testing](testing.md).
