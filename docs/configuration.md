# Configuration

`ovos-bus-client` reads its connection settings from the standard OVOS
configuration via `ovos-config`. You rarely set anything explicitly.

## Security: do not change `host` away from localhost

The default `host` is `127.0.0.1` on purpose. The bus has no auth, no
authorisation, and no scopes; any client that can open the socket can issue
any natural-language command or take over any subsystem. Bind to localhost
only, and treat the bus as private to the device.

For remote access, run [HiveMind](https://github.com/JarbasHiveMind) in front
of the bus — it terminates external connections, authenticates by `api_key`,
and enforces ACLs / policies before any message reaches the bus.

See [Core concepts → The bus is OVOS's nervous system](concepts.md#the-bus-is-ovoss-nervous-system--and-it-is-private).

## Default config block

```json
{
  "websocket": {
    "host": "127.0.0.1",
    "port": 8181,
    "route": "/core",
    "ssl": false
  }
}
```

Save it under `~/.config/mycroft/mycroft.conf` (or any of the standard
`ovos-config` search paths) to override the built-in defaults.

## How it is loaded

`load_message_bus_config(**overrides)` — `ovos_bus_client/conf.py:18` —
returns a `MessageBusConfig` namedtuple `(host, port, route, ssl)`.

Resolution order, highest priority first:

1. Keyword arguments passed to `load_message_bus_config`.
2. The `websocket` section of the loaded `ovos-config` configuration.
3. Hardcoded defaults (`127.0.0.1`, `8181`, `/core`, `False`).

`MessageBusClient.__init__` calls this with whatever was passed to its own
constructor, so `MessageBusClient(host="some.host")` does the right thing
without you ever touching `conf.py`.

## GUI bus config

The GUI bus uses a separate block read by `load_gui_message_bus_config`
(`conf.py:52`). Same shape, different defaults — see the source if you need
the exact values.

## `client_from_config(subconf="websocket", **overrides)`

`ovos_bus_client/conf.py` also exposes `client_from_config` — a helper that
loads config and constructs a `MessageBusClient` in one step. Use it when you
want a non-default subconfiguration block, e.g. a secondary bus on a
different port.

```python
from ovos_bus_client import client_from_config

bus = client_from_config()                       # standard "websocket" block
gui = client_from_config("gui_websocket")        # custom block
```

## Connection knobs

| Argument | Default | Effect |
|---|---|---|
| `host`   | `127.0.0.1` | WebSocket host. |
| `port`   | `8181`      | WebSocket port. |
| `route`  | `/core`     | HTTP path component of the WebSocket URL. |
| `ssl`    | `False`     | If `True`, use `wss://` rather than `ws://`. |

The full URL is built by `MessageBusClient.build_url(host, port, route, ssl)`
(`ovos_bus_client/client/client.py:67`) — call it directly if you need the
URL string for logging or non-WebSocket tooling.

## TLS / wss

Set `"ssl": true` in `mycroft.conf` (or pass `ssl=True`). The underlying
`websocket-client` library handles certificate validation against the system
trust store. If you are running a self-signed server, you will need to either
trust the cert at the OS level or fork the client — the `MessageBusClient`
itself does not expose an `sslopt` argument.

## When config is wrong

If `mycroft.conf` exists but has no `websocket` section,
`load_message_bus_config` raises `KeyError` (`conf.py:33`). If you want to be
robust against missing config, either:

- Catch the exception and fall back to defaults yourself, or
- Construct `MessageBusClient(host="127.0.0.1", port=8181, route="/core",
  ssl=False)` explicitly so the loader is never consulted.

## Environment variables

`ovos-bus-client` does not read environment variables directly. `ovos-config`
does — see its own documentation if you want env-based overrides for
`mycroft.conf` values.
