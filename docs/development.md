# Development

## Repository layout

```
ovos_bus_client/
  __init__.py          # public re-exports: MessageBusClient, Message, Session, SessionManager, send
  client/
    client.py          # MessageBusClient, GUIWebsocketClient
    collector.py       # MessageCollector utility
    waiter.py          # MessageWaiter utility
    events.py          # event emitter internals
  message.py           # Message, GUIMessage, dig_for_message
  session.py           # Session, SessionManager, IntentContextManager, UtteranceState
  send_func.py         # send() one-shot helper
  conf.py              # load_message_bus_config, client_from_config
  scripts.py           # CLI entry points (ovos-speak, ovos-listen, …)
  apis/
    gui.py             # GUIInterface — skill GUI API
    ocp.py             # OCPInterface, OCPQuery, audio/video/web service interfaces
    enclosure.py       # EnclosureAPI
```

## Running tests

```bash
pip install -e ".[test]"
pytest
```

## Entry points (2.0)

Only CLI scripts are registered:

| Entry point | Function |
|---|---|
| `ovos-listen` | `ovos_bus_client.scripts:ovos_listen` |
| `ovos-speak` | `ovos_bus_client.scripts:ovos_speak` |
| `ovos-say-to` | `ovos_bus_client.scripts:ovos_say_to` |
| `ovos-simple-cli` | `ovos_bus_client.scripts:simple_cli` |

The `hivemind.agent.protocol` and `neon.plugin.solver` groups are no longer registered here. See [migration.md](migration.md).

## Versioning

Version is defined in `ovos_bus_client/version.py` and read by `setup.py`. The project follows `MAJOR.MINOR.BUILD[aN]` with `VERSION_ALPHA=0` meaning a stable release.
