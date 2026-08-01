# Bus namespace migration — completed

OVOS bus topics are the canonical `ovos.*` names defined by the specifications
(see `ovos_spec_tools.SpecMessage` and `MIGRATION_MAP`). The migration is over:
`MessageBusClient` speaks the spec topics and nothing else.

## What was removed

Earlier releases carried two compat bridges on the receive side. Both are gone.

| Removed | What it did |
|---|---|
| namespace bridge | delivered a migrated event to listeners on **both** the legacy and the `ovos.*` topic |
| handler mirror-guard | dropped the mirror copy so a handler subscribed to both namespaces ran once |
| intent-topic twin | mirrored a canonical `<skill_id>:<intent>` dispatch onto the old `<skill_id>:<intent>.intent` spelling |

With them go the flags that steered them: `emit_legacy`, `modernize` and
`intent_reemit_blanket`, in both their `websocket` config and `OVOS_BUS_*`
environment spellings.

## If you still set one of those flags

The client raises a `RuntimeError` at construction. This is deliberate. Setting
`emit_legacy` means you believe the legacy topics still travel; they do not, and
a silent client would hand you a fleet that drops messages with no signal at all.

To clear the error: migrate the producers and the consumers to the spec topics,
then unset the flag.

## Migrating a component now

1. Look the legacy topic up in `ovos_spec_tools.MIGRATION_MAP` to find the
   `SpecMessage` that replaces it. `SPEC_TO_LEGACY` is the reverse index.
2. Replace the literal string with the `SpecMessage` member, on the emit side
   and on the listen side.
3. For per-intent dispatch topics, use `<skill_id>:<intent_name>` — the
   canonical form of OVOS-MSG-1 §2.1.1, with no `.intent` extension. The
   authoring filename never belonged on the wire.

The helpers in `ovos_spec_tools` — `MIGRATION_MAP`, `SPEC_TO_LEGACY`,
`migration_counterpart` and `ovos_spec_tools.intent_topics` — remain. They are
pure functions, used by the spec linter and by migration tooling. They are
simply no longer wired into the bus.

## Topics outside the map

Two families were never bridged and are unaffected by this removal:

- the PIPELINE-1 §8 handler-lifecycle trio, where the orchestrator emits the
  spec `ovos.intent.handler.*` and the skill framework keeps
  `mycroft.skill.handler.*` as a private done-signal;
- the STOP-1 per-skill handshake placeholders `{skill_id}.stop.ping` and
  `{skill_id}.stop`, which are runtime-assembled topics rather than static
  strings.
