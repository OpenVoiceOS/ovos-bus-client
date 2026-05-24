# Migration from 1.x to 2.0

## Breaking changes in 2.0.0

Two modules were removed from `ovos-bus-client`. Their entry points are no longer registered by this package. Install the replacement packages to restore functionality.

---

### ovos_bus_client.hpm — HiveMind agent protocol

**Removed:** `ovos_bus_client/hpm.py` (class `OVOSProtocol`)

**Why:** `ovos-bus-client` is a foundational library used throughout the OVOS stack. Having it import `hivemind-core` and `hivemind-bus-client` inverted the dependency direction.

**Replacement:** [hivemind-ovos-agent-plugin](https://github.com/JarbasHiveMind/hivemind-ovos-agent-plugin)

```bash
pip install hivemind-ovos-agent-plugin
```

The entry-point name `hivemind.agent.protocol` is preserved by the new package. No changes to `hivemind-core` configuration are required.

**Import change:**

```python
# 1.x
from ovos_bus_client.hpm import OVOSProtocol

# 2.0
from hivemind_ovos_agent_plugin import OVOSAgentProtocol  # also exposes OVOSProtocol alias
```

---

### ovos_bus_client.opm — Messagebus solver

**Removed:** `ovos_bus_client/opm.py` (class `OVOSMessagebusSolver`)

**Why:** `OVOSMessagebusSolver` inherited from the deprecated `QuestionSolver` base class and was registered under the deprecated `neon.plugin.solver` entry-point group. It also lacked multi-turn `Session` state.

**Replacement:** [ovos-messagebus-chat-plugin](https://github.com/OpenVoiceOS/ovos-messagebus-chat-plugin)

```bash
pip install ovos-messagebus-chat-plugin
```

The replacement implements the modern `ChatEngine` interface (`opm.agents.chat`) and preserves per-session OVOS `Session` state via `SessionManager` keyed by `session_id`, enabling correct multi-turn conversation.

**Entry-point change:**

| 1.x | 2.0 |
|---|---|
| `neon.plugin.solver` | `opm.agents.chat` |

No old entry point is preserved. Update any persona or pipeline configuration that referenced `ovos-solver-bus-plugin` under the solver group to use the chat-engine group instead.

---

### Encryption: moved from `Message` to the transport edge

[`Message.serialize` and `Message.deserialize`](messages.md#serialisation) produce
and consume pure JSON — they have never encrypted or decrypted the envelope.
The legacy AES-GCM wrapper (controlled by
[`websocket.secret_key`](configuration.md#deprecated-websocketsecret_key-and-websocketallow_unencrypted))
was always a transport-level concern; it is now explicitly placed at the
transport edge inside [`MessageBusClient` and `GUIWebsocketClient`](client.md)
via `_maybe_encrypt` / `_maybe_decrypt`
(`ovos_bus_client/client/client.py:52,67`). See
[The client → Deprecated transport-edge encryption](client.md#deprecated-transport-edge-encryption)
for the full hook layout.

The scheme is deprecated. If your deployment set `websocket.secret_key`, you
will see `DeprecationWarning` on every encrypted send or receive. Remove the
key to suppress the warning. For remote-access security, use HiveMind instead.
