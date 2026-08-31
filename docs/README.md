# ovos-bus-client developer documentation

The user-facing introduction is in the [top-level README](../README.md). This
folder is for developers integrating `ovos-bus-client` into skills, plugins,
agent backends, or anything else that talks to an OVOS messagebus.

## Table of contents

### Zero to hero

1. [Getting started](getting_started.md) — install, connect, send your first message.
2. [Core concepts](concepts.md) — the OVOS bus model, message anatomy, who emits what.
3. [Messages](messages.md) — `Message`, `GUIMessage`, `reply`/`forward`/`response`, helpers.
4. [The client](client.md) — `MessageBusClient`: connect, threading, handlers, lifecycle.
4b. [Async client](async_client.md) — `AsyncMessageBusClient`: same shape, but `async/await`-native (optional `[async]` extra).
5. [Configuration](configuration.md) — `load_message_bus_config`, env knobs, SSL, custom routes.
6. [Sessions](session.md) — `Session`, `SessionManager`, `IntentContextManager`.

### Intermediate

7. [Waiters and collectors](waiter_and_collector.md) — request/response and multi-reply patterns.
8. [High-level APIs](apis.md) — `GUIInterface`, `OCPInterface`, `EnclosureAPI`, `EventSchedulerInterface`.
9. [CLI tools](scripts.md) — `ovos-listen`, `ovos-speak`, `ovos-say-to`, `ovos-simple-cli`.
10. [Common patterns](patterns.md) — request/reply, broadcast, session scoping, reconnect.
11. [Testing](testing.md) — `FakeBus`, isolating tests, asserting bus traffic.

### Advanced and reference

12. [Migration from 1.x](migration.md) — what moved out in 2.0 and where to find it.
13. [Development](development.md) — repo layout, running tests, releases.
14. [Glossary](glossary.md) — terms you will hit reading OVOS code.

## Audience

- **Skill / plugin authors** start at [Getting started](getting_started.md), then
  jump to [High-level APIs](apis.md).
- **Agent backend / pipeline integrators** read [Sessions](session.md) and
  [Common patterns](patterns.md) first — those are the load-bearing surfaces.
- **Contributors to `ovos-bus-client` itself** read [Core concepts](concepts.md)
  then [Development](development.md).
