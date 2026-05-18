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
