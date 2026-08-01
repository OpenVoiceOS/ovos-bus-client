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

Both spellings are live in the field, and a skill container upgrades
independently of the core it talks to. When `emit_legacy` is on, the client
bridges the four pairings:

| | old core (dispatches `X:Y.intent`) | new core (dispatches `X:Y`) |
|---|---|---|
| **old skill** (listens `X:Y.intent`) | works today, untouched | new core sends the **wire twin** |
| **new skill** (listens `X:Y`) | receiving client **modernizes** on arrival | works today, untouched |

The bridge is two stateless rules, both built on the two pure functions in
`ovos_spec_tools.intent_topics`:

1. **Send.** `emit()` sends the canonical frame, then sends
   `legacy_intent_topic(msg_type)` as a second frame carrying the same payload
   plus a `_intent_compat_twin` context marker. Every intent
   dispatch is twinned: which listeners exist in which process is unknowable
   from the emitter, and a twin nobody listens to is a few ignored bytes.
2. **Receive.** A suffixed frame arriving **without** that marker is also
   dispatched locally on `canonical_intent_topic(msg_type)`, so a spec-pure
   skill hears an old core. Nothing bridged goes back on the wire.

The marker is the whole deduplication, and exactly-once holds by inspection:

- a **new** emitter sends the pair, and rule 2 ignores the marked twin, so the
  canonical handlers run once and suffixed handlers still receive the twin;
- an **old** emitter sends one unmarked suffixed frame, and rule 2 modernizes
  it once;
- an **already-suffixed** emit is never re-twinned, so the mirror cannot
  cascade.

The bridge is gated by `emit_legacy` (`OVOS_BUS_EMIT_LEGACY`, `websocket`
config key `emit_legacy`) — the same flag as the namespace bridge. Turning the
compat off for good is deleting the two `if` blocks.

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
