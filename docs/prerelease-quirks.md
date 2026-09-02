# Prerelease quirks

This file lists everything that changed or broke since the last stable
release, `1.5.0`. It is version-stamped and newest first. If you install an
alpha of `ovos-bus-client`, read this before you file a bug: the behavior
you are seeing may be documented here already.

This file resets at the next stable release. At that point its contents
become upgrade notes for the `1.5.0 -> next-stable` jump, and a new, empty
quirks log starts.

## 2.10.1a2

`Session.deserialize()` built its result from a hand-enumerated
constructor call rather than the `ovos_spec_tools.session.Session` instance
`_SpecSession.from_dict()` already stamped with `wire_payload`, so the
`Session` object bus-client actually returned to callers was always
snapshot-less. `SessionManager._store()` (inherited from ovos-spec-tools)
reads `wire_payload` to decide, per OVOS-SESSION-2 §5.1, which fields an
inbound default-session message actually carried; without it every
serialized field -- and bus-client's `serialize()` emits `location`,
`is_speaking`, `is_recording`, `system_unit`, `time_format`, `date_format`,
`active_skills` and `context` unconditionally -- counted as carried, so a
minimal inbound default-session message (e.g. `{"session_id": "default",
"lang": "en-us"}`) silently overwrote previously stored default-session
state such as `location_preferences`, `is_speaking`, `blacklisted_skills`
and `intent_context` with deployment defaults. `Session.deserialize()` now
stamps `wire_payload` on the object it returns, matching the
`ovos_spec_tools.session.Session.from_dict()` contract. This requires
`ovos-spec-tools>=1.9.1a1`, which shipped the `wire_payload`-aware
`merge_from()`/`_store()` behavior this fix activates.

## 2.10.0a2

A clean `close()` could still surface `RuntimeError: cannot schedule new
futures after interpreter shutdown` from the default `ExecutorEventEmitter`
(a `pyee` emitter backed by a bare `ThreadPoolExecutor`, whose `emit()`
submits to it). The receiver thread `run_in_thread()` starts is a daemon, so
it can still be mid-emit -- inside `on_error()`'s reconnect backoff -- when
the interpreter starts tearing down, and by then the executor refuses new
work with that `RuntimeError` instead of a clean no-op. `close()` now joins
the thread `run_in_thread()` started (2 second timeout) and, as its very
last step, shuts the emitter down (`self.emitter.shutdown(wait=False)`,
skipped for emitters that don't expose `.shutdown()`). `on_error()`'s
reconnect path also checks `_closing` immediately before emitting
`'reconnecting'` and before its retry sleep, and swallows a `RuntimeError`
from the emitter at debug level in case a call slips through the window
between that check and the emit.

Two behaviour changes are worth knowing about. `close()` can now block for
up to 2 seconds if the receiver thread is inside `on_error()`'s reconnect
backoff sleep when `close()` is called, where it previously returned
immediately -- the wait is bounded and lets that thread wind down cleanly
instead of leaking past `close()`. And the emitter is torn down only after
`client.close()` and the thread join have run, so the `'close'`/`'error'`
events a live disconnect legitimately triggers are still delivered to any
listener registered with `bus.on(...)` before the emitter itself goes away.

## 2.8.5a2

`EventSchedulerInterface.update_scheduled_event()` emitted
`mycroft.schedule.update_event`, but the scheduler
(`ovos_bus_client.util.scheduler.EventScheduler`) only ever listened on
`mycroft.scheduler.update_event`. The topic mismatch made the call a silent
no-op: no error, the event just never updated. Fixed by emitting the
spelling the scheduler actually listens on.

`close()` used to only close the currently-active websocket object. A
client caught inside `on_error()`'s reconnect backoff (sleep, recreate the
websocket, recurse into `run_forever()` -- all on the same thread
`run_in_thread()` started) ignored that and reconnected again after
`close()` had already returned, so the receiver thread could outlive
`close()` indefinitely instead of stopping within a bounded join. `close()`
now sets a `_closing` flag that the reconnect path checks before sleeping,
before recreating the websocket, and before recursing, so a client mid-
backoff actually stops.

The flag is initialised in `__init__` and reset in `run_in_thread()`
BEFORE the new thread is started, not inside `run_forever()`'s body: a
`close()` landing between `run_in_thread()` returning and the thread
actually reaching `run_forever()` would otherwise be undone the instant
the thread got there, and the client would reconnect right after being
told to close.

`AsyncMessageBusClient.emit()` now puts the same wire frames as
`MessageBusClient.emit()` for the same `Message`, including the legacy
intent-topic and namespace twins (`OVOS_BUS_EMIT_LEGACY` /
`OVOS_BUS_WIRE_LEGACY_TWINS` still gate them the same way on both clients).
Earlier alphas of the async client only sent the canonical frame, so a
producer that switched onto it silently stopped emitting the V0-compat
twins. `AsyncMessageBusClient` also gained a `connected` property matching
`MessageBusClient`'s connection-liveness semantics, for consumers that check
`getattr(bus, "connected", None)` against either client.

## 2.8.4a3

Importing `ovos_bus_client.session` no longer emits an ovos-config
deprecation notice. The module-level default `Session` resolves its default
lang by reading `Configuration()` directly instead of calling the
deprecated `get_default_lang()`. The `ovos-config` floor is now `1.0.0`.

## 2.8.4a1

