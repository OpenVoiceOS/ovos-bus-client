# OpenVoiceOS Bus Client

A Python client for the OVOS messagebus. Connect to OVOS, emit messages, and react to system events.

## Install

```bash
pip install ovos-bus-client
```

## Quick Start

```python
from ovos_bus_client import MessageBusClient, Message

client = MessageBusClient()
client.run_in_thread()

client.emit(Message('speak', data={'utterance': 'Hello World'}))
```

Listening for messages:

```python
from ovos_bus_client import MessageBusClient, Message

client = MessageBusClient()

def on_speak(message):
    print('OVOS said:', message.data.get('utterance'))

client.on('speak', on_speak)
client.run_forever()
```

## CLI Tools

Installed alongside the package:

| Command | Description |
|---|---|
| `ovos-speak <text>` | Ask OVOS to speak a phrase |
| `ovos-say-to <text>` | Send a utterance to the intent pipeline |
| `ovos-listen` | Trigger the wake-word / listen cycle |
| `ovos-simple-cli` | Interactive text CLI for OVOS |

## Configuration

`MessageBusClient` reads `~/.config/mycroft/mycroft.conf` by default. Override at construction:

```python
MessageBusClient(host='192.168.1.200', port=8181)
```

## Migrating from 1.x

Two modules were removed in 2.0.0. Install their replacement packages if you used them:

| Removed | Replacement | Install |
|---|---|---|
| `ovos_bus_client.hpm.OVOSProtocol` | `hivemind-ovos-agent-plugin` | `pip install hivemind-ovos-agent-plugin` |
| `ovos_bus_client.opm.OVOSMessagebusSolver` | `ovos-messagebus-chat-plugin` | `pip install ovos-messagebus-chat-plugin` |

The HiveMind agent entry point (`hivemind.agent.protocol`) and the solver entry point (`neon.plugin.solver`) are no longer registered by this package. See [docs/migration.md](docs/migration.md) for details.

## Documentation

- [Architecture](docs/index.md)
- [Session / SessionManager](docs/session.md)
- [Migration from 1.x](docs/migration.md)

## License

Apache 2.0
