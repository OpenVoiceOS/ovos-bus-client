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

A second, unrelated legacy spelling rides on the same two flags.
OVOS-MSG-1 §2.1.1 builds the per-intent dispatch topic as
`<skill_id>:<intent_name>`. Old `ovos-workshop` releases built it from the
padatious resource **filename**, so the authoring extension leaked onto the
wire: a skill with `food.order.intent` registered and listened on
`<skill_id>:food.order.intent`. Current workshop is spec-pure and uses
`<skill_id>:food.order`.

Both spellings are live in the field, and a skill container upgrades
independently of the core it talks to. The client bridges the four pairings:


| | old core (dispatches `X:Y.intent`) | new core (dispatches `X:Y`) |
|---|---|---|
| **old skill** (listens `X:Y.intent`) | works today, untouched | new core sends the **wire twin** |
| **new skill** (listens `X:Y`) | receiving client **modernizes** on arrival | works today, untouched |

The bridge is two stateless rules, both built on the pure functions in
`ovos_spec_tools.intent_topics`:

1. **Send** (`emit_legacy`). `emit()` sends the canonical frame, then sends
   `legacy_intent_topic(msg_type)` as a second frame. The twin carries the
   payload and the context **verbatim** — the same session, language, routing
   and `active_skills` as the canonical frame — plus an
   `_intent_compat_twin` context marker. It is built with a plain copy, never
   with `Message.forward()`: `forward()` re-stamps the session, and for
   `session_id == "default"` it replaces the carried session with the emitting
   process's own. Every intent dispatch is twinned: which listeners exist in
   which process is unknowable from the emitter, and a twin nobody listens to
   is a few ignored bytes.
2. **Receive** (`modernize`). A suffixed frame arriving **without** that marker
   is also dispatched locally on `canonical_intent_topic(msg_type)`, so a
   spec-pure skill hears an old core. The canonical copy stays local: it is
   never put back on the wire.

### Two flags, two operations

Rule 1 is legacy emission and rides `emit_legacy`
(`OVOS_BUS_EMIT_LEGACY`, `websocket` config key `emit_legacy`). Rule 2 is
modernization and rides `modernize` (`OVOS_BUS_MODERNIZE`, config key
`modernize`) — the same split as the namespace bridge. An operator who turns
`emit_legacy` off to quiet the wire keeps old-core → new-skill delivery.

| `emit_legacy` | `modernize` | effect |
|---|---|---|
| on | on | full bridge (default) |
| off | on | no twin on the wire; an old core is still heard |
| on | off | old skills still reached; an old core is not heard |
| off | off | bridge off |

### Which topics are intent topics

`is_intent_topic` is deliberately narrow. A colon is **not** enough: several
subsystems own a `<namespace>:<event>` topic (`recognizer_loop:utterance`,
`question:query`, `padatious:register_intent`, `speak:b64_audio`,
`stop:global`), and the reserved per-skill dispatch names (`<skill_id>:stop`,
`:converse`, `:common_query`, `:ping`, `:pong`) never came from a `.intent`
resource file. None of them is twinned. Topics that migrate through
`MIGRATION_MAP` are excluded too — their counterpart is the namespace bridge's
business. Everything else keeps the documented invariant: **one emit, one
wire frame**.

### One dispatch, one handler run

The pair guard is the deduplication. A registration on either spelling of an
intent topic is wrapped with a mirror guard keyed by the **topic pair**
(`frozenset({canonical, suffixed})`), shared by every registration on either
spelling. The guard drops a frame whose payload+context fingerprint it already
saw on the counterpart topic inside the mirror window, and never suppresses a
repeat on the same topic — so two independent handlers on one topic each still
run once per dispatch, and two genuine dispatches still run the handler twice.

The pair key, not the handler, is what makes this work. `ovos-workshop`
9.3.2a1+ binds one skill method to both spellings through a **fresh wrapper
closure per binding**, so the two registrations are two distinct callables. A
per-handler guard would give each its own state, the canonical frame would run
one closure and the twin the other, and the skill handler would fire twice for
a single dispatch.

Exactly-once then holds in every pairing:

- **new core → new skill** — canonical + marked twin arrive; the guard drops
  the second, whichever spellings the handler is bound to;
- **new core → old skill** — the old container binds the suffixed topic only
  and holds no bridge; the twin is the frame that reaches it;
- **old core → new skill** — one unmarked suffixed frame arrives, rule 2
  dispatches its canonical spelling, and the guard drops whichever of the two
  the handler already ran;
- **old core → old skill** — untouched, as today.

### Known residual gap

A container frozen with a bus-client older than this release **and**
`ovos-workshop` 9.3.2a1 … 9.3.9a1 has the dual binding but not the pair guard.
When a core carrying this bridge emits the twin, that container runs the
handler twice.

This is a deliberate trade-off. The dual-binding workshop window is small; the
population the bridge exists for — stable and testing channel pins at workshop
≤ 9.3.1a2, which bind the suffixed topic **only** — is much larger, and it
receives nothing at all without the twin. `ovos-workshop#500` removes the dual
binding going forward, and upgrading the bus-client in an affected container
closes the gap immediately.

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
