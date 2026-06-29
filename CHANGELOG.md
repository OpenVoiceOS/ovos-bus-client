# Changelog

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

**Merged pull requests:**

- fix: restore legacy AES encryption at the websocket transport edge [\#218](https://github.com/OpenVoiceOS/ovos-bus-client/pull/218) ([JarbasAl](https://github.com/JarbasAl))

## [2.1.0a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.1.0a1) (2026-05-24)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.0.0a4...2.1.0a1)

**Merged pull requests:**

- feat: Message subclasses ovos\_spec\_tools.Message — no API break [\#215](https://github.com/OpenVoiceOS/ovos-bus-client/pull/215) ([JarbasAl](https://github.com/JarbasAl))

## [2.0.0a4](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.0.0a4) (2026-05-22)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.0.0a3...2.0.0a4)

**Merged pull requests:**

- refactor: migrate language matching to ovos-spec-tools [\#213](https://github.com/OpenVoiceOS/ovos-bus-client/pull/213) ([JarbasAl](https://github.com/JarbasAl))

## [2.0.0a3](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.0.0a3) (2026-05-18)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.0.0a2...2.0.0a3)

**Merged pull requests:**

- test: comprehensive test coverage \(19% → 93% real / 67% pytest-cov\) [\#211](https://github.com/OpenVoiceOS/ovos-bus-client/pull/211) ([JarbasAl](https://github.com/JarbasAl))

## [2.0.0a2](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.0.0a2) (2026-05-18)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/2.0.0a1...2.0.0a2)

**Merged pull requests:**

- docs+ci: modernize after 2.0 cleanup [\#209](https://github.com/OpenVoiceOS/ovos-bus-client/pull/209) ([JarbasAl](https://github.com/JarbasAl))

## [2.0.0a1](https://github.com/OpenVoiceOS/ovos-bus-client/tree/2.0.0a1) (2026-05-18)

[Full Changelog](https://github.com/OpenVoiceOS/ovos-bus-client/compare/1.5.0...2.0.0a1)

**Breaking changes:**

- remove!: hivemind agent protocol and messagebus solver [\#207](https://github.com/OpenVoiceOS/ovos-bus-client/pull/207) ([JarbasAl](https://github.com/JarbasAl))



\* *This Changelog was automatically generated by [github_changelog_generator](https://github.com/github-changelog-generator/github-changelog-generator)*
