# ovos-bus-client

Python client library for the OVOS messagebus.

## Overview

`ovos-bus-client` provides the foundational types and connection management that OVOS components use to communicate over the WebSocket messagebus. It is a dependency of `ovos-core`, skills, plugins, and external integrations. It deliberately has no dependency on any HiveMind or solver framework.

## Key Classes

| Class | Purpose | Source |
|---|---|---|
| `MessageBusClient` | WebSocket client; `emit`, `on`, `run_forever` | `ovos_bus_client/client/client.py:28` |
| `GUIWebsocketClient` | GUI-specific bus client | `ovos_bus_client/client/client.py:380` |
| `Message` | Typed bus message (`msg_type`, `data`, `context`) | `ovos_bus_client/message.py` |
| `Session` | Per-session state (lang, pipeline, context, flags) | `ovos_bus_client/session.py:263` |
| `SessionManager` | In-process registry; `get()` / `update()` / `from_message()` | `ovos_bus_client/session.py:568` |
| `GUIInterface` | High-level GUI page/variable API for skills | `ovos_bus_client/apis/gui.py:68` |
| `OCPInterface` | OCP media player control API | `ovos_bus_client/apis/ocp.py:303` |

## Contents

- [Installation](../README.md#install)
- [Session / SessionManager](session.md)
- [Migration from 1.x](migration.md)
- [Development](development.md)
