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

The rules come from `ovos_spec_tools.intent_topics`:

- **wire twin (primary).** `emit()` sends the suffixed twin as a second frame,
  after the canonical dispatch. An outdated standalone skill container runs an
  old bus-client with no bridge in it, so only a real frame reaches it;
- **alias-driven.** The twin is sent only for an intent with a recorded alias,
  so wire traffic doubles for those intents alone. The client's
  `IntentAliasRegistry` is filled from the intent **registrations it sees on
  the wire** — an old container announces its intents under the suffixed name —
  and from its own `bus.on()` / `bus.once()` calls, which cover an old-style
  listener in the same process. Nothing else feeds it, so the bus invents no
  topic;
- **modernize on receive.** A suffixed dispatch off the wire is also delivered
  locally under its canonical topic, so a spec-pure skill hears an old core;
- **local mirror (secondary).** A canonical dispatch is also delivered locally
  under a recorded suffixed alias, covering an old-style listener in this
  process. Nothing bridged goes back on the wire;
- **delivered once.** Every twin carries `__legacy_intent_reemit__`, and each
  client keeps a short per-dispatch record of the spellings it already
  delivered. When a new core puts both spellings on the wire, a client that
  bridges them itself drops the twin, so no handler runs twice.

| Flag | env | config (`websocket`) | Effect |
|---|---|---|---|
| `intent_reemit_blanket` | `OVOS_BUS_INTENT_REEMIT_BLANKET` | `intent_reemit_blanket` | twin **every** intent dispatch, on the wire and locally, registered alias or not (default **off**) |

Blanket mode exists for pure-bus listeners that subscribe without ever
registering. It invents topics nobody may listen on and doubles all intent
traffic on the wire, so leave it off unless such a listener is known to be
present.

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
