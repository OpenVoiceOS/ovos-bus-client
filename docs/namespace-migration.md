# Bus namespace migration

OVOS is moving its bus topics to the canonical `ovos.*` namespace defined by the
specifications (see `ovos_spec_tools.SpecMessage` and `MIGRATION_MAP`).
`MessageBusClient` bridges the transition so a mixed fleet — services and
HiveMind satellites that upgrade at different times — keeps working, and so
**any repo can migrate its emit or its listen to `ovos.*` independently, in any
order, with no coordination.**

## Two emit-side flags (both ON by default)

During the migration window both are on, so every migrated event travels on
**both** the legacy and the `ovos.*` topic.

| Flag | env | config (`websocket`) | Effect |
|---|---|---|---|
| `modernize` | `OVOS_BUS_MODERNIZE` | `modernize` | emitting a *legacy* topic also emits the `ovos.*` spec topic |
| `emit_legacy` | `OVOS_BUS_EMIT_LEGACY` | `emit_legacy` | emitting an `ovos.*` spec topic also emits the legacy topic |

Only payload-compatible renames are translated (`MIGRATION_MAP`); topics whose
payload shape also changes are never translated and must be migrated at the call
site.

## Handler de-duplication

Because both namespaces carry the event, a handler that is subscribed to **both**
the legacy and the spec topic would otherwise run twice. The client wraps
handlers on migrated topics so the **mirror** copy is dropped — the second
delivery of the *same payload via the counterpart topic* within a short window is
suppressed. This makes dual-listening safe (no need to coordinate which namespace
a handler uses while migrating).

It does **not** suppress two genuine events on the *same* topic: only a delivery
via the counterpart topic is treated as a mirror. Dedup state is per handler,
shared across its registrations, so two `bus.on(...)` calls for the same callback
collapse correctly.

## Rollout

1. **Now** — both flags on by default; every migrated event is on both
   namespaces. Migrate each repo's emit and/or listen to `ovos.*` independently;
   dual-listening callbacks are deduped, single-namespace callbacks are always
   reached.
2. **Later** — once services predominantly use `ovos.*`, reverse the defaults
   (flags off) so the legacy copies stop.
3. **Eventually** — drop the translation entirely; `ovos.*` only.
