# Testing

`ovos-utils` ships `FakeBus`, a drop-in replacement for `MessageBusClient`
that runs entirely in process with no sockets. It is the recommended way to
test code that talks to the bus.

## Why not a real bus

A real `MessageBusClient` requires a running WebSocket server, runs the
WebSocket loop on a background thread, and is subject to timing-dependent
test flakiness. `FakeBus` eliminates all three: no server, no thread,
synchronous dispatch.

It is good enough for the entire OVOS test suite, so it is good enough for
yours.

## Basic usage

```python
from ovos_utils.fakebus import FakeBus
from ovos_bus_client import Message

bus = FakeBus()

received = []
bus.on("speak", received.append)

bus.emit(Message("speak", {"utterance": "hi"}))

assert len(received) == 1
assert received[0].data["utterance"] == "hi"
```

`FakeBus.emit` dispatches synchronously to every registered handler on the
calling thread. No `connected_event.wait()` needed.

## Capturing every emit

`FakeBus` does not record emits by default. Subclass it if you want a
recording bus:

```python
from ovos_utils.fakebus import FakeBus

class CapturingBus(FakeBus):
    def __init__(self):
        super().__init__()
        self.emitted_msgs = []

    def emit(self, message):
        self.emitted_msgs.append(message)
        super().emit(message)
```

Then assert against `bus.emitted_msgs` after the code under test runs.

## Simulating downstream responses

Code that does request/reply expects something to answer. In tests, register
a fake responder:

```python
from ovos_utils.fakebus import FakeBus
from ovos_bus_client import Message

bus = FakeBus()

def fake_stt(message):
    bus.emit(message.response({"langs": ["en-us", "pt-pt"]}))

bus.on("ovos.languages.stt", fake_stt)

# code under test
reply = bus.wait_for_response(Message("ovos.languages.stt"), timeout=1.0)
assert reply.data["langs"] == ["en-us", "pt-pt"]
```

For asynchronous pipelines (skills that emit speaks after some processing),
spawn the response on a thread with a small delay so the caller's `wait`
actually waits.

## Isolating `SessionManager` between tests

`SessionManager` is a class-level singleton. Tests that touch it leak state
to each other. Reset it explicitly:

```python
import pytest
from ovos_bus_client.session import SessionManager

@pytest.fixture(autouse=True)
def reset_session_manager():
    default = SessionManager.default_session
    SessionManager.sessions = {"default": default}
    yield
    SessionManager.sessions = {"default": default}
```

Drop this fixture in `conftest.py` once and never think about it again.

## Asserting message context

Most bugs hide in `context`, not `data`. Assert against the serialised session
to catch session-stripping regressions:

```python
emitted = bus.emitted_msgs[-1]
assert emitted.context["session"]["session_id"] == "kitchen"
assert emitted.context["session"]["lang"] == "pt-pt"
```

If you have a chain of relays (HiveMind, multi-bus bridges), assert at each
hop that `context["session"]` survives.

## Testing the high-level APIs

`GUIInterface`, `OCPInterface`, etc. accept a `bus` argument. Pass a
`FakeBus`. Then `bus.on(...)` for the message types the API emits and assert
they fire with the right `data`/`context`.

```python
from ovos_utils.fakebus import FakeBus
from ovos_bus_client.apis.gui import GUIInterface

bus = FakeBus()
seen = []
bus.on("gui.value.set", seen.append)

gui = GUIInterface(skill_id="t.skill", bus=bus)
gui["temperature"] = 24

assert seen[0].data["__from"] == "t.skill"
assert seen[0].data["values"]["temperature"] == 24
```

## When you do need a real bus

Some integration tests benefit from a real WebSocket. Spin up `ovos-messagebus`
in a `subprocess.Popen` for the duration of the test module, then connect a
real `MessageBusClient` to it. Tear it down in the module's `teardown`.
That said, 95% of the time `FakeBus` is the right call.
