# Waiters and collectors

Two utilities that turn the fire-and-forget bus into request/response and
multi-reply patterns. Both are wrapped by helper methods on `MessageBusClient`;
you can use those helpers without ever touching the lower-level classes.

## `MessageWaiter` — `ovos_bus_client/client/waiter.py:17`

Block until a message of a given type (or one of several types) arrives.

```python
from ovos_bus_client.client.waiter import MessageWaiter

waiter = MessageWaiter(bus, "ovos.languages.stt.response")
bus.emit(Message("ovos.languages.stt"))
reply = waiter.wait(timeout=3.0)
```

- `MessageWaiter(bus, message_type)` — `message_type` may be a string or a
  list of strings. Construction registers a one-shot handler; the wait happens
  in `wait(timeout)`.
- `wait(timeout)` returns the captured `Message`, or `None` on timeout.

**Important**: instantiate the `MessageWaiter` **before** emitting the message
you expect a reply to, otherwise you can miss a same-tick reply. The
`bus.wait_for_response` helper does this correctly for you
(`ovos_bus_client/client/client.py:272`).

### When to use it directly

Use `MessageBusClient.wait_for_response` or `wait_for_message`. Reach for
`MessageWaiter` only if you need to start waiting some time before you emit,
or wait for one of several reply types without sending a query.

## `MessageCollector` — `ovos_bus_client/client/collector.py:20`

Multi-handler collect-call. Used when several independent handlers may answer
a single query (the canonical case: `question:query` in OVOS common_query,
where every Q&A skill might respond).

The protocol:

1. Caller emits the query with a unique `__collect_id__` in `context`.
2. Each handler that wants to answer first emits an
   `<msg_type>.handling` ack containing its `handler_id`, then computes its
   answer and emits `<msg_type>.response`.
3. The collector waits **at most `max_timeout` seconds total** and **at
   least `min_timeout` seconds after each ack** for further responses.
4. Handlers can extend the wait by emitting `<msg_type>.handling` again with
   a new timeout (this is what `CollectionMessage.extend` does).
5. Handlers can drop out by emitting `<msg_type>.failure`
   (`CollectionMessage.failure`).
6. Caller can short-circuit via `direct_return_func` — return truthy from
   that callable on any incoming response and `collect()` returns immediately
   with just that response.

### Calling side

```python
results = bus.collect_responses(
    Message("question:query", {"phrase": "what is six times seven"}),
    min_timeout=0.5,
    max_timeout=3.0,
    direct_return_func=lambda msg: msg.data.get("conf", 0) >= 0.99,
)
for r in results:
    print(r.data)
```

Wrapper at `ovos_bus_client/client/client.py:199`.

### Handler side

Register your handler with `bus.on_collect` rather than `bus.on`. The wrapper
takes care of emitting the initial `.handling` ack and giving your function a
`CollectionMessage` rather than a plain `Message`.

```python
def handle_question(cmessage):
    answer = solve(cmessage.data["phrase"])
    if answer is None:
        cmessage.failure()
        return
    if needs_more_time(cmessage):
        cmessage.extend(timeout=5)
    bus.emit(cmessage.success({"answer": answer, "conf": 0.95}))

bus.on_collect("question:query", handle_question, timeout=2)
```

Wrapper at `ovos_bus_client/client/client.py:225`.

### What you get back

`collector.collect()` returns a `List[Message]`. Each entry is one
`<msg_type>.response` your handlers emitted. Order is arrival order. An empty
list means no handler answered before `max_timeout`.

### Iterator interface

`MessageCollector` is also iterable. Use this when you want to react to each
response as it arrives rather than waiting for the whole set:

```python
collector = MessageCollector(bus, query, 0.5, 3.0, lambda m: False)
for response in collector:
    process(response)
```

Implemented at `ovos_bus_client/client/collector.py:55-64`.

## Differences at a glance

| Need | Use |
|---|---|
| One specific reply type | `bus.wait_for_response` |
| Block for next message of type X without sending anything | `bus.wait_for_message` |
| Wait for one of several reply types | `MessageWaiter` (list of types) |
| Several handlers can answer; want them all | `bus.collect_responses` |
| Several handlers can answer; want to stream them | `MessageCollector` iterator |
