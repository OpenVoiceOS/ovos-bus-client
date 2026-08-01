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

## Legacy intent dispatch topics

A second, unrelated legacy spelling rides on the same `emit_legacy` flag.
OVOS-MSG-1 §2.1.1 builds the per-intent dispatch topic as
`<skill_id>:<intent_name>`. Old `ovos-workshop` releases built it from the
padatious resource **filename**, so the authoring extension leaked onto the
wire: a skill with `food.order.intent` registered and listened on
`<skill_id>:food.order.intent`. Current workshop is spec-pure and uses
`<skill_id>:food.order`.

When `emit_legacy` is on, the client mirrors an intent dispatch onto the
suffixed twin, so a handler written against the old spelling still runs. The
rules come from `ovos_spec_tools.intent_topics`:

- the mirror is **alias-driven** — it fires only for an intent that a handler
  in this process subscribed to by its suffixed name (`bus.on()` / `bus.once()`
  fill the client's `IntentAliasRegistry`). No listener, no mirror, so the bus
  invents no topic;
- the twin carries the same data and context plus `__legacy_intent_reemit__`,
  and is never mirrored again;
- it is delivered to **local listeners only**, like the namespace bridge above,
  and never goes back on the wire.

| Flag | env | config (`websocket`) | Effect |
|---|---|---|---|
| `intent_reemit_blanket` | `OVOS_BUS_INTENT_REEMIT_BLANKET` | `intent_reemit_blanket` | mirror **every** intent dispatch, registered alias or not (default **off**) |

Blanket mode exists for pure-bus listeners that subscribe without registering.
It doubles intent traffic and any handler bound to both spellings then needs its
own dedup, so leave it off unless such a listener is known to be present.

Nothing here is normative: no specification mandates the suffixed topic. New
code must produce and consume canonical topics only.

## Rollout

1. **Now** — both flags on by default; every migrated event is on both
   namespaces. Migrate each repo's emit and/or listen to `ovos.*` independently;
   dual-listening callbacks are deduped, single-namespace callbacks are always
   reached.
2. **Later** — once services predominantly use `ovos.*`, reverse the defaults
   (flags off) so the legacy copies stop.
3. **Eventually** — drop the translation entirely; `ovos.*` only.
