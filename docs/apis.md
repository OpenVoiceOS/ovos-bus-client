# High-level APIs

`ovos-bus-client` ships several typed wrappers under `ovos_bus_client.apis/`.
Each wraps a chunk of the OVOS message vocabulary in a class with explicit
methods, so skill and plugin code can call `gui["foo"] = "bar"` instead of
hand-rolling messages.

These are the right level for **skill authors** and most application code.

## `GUIInterface` — `ovos_bus_client/apis/gui.py:68`

The skill-facing GUI API. Show pages, set variables, register event handlers,
and toggle widgets without touching `qt5` or the GUI bus directly.

### Construction

```python
from ovos_bus_client.apis.gui import GUIInterface

gui = GUIInterface(skill_id="weather.openvoiceos", bus=bus)
```

A skill base class instantiates one for you on `self.gui`. Outside a skill,
construct one explicitly with your `skill_id` and a `MessageBusClient`.

### Common operations

| Operation | Method | Source |
|---|---|---|
| Show a page | `gui.show_page(name, override_idle=None, override_animations=False)` | `gui.py:~300` |
| Set a variable | `gui["temp"] = 24` (`__setitem__`) | `gui.py:258` |
| Read a variable | `gui["temp"]` (`__getitem__`) | `gui.py:274` |
| Register an event from the QML side | `gui.register_handler(event, fn)` | `gui.py:207` |
| Build the event message type | `gui.build_message_type("clicked")` | `gui.py:190` |
| Detect a connected GUI | `gui.connected` | `gui.py:174` |
| Show a widget | `gui.widgets.show_widget(type, data)` | `gui.py:38` |

`gui["key"] = value` automatically pushes the change to all connected GUI
clients; you don't call a "sync" function (`gui.py:249`).

### Lifecycle

`set_bus(bus)` rebinds the interface to a different bus, e.g. when a skill is
reloaded (`gui.py:134`). `setup_default_handlers()` registers the standard
event names the OVOS skill base class expects (`gui.py:199`).

## `OCPInterface` — `ovos_bus_client/apis/ocp.py:303`

The OCP (OVOS Common Playback) media-player control API. Queue tracks, play,
pause, seek, fetch track info — without learning the OCP message vocabulary.

```python
from ovos_bus_client.apis.ocp import OCPInterface

ocp = OCPInterface(bus=bus)
ocp.queue([{"uri": "https://example.com/track.mp3", "title": "demo"}])
ocp.play()
```

Methods (all on `OCPInterface`):

| Method | Effect |
|---|---|
| `queue(tracks, source_message=None)` | Append tracks to the queue (`ocp.py:343`). |
| `populate_search_results(tracks, ...)` | Push search results into the OCP UI (`ocp.py:355`). |
| `play(tracks, utterance=None, source_message=None)` | Start playback (`ocp.py:371`). |
| `stop()`, `next()`, `prev()`, `pause()`, `resume()` | Standard transport. |

### Classic audio service interface

`ClassicAudioServiceInterface` (`ocp.py:79`) is the equivalent for the
pre-OCP audio service. New code should use `OCPInterface`. The classic
interface remains for components still using `mycroft.audio.service.*`
messages.

The `@message_injector` decorator on `ClassicAudioServiceInterface`
(`ocp.py:58`) auto-fills `source_message` from `dig_for_message()` when the
caller does not pass one — handy inside skills.

## `EnclosureAPI` — `ovos_bus_client/apis/enclosure.py:4`

Controls Mark 1 / Mark 2 hardware enclosures (eyes, mouth, system LED). If
you are not on those devices, none of these calls do anything; emitting them
is still harmless.

```python
from ovos_bus_client.apis.enclosure import EnclosureAPI

enc = EnclosureAPI(bus=bus, skill_id="my.skill")
enc.eyes_color(r=0, g=128, b=255)
enc.mouth_text("hello")
enc.system_blink(3)
```

The class is a thin emitter — every method maps one-to-one to a Mycroft
enclosure message type. Read the source for the full method list; it is more
exhaustive than this doc would be useful at.

## `EventSchedulerInterface` — `ovos_bus_client/apis/events.py:12`

Schedule one-shot and repeating events through the OVOS event scheduler.

```python
from ovos_bus_client.apis.events import EventSchedulerInterface
from datetime import datetime, timedelta

es = EventSchedulerInterface(bus=bus, skill_id="my.skill")

es.schedule_event(
    handler=my_callback,
    when=datetime.now() + timedelta(minutes=5),
    data={"reason": "wake up"},
    name="wakeup_timer",
)

es.schedule_repeating_event(
    handler=heartbeat,
    when=datetime.now(),
    frequency=60,           # seconds
    name="heartbeat",
)
```

Cancel with `cancel_scheduled_event(name)`. Inspect with
`get_scheduled_event_status(name)` (`events.py:185`). Tear everything down
with `shutdown()` (`events.py:224`).

Names are namespaced by `skill_id` internally (`events.py:44`) so two skills
can use the same logical name without colliding.

## Pattern: passing `source_message`

Most `apis/` methods accept an optional `source_message` argument. When
present, it is used to derive `context` for the outgoing message — most
importantly the `session`, so the resulting media playback / GUI update is
attached to the right user/device.

Always pass `source_message` when you can — typically the `message` argument
your handler received (or the `message` argument from an intent function).
When no `message` is in scope, call `dig_for_message()`
(`ovos_bus_client/message.py:258`) to walk back through the call stack and
find one. Skipping `source_message` means the outgoing message has no
session, which can cause OCP and the GUI to act on the wrong device in a
multi-user deployment.
