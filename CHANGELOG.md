# Changelog

## [2.11.8a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.11.8a1) (2026-09-04)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.11.7a1...2.11.8a1)

**Merged pull requests:**

- fix: location\_preferences setter, session/message readability cleanup [\#331](https://github.com/OpenVoiceOS/ovos-bus-client/pull/331) ([JarbasAl](https://github.com/JarbasAl))

## [2.11.7a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.11.7a1) (2026-09-04)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.11.6a1...2.11.7a1)

**Merged pull requests:**

- fix: take session.location from the ovos-spec-tools field registry [\#327](https://github.com/OpenVoiceOS/ovos-bus-client/pull/327) ([JarbasAl](https://github.com/JarbasAl))

## [2.11.6a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.11.6a1) (2026-09-04)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.11.5a1...2.11.6a1)

**Merged pull requests:**

- fix: never push the default session on connect \(OVOS-SESSION-2 §2.7\) [\#328](https://github.com/OpenVoiceOS/ovos-bus-client/pull/328) ([JarbasAl](https://github.com/JarbasAl))

## [2.11.5a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.11.5a1) (2026-09-04)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.11.4a1...2.11.5a1)

**Merged pull requests:**

- fix: stop folding the default session on every observed bus message [\#317](https://github.com/OpenVoiceOS/ovos-bus-client/pull/317) ([JarbasAl](https://github.com/JarbasAl))

## [2.11.4a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.11.4a1) (2026-09-04)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.11.3a1...2.11.4a1)

**Merged pull requests:**

- fix: use spec-tools session resolver and stamp bound sessions on Collection/GUI derivations [\#324](https://github.com/OpenVoiceOS/ovos-bus-client/pull/324) ([JarbasAl](https://github.com/JarbasAl))

## [2.11.3a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.11.3a1) (2026-09-03)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.11.2a2...2.11.3a1)

**Merged pull requests:**

- fix: carry session.location as {lat, lon, tz} per OVOS-SESSION-1 §3.5 [\#320](https://github.com/OpenVoiceOS/ovos-bus-client/pull/320) ([JarbasAl](https://github.com/JarbasAl))

## [2.11.2a2](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.11.2a2) (2026-09-03)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.11.2a1...2.11.2a2)

**Merged pull requests:**

- test: stop pinning repeated SessionManager.get identity [\#321](https://github.com/OpenVoiceOS/ovos-bus-client/pull/321) ([JarbasAl](https://github.com/JarbasAl))

## [2.11.2a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.11.2a1) (2026-09-03)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.11.1a1...2.11.2a1)

**Merged pull requests:**

- fix: resolve session\_id per SESSION-1 without reaching into ovos-spec-tools internals [\#318](https://github.com/OpenVoiceOS/ovos-bus-client/pull/318) ([JarbasAl](https://github.com/JarbasAl))

## [2.11.1a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.11.1a1) (2026-09-03)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.11.0a1...2.11.1a1)

**Merged pull requests:**

- fix: reach the session registry through its public API, not \_store [\#313](https://github.com/OpenVoiceOS/ovos-bus-client/pull/313) ([JarbasAl](https://github.com/JarbasAl))

## [2.11.0a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.11.0a1) (2026-09-03)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.10.1a1...2.11.0a1)

**Merged pull requests:**

- feat: scheduled events service and client \(SCHEDULER-1\) [\#311](https://github.com/OpenVoiceOS/ovos-bus-client/pull/311) ([JarbasAl](https://github.com/JarbasAl))

## [2.10.1a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.10.1a1) (2026-09-01)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.10.0a2...2.10.1a1)

**Merged pull requests:**

- fix: shut down the event emitter and join the dispatch thread on close [\#308](https://github.com/OpenVoiceOS/ovos-bus-client/pull/308) ([JarbasAl](https://github.com/JarbasAl))

## [2.10.0a2](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.10.0a2) (2026-09-01)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.10.0a1...2.10.0a2)

**Merged pull requests:**

- revert: scheduled events service \(held for owner review\) [\#307](https://github.com/OpenVoiceOS/ovos-bus-client/pull/307) ([JarbasAl](https://github.com/JarbasAl))

## [2.10.0a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.10.0a1) (2026-09-01)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.9.1a1...2.10.0a1)

**Merged pull requests:**

- feat: scheduled events service implementing SCHEDULER-1 [\#305](https://github.com/OpenVoiceOS/ovos-bus-client/pull/305) ([JarbasAl](https://github.com/JarbasAl))

## [2.9.1a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.9.1a1) (2026-09-01)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.9.0a1...2.9.1a1)

**Merged pull requests:**

- fix: allow ovos-config 3.x [\#302](https://github.com/OpenVoiceOS/ovos-bus-client/pull/302) ([JarbasAl](https://github.com/JarbasAl))

## [2.9.0a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.9.0a1) (2026-08-31)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.8.6a2...2.9.0a1)

## [2.8.6a2](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.8.6a2) (2026-08-31)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.8.7a1...2.8.6a2)

## [2.8.7a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.8.7a1) (2026-08-31)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.8.6a1...2.8.7a1)

## [2.8.6a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.8.6a1) (2026-08-31)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.8.5a2...2.8.6a1)

## [2.8.5a2](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.8.5a2) (2026-08-31)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.8.5a1...2.8.5a2)

**Merged pull requests:**

- docs: add AGENTS.md with the conventions for coding agents [\#296](https://github.com/OpenVoiceOS/ovos-bus-client/pull/296) ([JarbasAl](https://github.com/JarbasAl))
- fix: make close\(\) stop a client that is reconnecting [\#295](https://github.com/OpenVoiceOS/ovos-bus-client/pull/295) ([JarbasAl](https://github.com/JarbasAl))
- docs: cross-link the technical manual [\#273](https://github.com/OpenVoiceOS/ovos-bus-client/pull/273) ([JarbasAl](https://github.com/JarbasAl))
- fix: emit mycroft.scheduler.update\_event so update\_scheduled\_event reaches the scheduler [\#222](https://github.com/OpenVoiceOS/ovos-bus-client/pull/222) ([JarbasAl](https://github.com/JarbasAl))

## [2.8.5a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.8.5a1) (2026-08-31)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.8.4a2...2.8.5a1)

**Merged pull requests:**

- fix: avoid calling deprecated get\_default\_lang\(\) at session import time [\#293](https://github.com/OpenVoiceOS/ovos-bus-client/pull/293) ([JarbasAl](https://github.com/JarbasAl))

## [2.8.4a2](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.8.4a2) (2026-08-14)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.8.4a1...2.8.4a2)

**Merged pull requests:**

- docs: prerelease-quirks changelog [\#290](https://github.com/OpenVoiceOS/ovos-bus-client/pull/290) ([JarbasAl](https://github.com/JarbasAl))

## [2.8.4a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.8.4a1) (2026-08-14)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.8.3a1...2.8.4a1)

**Merged pull requests:**

- fix: gate the namespace wire twin behind an escape-hatch flag [\#288](https://github.com/OpenVoiceOS/ovos-bus-client/pull/288) ([JarbasAl](https://github.com/JarbasAl))

## [2.8.3a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.8.3a1) (2026-08-14)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.8.2a1...2.8.3a1)

**Merged pull requests:**

- fix: legacy wire twin for every migrated namespace topic, not just intents [\#286](https://github.com/OpenVoiceOS/ovos-bus-client/pull/286) ([JarbasAl](https://github.com/JarbasAl))

## [2.8.2a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.8.2a1) (2026-08-14)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.8.1a1...2.8.2a1)

**Merged pull requests:**

- fix: silence deprecation warning noise on hot path [\#282](https://github.com/OpenVoiceOS/ovos-bus-client/pull/282) ([JarbasAl](https://github.com/JarbasAl))

## [2.8.1a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.8.1a1) (2026-08-14)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.8.0a1...2.8.1a1)

**Merged pull requests:**

- fix: restore idempotent double-registration for intent-topic wrapped handlers [\#281](https://github.com/OpenVoiceOS/ovos-bus-client/pull/281) ([JarbasAl](https://github.com/JarbasAl))

## [2.8.0a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.8.0a1) (2026-08-13)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.7.3a1...2.8.0a1)

**Merged pull requests:**

- feat: legacy intent-topic bridge — wire twin on emit, modernize on receive [\#271](https://github.com/OpenVoiceOS/ovos-bus-client/pull/271) ([JarbasAl](https://github.com/JarbasAl))

## [2.7.3a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.7.3a1) (2026-08-01)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.7.2a1...2.7.3a1)

**Merged pull requests:**

- fix: bus CLIs hang forever when the messagebus is unreachable [\#274](https://github.com/OpenVoiceOS/ovos-bus-client/pull/274) ([JarbasAl](https://github.com/JarbasAl))

## [2.7.2a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.7.2a1) (2026-07-31)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.7.1a1...2.7.2a1)

**Merged pull requests:**

- fix: seed blacklisted\_pipelines deployment default from config [\#269](https://github.com/OpenVoiceOS/ovos-bus-client/pull/269) ([JarbasAl](https://github.com/JarbasAl))

## [2.7.1a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.7.1a1) (2026-07-24)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.7.0a1...2.7.1a1)

**Merged pull requests:**

- fix: survive a malformed session on an inbound message [\#267](https://github.com/OpenVoiceOS/ovos-bus-client/pull/267) ([JarbasAl](https://github.com/JarbasAl))

## [2.7.0a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.7.0a1) (2026-07-16)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.6.5a1...2.7.0a1)

**Merged pull requests:**

- feat: unify legacy Session.context onto canonical intent\_context [\#256](https://github.com/OpenVoiceOS/ovos-bus-client/pull/256) ([JarbasAl](https://github.com/JarbasAl))

## [2.6.5a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.6.5a1) (2026-07-15)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.6.4a1...2.6.5a1)

**Merged pull requests:**

- fix: discard malformed frames instead of tearing down the connection [\#264](https://github.com/OpenVoiceOS/ovos-bus-client/pull/264) ([JarbasAl](https://github.com/JarbasAl))
- fix: Clear bus connected state on close/error [\#263](https://github.com/OpenVoiceOS/ovos-bus-client/pull/263) ([goldyfruit](https://github.com/goldyfruit))

## [2.6.4a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.6.4a1) (2026-07-03)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.6.3a1...2.6.4a1)

**Merged pull requests:**

- fix: always deserialize canonical Session list/dict fields to empty containers [\#257](https://github.com/OpenVoiceOS/ovos-bus-client/pull/257) ([JarbasAl](https://github.com/JarbasAl))

## [2.6.3a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.6.3a1) (2026-07-03)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.6.2a2...2.6.3a1)

**Merged pull requests:**

- fix: bridge namespace counterpart on receive, not as a second wire message [\#258](https://github.com/OpenVoiceOS/ovos-bus-client/pull/258) ([JarbasAl](https://github.com/JarbasAl))

## [2.6.2a2](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.6.2a2) (2026-06-29)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.6.2a1...2.6.2a2)

**Merged pull requests:**

- refactor: SessionManager subclasses the ovos-spec-tools registry [\#254](https://github.com/OpenVoiceOS/ovos-bus-client/pull/254) ([JarbasAl](https://github.com/JarbasAl))

## [2.6.2a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.6.2a1) (2026-06-29)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.6.1a1...2.6.2a1)

**Merged pull requests:**

- fix: Session.update\_from applies SESSION-1 deserialization semantics [\#251](https://github.com/OpenVoiceOS/ovos-bus-client/pull/251) ([JarbasAl](https://github.com/JarbasAl))

## [2.6.1a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.6.1a1) (2026-06-29)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.6.0a1...2.6.1a1)

**Merged pull requests:**

- fix: SessionManager keeps one live Session per id \(singleton\) [\#249](https://github.com/OpenVoiceOS/ovos-bus-client/pull/249) ([JarbasAl](https://github.com/JarbasAl))

## [2.6.0a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.6.0a1) (2026-06-28)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.5.1a3...2.6.0a1)

**Merged pull requests:**

- feat: shared HandlerLifecycle done-signal helper for in-process dispatchers [\#246](https://github.com/OpenVoiceOS/ovos-bus-client/pull/246) ([JarbasAl](https://github.com/JarbasAl))

## [2.5.1a3](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.5.1a3) (2026-06-28)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.5.1a2...2.5.1a3)

**Merged pull requests:**

- refactor: use SpecMessage.SESSION\_SYNC for the ovos.session.sync topic [\#245](https://github.com/OpenVoiceOS/ovos-bus-client/pull/245) ([JarbasAl](https://github.com/JarbasAl))

## [2.5.1a2](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.5.1a2) (2026-06-28)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.5.1a1...2.5.1a2)

**Merged pull requests:**

- test: repoint shape-changing reshape tests off the handler trio [\#243](https://github.com/OpenVoiceOS/ovos-bus-client/pull/243) ([JarbasAl](https://github.com/JarbasAl))

## [2.5.1a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.5.1a1) (2026-06-27)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.5.0a1...2.5.1a1)

**Merged pull requests:**

- fix: translate mirrored payload onto counterpart topic [\#235](https://github.com/OpenVoiceOS/ovos-bus-client/pull/235) ([JarbasAl](https://github.com/JarbasAl))

## [2.5.0a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.5.0a1) (2026-06-27)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.4.1a1...2.5.0a1)

**Merged pull requests:**

- feat: SessionManager owns the ovos.session.sync intent\_context merge \(OVOS-CONTEXT-1 §5.3\) [\#239](https://github.com/OpenVoiceOS/ovos-bus-client/pull/239) ([JarbasAl](https://github.com/JarbasAl))

## [2.4.1a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.4.1a1) (2026-06-27)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.4.0a1...2.4.1a1)

**Merged pull requests:**

- fix: keep an unset site\_id absent instead of fabricating "unknown" \(BRIDGE-1 §3.3\) [\#237](https://github.com/OpenVoiceOS/ovos-bus-client/pull/237) ([JarbasAl](https://github.com/JarbasAl))

## [2.4.0a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.4.0a1) (2026-06-27)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.3.0a2...2.4.0a1)

**Merged pull requests:**

- feat: Session subclasses ovos\_spec\_tools.Session \(canonical SESSION-1\) + back-compat shim [\#234](https://github.com/OpenVoiceOS/ovos-bus-client/pull/234) ([JarbasAl](https://github.com/JarbasAl))

## [2.3.0a2](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.3.0a2) (2026-06-25)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.3.0a1...2.3.0a2)

**Merged pull requests:**

- refactor: share NamespaceTranslator with FakeBus \(drop inline migration logic\) [\#232](https://github.com/OpenVoiceOS/ovos-bus-client/pull/232) ([JarbasAl](https://github.com/JarbasAl))

## [2.3.0a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.3.0a1) (2026-06-25)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.2.0a1...2.3.0a1)

**Merged pull requests:**

- feat: namespace migration via MessageBusClient — both flags on + handler dedup [\#230](https://github.com/OpenVoiceOS/ovos-bus-client/pull/230) ([JarbasAl](https://github.com/JarbasAl))

## [2.2.0a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.2.0a1) (2026-06-25)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.1.2a2...2.2.0a1)

**Merged pull requests:**

- feat: transparent opt-in legacy\<-\>ovos.\* namespace migration in MessageBusClient [\#228](https://github.com/OpenVoiceOS/ovos-bus-client/pull/228) ([JarbasAl](https://github.com/JarbasAl))

## [2.1.2a2](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.1.2a2) (2026-06-25)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.1.2a1...2.1.2a2)

**Merged pull requests:**

- refactor: deprecate EnclosureAPI \(moved to ovos-gui-api-client\) [\#226](https://github.com/OpenVoiceOS/ovos-bus-client/pull/226) ([JarbasAl](https://github.com/JarbasAl))

## [2.1.2a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.1.2a1) (2026-06-23)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.1.1a1...2.1.2a1)

**Merged pull requests:**

- fix: ignore non-exception websocket on\_error callbacks \(\#223\) [\#224](https://github.com/OpenVoiceOS/ovos-bus-client/pull/224) ([JarbasAl](https://github.com/JarbasAl))

## [2.1.1a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.1.1a1) (2026-05-24)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.1.0a1...2.1.1a1)

## [2.1.0a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.1.0a1) (2026-05-24)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.0.0a4...2.1.0a1)

## [2.0.0a4](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.0.0a4) (2026-05-22)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.0.0a3...2.0.0a4)

## [2.0.0a3](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.0.0a3) (2026-05-18)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.0.0a2...2.0.0a3)

## [2.0.0a2](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.0.0a2) (2026-05-18)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.0.0a1...2.0.0a2)

## [2.0.0a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.0.0a1) (2026-05-18)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.5.0...2.0.0a1)

## [1.5.0](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.5.0) (2026-03-02)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.4.0a4...1.5.0)

## [1.4.0a4](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.4.0a4) (2026-03-02)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.4.0...1.4.0a4)

## [1.4.0](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.4.0) (2026-01-23)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.4.0a2...1.4.0)

## [1.4.0a2](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.4.0a2) (2026-01-23)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.4.0a1...1.4.0a2)

## [1.4.0a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.4.0a1) (2026-01-23)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.3.8a5...1.4.0a1)

## [1.3.8a5](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.3.8a5) (2025-12-19)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.3.8a4...1.3.8a5)

## [1.3.8a4](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.3.8a4) (2025-12-19)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.3.8a3...1.3.8a4)

## [1.3.8a3](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.3.8a3) (2025-12-19)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.3.8a2...1.3.8a3)

## [1.3.8a2](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.3.8a2) (2025-12-18)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.3.8a1...1.3.8a2)

## [1.3.8a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.3.8a1) (2025-11-09)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.3.7...1.3.8a1)

## [1.3.7](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.3.7) (2025-11-06)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.3.7a1...1.3.7)

## [1.3.7a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.3.7a1) (2025-11-06)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.3.6a2...1.3.7a1)

## [1.3.6a2](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.3.6a2) (2025-11-06)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.3.6a1...1.3.6a2)

## [1.3.6a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.3.6a1) (2025-09-05)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.3.5a1...1.3.6a1)

## [1.3.5a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.3.5a1) (2025-06-16)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.3.4...1.3.5a1)

## [1.3.4](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.3.4) (2025-04-10)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.3.4a1...1.3.4)

## [1.3.4a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.3.4a1) (2025-04-10)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.3.3a1...1.3.4a1)

## [1.3.3a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.3.3a1) (2025-04-10)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.3.2...1.3.3a1)

## [1.3.2](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.3.2) (2025-01-25)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.3.2a1...1.3.2)

## [1.3.2a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.3.2a1) (2025-01-04)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.3.1...1.3.2a1)

## [1.3.1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.3.1) (2024-12-29)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.3.1a1...1.3.1)

## [1.3.1a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.3.1a1) (2024-12-29)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.3.0...1.3.1a1)

## [1.3.0](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.3.0) (2024-12-28)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.3.0a1...1.3.0)

## [1.3.0a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.3.0a1) (2024-12-28)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.2.0...1.3.0a1)

## [1.2.0](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.2.0) (2024-12-26)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.2.0a1...1.2.0)

## [1.2.0a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.2.0a1) (2024-12-26)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.1.0...1.2.0a1)

## [1.1.0](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.1.0) (2024-12-26)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.1.0a1...1.1.0)

## [1.1.0a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.1.0a1) (2024-12-26)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.0.7a1...1.1.0a1)

## [1.0.7a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.0.7a1) (2024-12-05)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.0.6...1.0.7a1)

## [1.0.6](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.0.6) (2024-11-26)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.0.6a1...1.0.6)

## [1.0.6a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.0.6a1) (2024-11-26)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.0.5a1...1.0.6a1)

## [1.0.5a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.0.5a1) (2024-11-25)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.0.4...1.0.5a1)

## [1.0.4](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.0.4) (2024-11-21)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.0.4a1...1.0.4)

## [1.0.4a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.0.4a1) (2024-11-21)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.0.3...1.0.4a1)

## [1.0.3](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.0.3) (2024-11-21)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.0.3a1...1.0.3)

## [1.0.3a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.0.3a1) (2024-11-21)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.0.2...1.0.3a1)

## [1.0.2](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.0.2) (2024-11-15)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.0.2a1...1.0.2)

## [1.0.2a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.0.2a1) (2024-11-15)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.0.1...1.0.2a1)

## [1.0.1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.0.1) (2024-11-15)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.0.1a1...1.0.1)

## [1.0.1a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.0.1a1) (2024-11-15)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.0.0...1.0.1a1)

## [1.0.0](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.0.0) (2024-11-05)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.0.0a1...1.0.0)

## [1.0.0a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/1.0.0a1) (2024-11-05)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/0.1.6...1.0.0a1)

## [0.1.6](https://github.com/OpenVoiceOS/ovos-bus-client/tree/0.1.6) (2024-10-27)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/0.1.6a1...0.1.6)

## [0.1.6a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/0.1.6a1) (2024-10-27)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/0.1.5...0.1.6a1)

## [0.1.5](https://github.com/OpenVoiceOS/ovos-bus-client/tree/0.1.5) (2024-10-21)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/0.1.5a1...0.1.5)

## [0.1.5a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/0.1.5a1) (2024-10-21)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/0.1.4...0.1.5a1)

## [0.1.4](https://github.com/OpenVoiceOS/ovos-bus-client/tree/0.1.4) (2024-10-16)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/0.1.4a1...0.1.4)

## [0.1.4a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/0.1.4a1) (2024-10-16)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/0.1.3...0.1.4a1)

## [0.1.3](https://github.com/OpenVoiceOS/ovos-bus-client/tree/0.1.3) (2024-10-15)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/0.1.3a1...0.1.3)

## [0.1.3a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/0.1.3a1) (2024-10-15)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/0.1.2a1...0.1.3a1)

## [0.1.2a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/0.1.2a1) (2024-10-15)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/0.1.1...0.1.2a1)

## [0.1.1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/0.1.1) (2024-09-23)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/0.1.1a1...0.1.1)

## [0.1.1a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/0.1.1a1) (2024-09-23)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/0.1.0...0.1.1a1)

## [0.1.0](https://github.com/OpenVoiceOS/ovos-bus-client/tree/0.1.0) (2024-09-17)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/0.1.0a1...0.1.0)

## [0.1.0a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/0.1.0a1) (2024-09-17)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/0.0.10...0.1.0a1)

## [0.0.10](https://github.com/OpenVoiceOS/ovos-bus-client/tree/0.0.10) (2024-09-10)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/0.0.9a2...0.0.10)

## [0.0.9a2](https://github.com/OpenVoiceOS/ovos-bus-client/tree/0.0.9a2) (2024-09-10)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/0.0.9a1...0.0.9a2)

## [0.0.9a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/0.0.9a1) (2024-09-10)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.9...0.0.9a1)

## [V0.0.9](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.9) (2024-09-10)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V...V0.0.9)

## [V](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V) (2024-03-10)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.9a12...V)

## [V0.0.9a12](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.9a12) (2024-02-21)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.9a11...V0.0.9a12)

## [V0.0.9a11](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.9a11) (2024-02-02)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.9a10...V0.0.9a11)

## [V0.0.9a10](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.9a10) (2024-01-29)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.9a9...V0.0.9a10)

## [V0.0.9a9](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.9a9) (2024-01-23)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.9a8...V0.0.9a9)

## [V0.0.9a8](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.9a8) (2024-01-13)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.9a7...V0.0.9a8)

## [V0.0.9a7](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.9a7) (2024-01-13)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.9a6...V0.0.9a7)

## [V0.0.9a6](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.9a6) (2024-01-12)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.9a5...V0.0.9a6)

## [V0.0.9a5](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.9a5) (2024-01-12)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.9a4...V0.0.9a5)

## [V0.0.9a4](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.9a4) (2024-01-09)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.9a3...V0.0.9a4)

## [V0.0.9a3](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.9a3) (2024-01-08)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.9a2...V0.0.9a3)

## [V0.0.9a2](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.9a2) (2024-01-06)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.9a1...V0.0.9a2)

## [V0.0.9a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.9a1) (2023-12-30)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.8...V0.0.9a1)

## [V0.0.8](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.8) (2023-12-29)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.8a2...V0.0.8)

## [V0.0.8a2](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.8a2) (2023-12-29)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.8a1...V0.0.8a2)

## [V0.0.8a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.8a1) (2023-12-29)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.7...V0.0.8a1)

## [V0.0.7](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.7) (2023-12-29)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.7a1...V0.0.7)

## [V0.0.7a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.7a1) (2023-12-29)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a24...V0.0.7a1)

## [V0.0.6a24](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a24) (2023-12-28)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a23...V0.0.6a24)

## [V0.0.6a23](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a23) (2023-12-28)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a22...V0.0.6a23)

## [V0.0.6a22](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a22) (2023-12-28)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a21...V0.0.6a22)

## [V0.0.6a21](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a21) (2023-12-14)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a20...V0.0.6a21)

## [V0.0.6a20](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a20) (2023-12-08)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a19...V0.0.6a20)

## [V0.0.6a19](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a19) (2023-10-25)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a18...V0.0.6a19)

## [V0.0.6a18](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a18) (2023-10-24)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a17...V0.0.6a18)

## [V0.0.6a17](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a17) (2023-10-24)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a16...V0.0.6a17)

## [V0.0.6a16](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a16) (2023-10-23)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a15...V0.0.6a16)

## [V0.0.6a15](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a15) (2023-10-23)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a14...V0.0.6a15)

## [V0.0.6a14](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a14) (2023-10-16)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a13...V0.0.6a14)

## [V0.0.6a13](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a13) (2023-10-09)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a12...V0.0.6a13)

## [V0.0.6a12](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a12) (2023-10-04)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a11...V0.0.6a12)

## [V0.0.6a11](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a11) (2023-10-03)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a10...V0.0.6a11)

## [V0.0.6a10](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a10) (2023-10-03)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a9...V0.0.6a10)

## [V0.0.6a9](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a9) (2023-09-30)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a8...V0.0.6a9)

## [V0.0.6a8](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a8) (2023-09-29)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a7...V0.0.6a8)

## [V0.0.6a7](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a7) (2023-09-22)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a6...V0.0.6a7)

## [V0.0.6a6](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a6) (2023-09-14)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a5...V0.0.6a6)

## [V0.0.6a5](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a5) (2023-09-12)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a4...V0.0.6a5)

## [V0.0.6a4](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a4) (2023-08-29)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a3...V0.0.6a4)

## [V0.0.6a3](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a3) (2023-08-07)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a2...V0.0.6a3)

## [V0.0.6a2](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a2) (2023-08-04)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.6a1...V0.0.6a2)

## [V0.0.6a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.6a1) (2023-08-01)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.5...V0.0.6a1)

## [V0.0.5](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.5) (2023-07-04)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.5a2...V0.0.5)

## [V0.0.5a2](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.5a2) (2023-06-27)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.5a1...V0.0.5a2)

## [V0.0.5a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.5a1) (2023-06-21)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.4...V0.0.5a1)

## [V0.0.4](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.4) (2023-06-16)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.4a17...V0.0.4)

## [V0.0.4a17](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.4a17) (2023-06-15)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.4a16...V0.0.4a17)

## [V0.0.4a16](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.4a16) (2023-06-15)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.4a15...V0.0.4a16)

## [V0.0.4a15](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.4a15) (2023-06-14)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.4a14...V0.0.4a15)

## [V0.0.4a14](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.4a14) (2023-06-10)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.4a13...V0.0.4a14)

## [V0.0.4a13](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.4a13) (2023-06-09)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.4a12...V0.0.4a13)

## [V0.0.4a12](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.4a12) (2023-05-21)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.4a11...V0.0.4a12)

## [V0.0.4a11](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.4a11) (2023-05-19)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.4a10...V0.0.4a11)

## [V0.0.4a10](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.4a10) (2023-05-10)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.4a9...V0.0.4a10)

## [V0.0.4a9](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.4a9) (2023-05-06)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.4a8...V0.0.4a9)

## [V0.0.4a8](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.4a8) (2023-05-05)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.4a7...V0.0.4a8)

## [V0.0.4a7](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.4a7) (2023-05-05)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.4a6...V0.0.4a7)

## [V0.0.4a6](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.4a6) (2023-04-30)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.4a5...V0.0.4a6)

## [V0.0.4a5](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.4a5) (2023-04-27)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.4a4...V0.0.4a5)

## [V0.0.4a4](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.4a4) (2023-04-27)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.4a3...V0.0.4a4)

## [V0.0.4a3](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.4a3) (2023-04-22)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.4a2...V0.0.4a3)

## [V0.0.4a2](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.4a2) (2023-04-22)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.4a1...V0.0.4a2)

## [V0.0.4a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.4a1) (2023-04-22)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.3...V0.0.4a1)

## [V0.0.3](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.3) (2023-04-19)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.3a22...V0.0.3)

## [V0.0.3a22](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.3a22) (2023-04-19)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.3a21...V0.0.3a22)

## [V0.0.3a21](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.3a21) (2023-04-18)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.3a19...V0.0.3a21)

## [V0.0.3a19](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.3a19) (2023-04-15)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.3a18...V0.0.3a19)

## [V0.0.3a18](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.3a18) (2023-04-12)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.3a17...V0.0.3a18)

## [V0.0.3a17](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.3a17) (2023-04-11)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.3a16...V0.0.3a17)

## [V0.0.3a16](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.3a16) (2023-04-09)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.3a15...V0.0.3a16)

## [V0.0.3a15](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.3a15) (2023-04-08)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.3a14...V0.0.3a15)

## [V0.0.3a14](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.3a14) (2023-04-08)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.3a13...V0.0.3a14)

## [V0.0.3a13](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.3a13) (2023-04-08)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.3a12...V0.0.3a13)

## [V0.0.3a12](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.3a12) (2023-04-08)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.3a11...V0.0.3a12)

## [V0.0.3a11](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.3a11) (2023-04-08)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.3a10...V0.0.3a11)

## [V0.0.3a10](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.3a10) (2023-04-07)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.3a9...V0.0.3a10)

## [V0.0.3a9](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.3a9) (2023-04-05)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.3a8...V0.0.3a9)

## [V0.0.3a8](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.3a8) (2023-04-05)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.3a7...V0.0.3a8)

## [V0.0.3a7](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.3a7) (2023-04-05)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.3a6...V0.0.3a7)

## [V0.0.3a6](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.3a6) (2023-04-05)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.3a5...V0.0.3a6)

## [V0.0.3a5](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.3a5) (2023-04-05)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/V0.0.3a4...V0.0.3a5)

## [V0.0.3a4](https://github.com/OpenVoiceOS/ovos-bus-client/tree/V0.0.3a4) (2023-04-05)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/240929a2eb70a305f6622070e6c953ec32986565...V0.0.3a4)



\* *This Changelog was automatically generated by [github_changelog_generator](https://github.com/github-changelog-generator/github-changelog-generator)*