Added an escape hatch for the namespace wire twin added in 2.8.3a1:
`OVOS_BUS_WIRE_LEGACY_TWINS` (env var) or `websocket.wire_legacy_twins`
(config key). Default is `True`. Turn it off only if you know no listener
on the bus still needs the real legacy-spelled wire frame.

## 2.8.3a1

Every canonical (`ovos.*`) emit of a topic in `MIGRATION_MAP` now also puts
a real second wire frame on the legacy spelling, not just intent-dispatch
topics as before. This closes the gap for the actual supported population:
stable `ovos-bus-client` 1.5.0 and anything older, which has no
`NamespaceTranslator` at all and never sees a canonical-only emit reach a
legacy-spelled subscription.

Known quirk: a receiver running an alpha between 2.2.0a1 and 2.8.2a1 already
bridges both spellings locally from the canonical frame alone. It is not a
supported configuration (only the latest prerelease is supported), and it
now double-delivers every migrated topic, because it gets both the local
bridge and the new wire twin. Upgrade any such receiver to the current
alpha.

## 2.8.2a1

`Session.__init__` and `get_message_lang()` used to call deprecated
`ovos_config.locale` helpers on every construction, firing a
`DeprecationWarning` on every utterance. Fixed: the tz path reads
`get_config_tz()` directly; the lang path keeps calling the real helper
through a small shared cache-aware wrapper, since `ovos-config` stable
still keeps a private lang cache that `Configuration()` does not see.

## 2.8.1a1

Fixed a regression in double-registration handling. Wrapping every
registration in a fresh closure (added for the intent-topic bridge and the
namespace-migration mirror guard) broke the old behavior where registering
the same handler twice on one topic collapsed onto one listener. `on()`
already reused the wrapper for a repeat `(event_name, func)` pair; `once()`
did not, so a handler bound via `once()` to both spellings of a mirrored
topic could fire twice. `once()` now goes through the same dedup path as
`on()`.

## 2.8.0a1

Added the legacy intent-topic bridge: a wire twin on emit, modernization on
receive. Old cores build the per-intent dispatch topic as
`<skill_id>:<intent_name>.intent` (leaking the padatious resource file
extension); current `ovos-workshop` builds `<skill_id>:<intent_name>`. This
release bridges both spellings so an old core and a new skill (or vice
versa) still talk to each other. Documented in full in
`docs/namespace-migration.md`. See RULE 1 (send) / RULE 2 (receive) in
`ovos_bus_client/client/client.py`.

## 2.7.3a1

Fixed the bus CLIs (`ovos-speak`, `ovos-listen`, etc.) hanging forever when
the messagebus is unreachable.

## 2.7.2a1

`blacklisted_pipelines` deployment default is now seeded from config
instead of a hardcoded empty default.

## 2.7.1a1

A malformed `Session` on an inbound message is now rejected instead of
silently accepted.

## 2.7.0a1

Legacy `Session.context` unified onto the canonical `intent_context` field.

## 2.6.0a1 - 2.6.5a1

Session/registry hardening: canonical Session list/dict fields always
deserialize to empty containers instead of `None`; `SessionManager` keeps
one live `Session` per id (singleton, no more duplicate instances);
`Session.update_from` applies SESSION-1 deserialization semantics;
`SessionManager` subclasses the `ovos-spec-tools` registry; the namespace
bridge for a topic pair now bridges its counterpart on receive rather than
emitting it as a second wire message; malformed bus/GUI frames are
discarded instead of tearing down the connection; the bus-connected state
is cleared on close/error.

## 2.5.0a1 - 2.5.1a3

`SessionManager` merges `intent_context` entry-by-entry
(OVOS-CONTEXT-1 §5.3) instead of replacing the whole dict. A mirrored
namespace payload is translated onto its counterpart topic correctly.
`ovos.session.sync` now uses `SpecMessage.SESSION_SYNC`.

## 2.4.0a1 - 2.4.1a1

`Session` subclasses `ovos_spec_tools.Session` (canonical SESSION-1) with a
back-compat shim. An unset `site_id` stays absent instead of being
fabricated as `"unknown"` (BRIDGE-1 §3.3).

## 2.3.0a1 - 2.3.0a2

Namespace migration now uses the shared `NamespaceTranslator` from
`ovos-spec-tools` instead of inline migration logic, with both flags on by
default.

## 2.2.0a1

Namespace migration became **opt-in by default turned on**: both
`modernize` and `emit_legacy` default to `True`, wired through
`MessageBusClient` directly, with handler dedup for dual-listening
callbacks (see `docs/namespace-migration.md`).

## 2.1.2a2

The transparent legacy `<->` `ovos.*` namespace migration first landed as
opt-in (both flags default `False`). `EnclosureAPI` deprecated in favor of
`ovos-gui-api-client`.

## 2.1.0a1 - 2.1.2a1

`Message` now subclasses `ovos_spec_tools.Message` with no API break.
Restored legacy AES encryption at the websocket transport edge. Websocket
`on_error` callbacks that are not exceptions are now ignored instead of
raising.

## 2.0.0a1 - 2.0.0a4

Major version bump. The HiveMind agent protocol entry point
(`hivemind.agent.protocol`) and the solver entry point
(`neon.plugin.solver`) were removed from this package — see
`docs/migration.md`. Language tags now go through
`ovos_spec_tools.standardize_lang`. Test coverage raised from 19% to 93%
real / 67% pytest-cov.
