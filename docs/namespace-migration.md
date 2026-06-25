# Bus namespace migration

OVOS is moving its bus topics to the canonical `ovos.*` namespace defined by the
specifications (see `ovos_spec_tools.SpecMessage` and `MIGRATION_MAP`). To let a
mixed fleet — where services and HiveMind satellites upgrade at different times —
interoperate during the transition, `MessageBusClient` can translate topics
**on emit**, in either direction. The goal is for every service to emit and
listen on the `ovos.*` topics; these flags bridge the gap until then.

Both flags are **off by default** and **orthogonal** — enable whichever a node
needs. They operate purely on emit; `on()` is unchanged.

## `modernize` — legacy → spec

When a node still emits a *legacy* topic (e.g. it runs older code), also emit the
`ovos.*` spec counterpart, so spec-native listeners receive the event.

- env: `OVOS_BUS_MODERNIZE=true`
- config: `{"websocket": {"modernize": true}}`

Emitting `speak` also puts `ovos.utterance.speak` on the bus.

## `emit_legacy` — spec → legacy

When a node emits a *spec* topic (it has migrated), also emit the legacy
counterpart, so not-yet-migrated listeners still receive the event.

- env: `OVOS_BUS_EMIT_LEGACY=true`
- config: `{"websocket": {"emit_legacy": true}}`

Emitting `ovos.utterance.handle` also puts `recognizer_loop:utterance` on the bus.

## Important: listen on one namespace

There is **no listener-side translation and no de-duplication**. Each listener
must subscribe to exactly one namespace for a given event. If a single handler is
registered on *both* the legacy and the spec topic while the matching emit flag is
on, it will run **twice** per event. Migrate a listener by switching its
subscription from the legacy topic to the spec topic — do not subscribe to both.

Only payload-compatible renames are translated (`MIGRATION_MAP`); topics whose
payload shape also changes (e.g. the handler-lifecycle trio) are not translated
and must be migrated at the call site.
