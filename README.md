# OpenVoiceOS Bus Client

This module is a simple interface for the OVOS messagebus and can be used to connect to OVOS, send messages and react to messages sent by the OpenVoiceOS system.

---

## 🤖 Persona Integration

The project also includes the `ovos-solver-bus-plugin`, a plugin for interacting with `ovos-core` wherever a solver can be used.

example `persona.json`
```json
{
  "name": "Open Voice OS",
  "solvers": [
    "ovos-solver-bus-plugin",
    "ovos-solver-failure-plugin"
  ],
  "ovos-solver-bus-plugin": {
    "autoconnect": true,
    "host": "192.168.1.200",
    "port": 8181
  }
}
```

### Use Cases:
- Expose `ovos-core` to any OpenAI-compatible project via [ovos-persona-server](https://github.com/OpenVoiceOS/ovos-persona-server)
    - Chat with OVOS via the Open Web UI
    - Expose OVOS as an agent for Home Assistant
- Integrate OpenVoiceOS into a [Mixture Of Solvers (MOS)](https://github.com/TigreGotico/ovos-MoS)

---

## 📡 HiveMind Integration

This project includes native integration with [HiveMind Plugin Manager](https://github.com/JarbasHiveMind/hivemind-plugin-manager), enabling seamless interoperability within the HiveMind ecosystem.

`hivemind-ovos-agent-plugin` is a hivemind **Agent Protocol** responsible for handling HiveMessages and translating them to the OpenVoiceOS messagebus

---

## 🐍 Python Usage

### MycroftBusClient()

The `MycroftBusClient()` object can be setup to connect to any host and port as well as any endpont on that host. this makes it quite versitile and will work on the main bus as well as on a gui bus. If no arguments are provided it will try to connect to a local instance of OVOS on the default endpoint and port.

> NOTE: we kept the original pre-fork class name for compatibility reasons

### Message()

The `Message` object is a representation of the messagebus message, this will always contain a message type but can also contain data and context. Data is usually real information while the context typically contain information on where the message originated or who the intended recipient is.

```python
Message('MESSAGE_TYPE', data={'meaning': 42}, context={'origin': 'A.Dent'})
```

### Examples

Below are some a couple of simple cases for sending a message on the bus as well as reacting to messages on the bus

#### Sending a message on the bus.

```python
from ovos_bus_client import MessageBusClient, Message

print('Setting up client to connect to a local OVOS instance')
client = MessageBusClient()
client.run_in_thread()

print('Sending speak message...')
client.emit(Message('speak', data={'utterance': 'Hello World'}))
```

#### Catching a message on the messagebus

```python
from ovos_bus_client import MessageBusClient, Message

print('Setting up client to connect to a local OVOS instance')
client = MessageBusClient()

def print_utterance(message):
    print('OVOS said "{}"'.format(message.data.get('utterance')))


print('Registering handler for speak message...')
client.on('speak', print_utterance)

client.run_forever()
```
