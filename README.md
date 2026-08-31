# OpenVoiceOS Bus Client

A Python client for the OVOS messagebus. Connect to OVOS, emit messages, and react to system events.

The OVOS messagebus is the **nervous system** of an OVOS install. Every component — STT, intent parsing, skills, TTS, audio, GUI — talks over it. This package is the Python client.

> ⚠️ **The bus is private.** It has **no authentication** — every connected client can issue any natural-language command, speak through the speakers, take over any subsystem, and read every other client's traffic. **Keep it bound to `127.0.0.1`** (the default), never expose it on a network interface, and never put it behind a reverse proxy. For remote access, use [HiveMind](https://github.com/JarbasHiveMind), which adds encryption, identity, and policy enforcement on top.

## Install

```bash
pip install ovos-bus-client
```

## Quick start

```python
from ovos_bus_client import MessageBusClient, Message

client = MessageBusClient()
client.run_in_thread()
client.connected_event.wait()

client.emit(Message('speak', data={'utterance': 'Hello World'}))
```

Listening for messages:

```python
from ovos_bus_client import MessageBusClient

client = MessageBusClient()

def on_speak(message):
    print('OVOS said:', message.data.get('utterance'))

client.on('speak', on_speak)
client.run_forever()
```

### Async alternative

For asyncio-native code, install the optional `[async]` extra and use
`AsyncMessageBusClient`:

```bash
pip install ovos-bus-client[async]
```

```python
import asyncio
from ovos_bus_client import Message
from ovos_bus_client.client import AsyncMessageBusClient

async def main():
    bus = AsyncMessageBusClient()
    await bus.connect()
    bus.on("speak", lambda m: print("OVOS said:", m.data.get("utterance")))

    await bus.emit(Message("speak", {"utterance": "hello from asyncio"}))
    reply = await bus.wait_for_response(
        Message("ovos.languages.stt"), timeout=3.0,
    )
    await bus.close()

asyncio.run(main())
```

Both clients share the same `Message`, `Session`, and event-emitter shape;
pick whichever matches the rest of your application. See
[`docs/async_client.md`](docs/async_client.md) for the full async reference.

## CLI tools

| Command | Description |
|---|---|
| `ovos-speak <text> [lang]` | Ask OVOS to speak a phrase |
| `ovos-say-to <text> [lang]` | Inject an utterance into the intent pipeline |
| `ovos-listen` | Trigger the wake-word / listen cycle |
| `ovos-simple-cli [lang]` | Interactive text REPL for OVOS |

## Configuration

`MessageBusClient` reads the `websocket` block of your OVOS config (loaded by `ovos-config`) — defaults to `ws://127.0.0.1:8181/core`. Override at construction:

```python
MessageBusClient(host='127.0.0.1', port=8181)
```

Do **not** change `host` to anything routable. See the security callout above.

## Migrating from 1.x

Two modules were removed in 2.0.0. Install their replacement packages if you used them:

| Removed | Replacement | Install |
|---|---|---|
| `ovos_bus_client.hpm.OVOSProtocol` | `hivemind-ovos-agent-plugin` | `pip install hivemind-ovos-agent-plugin` |
| `ovos_bus_client.opm.OVOSMessagebusSolver` | `ovos-messagebus-chat-plugin` | `pip install ovos-messagebus-chat-plugin` |

The HiveMind agent entry point (`hivemind.agent.protocol`) and the solver entry point (`neon.plugin.solver`) are no longer registered by this package. See [docs/migration.md](docs/migration.md) for details.

## Documentation

Full developer docs live in [`docs/`](docs/):

- [Getting started](docs/getting_started.md) — install to first message
- [Core concepts](docs/concepts.md) — bus model and the security boundary
- [Messages](docs/messages.md) — `Message`, `GUIMessage`, reply helpers
- [The client](docs/client.md) — `MessageBusClient` API in depth
- [The async client](docs/async_client.md) — `AsyncMessageBusClient` (`pip install ovos-bus-client[async]`)
- [Configuration](docs/configuration.md) — host, port, route, ssl
- [Sessions](docs/session.md) — `Session`, `SessionManager`, `IntentContextManager`
- [Waiters and collectors](docs/waiter_and_collector.md) — request/response and multi-reply patterns
- [High-level APIs](docs/apis.md) — `GUIInterface`, `OCPInterface`, `EnclosureAPI`, scheduler
- [CLI tools](docs/scripts.md) — `ovos-speak`, `ovos-listen`, `ovos-say-to`, `ovos-simple-cli`
- [Common patterns](docs/patterns.md) — recipes for everyday use
- [Testing](docs/testing.md) — `FakeBus`, isolating tests
- [Migration from 1.x](docs/migration.md) — what moved out in 2.0
- [Prerelease quirks](docs/prerelease-quirks.md) — what changed since the last stable release
- [Development](docs/development.md) — repo layout, releases
- [Glossary](docs/glossary.md) — terms

## Related

- [**ovos-pydantic-models**](https://github.com/OpenVoiceOS/ovos-pydantic-models) — authoritative Pydantic v2 index of every OVOS bus message type. Opt-in validation layer for typing, docs generation, and integration tests. Browsable docs: <https://openvoiceos.github.io/ovos-pydantic-models/>.
- [**HiveMind**](https://github.com/JarbasHiveMind) — external-access layer in front of the bus; the right tool for remote clients.

## License

Apache 2.0. See [LICENSE.md](LICENSE.md).
