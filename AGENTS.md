# ovos-bus-client

Python client for the OVOS messagebus: websocket transport, session manager, and high-level APIs (GUI, OCP, Enclosure, event scheduler) on top of the OVOS-MSG-1 Message-Object spec.

## Setup

```bash
pip install -e .[test]
```

Requires Python >=3.10. Core deps: `ovos-config`, `ovos-utils`, `ovos-spec-tools[langcodes]`, `websocket-client`, `pyee`.

## Test

```bash
pytest test/unittests
```

Coverage (mirrors CI floor of 65% as reported by pytest-cov):

```bash
coverage run -m pytest test/unittests && coverage report
```

## Lint

```bash
ruff check ovos_bus_client
```

CI runs `ruff` (pre-commit disabled).

## Layout

- `ovos_bus_client/__init__.py` — public surface: `MessageBusClient`, `GUIWebsocketClient`, `Message`, `GUIMessage`, `send`, `client_from_config`, `Session`, `SessionManager`, `UtteranceState`.
- `client/` — `client.py` (`MessageBusClient`, `GUIWebsocketClient`), `collector.py` (`MessageCollector`, multi-reply gather), `waiter.py` (`MessageWaiter`, request/response).
- `message.py` — re-exports `Message` from `ovos-spec-tools`; adds `GUIMessage`, `CollectionMessage`, `dig_for_message`, AES encrypt/decrypt helpers.
- `session.py` — `Session`, `SessionManager`, `IntentContextManager`, `UtteranceState`.
- `apis/` — high-level interfaces: `gui.py` (`GUIInterface`), `ocp.py` (`OCPInterface`/OCP query), `enclosure.py` (`EnclosureAPI`), `events.py` (event API).
- `util/scheduler.py` — event scheduler.
- `conf.py` — config loading (`client_from_config`, `load_message_bus_config`).
- `scripts.py` — CLI entry points.
- `send_func.py` — one-shot `send`.

Console scripts (`[project.scripts]`): `ovos-listen`, `ovos-speak`, `ovos-say-to`, `ovos-simple-cli`. Not a plugin — no OPM/skill entry-point group; no opm-check/skill-check needed.

## Conventions

- Branches: work on `dev`, stable on `master`. NEVER `main`.
- Never edit `ovos_bus_client/version.py`; gh-automations bumps semver from conventional-commit prefixes (`feat:`/`fix:`/`feat!:`).
- New repos private by default.
- Commit identity: JarbasAi <jarbasai@mailfence.com>.
- Reference OpenVoiceOS/gh-automations reusable workflows at `@dev`.
- No Neon / `neon-*` references.
- No meta-commentary (no history, dates, or design-mistake narration) in docs, commits, or code.
- CI is provided by OpenVoiceOS/gh-automations.

## Gotchas

- The bus has NO authentication. Keep it on `127.0.0.1`; for remote access use HiveMind, never expose the raw bus.
- `Message` is re-exported from `ovos-spec-tools`; the envelope/routing/derivation semantics live there, not in this repo.
- 2.0 removed `ovos_bus_client.hpm.OVOSProtocol` (now `hivemind-ovos-agent-plugin`) and `ovos_bus_client.opm.OVOSMessagebusSolver` (now `ovos-messagebus-chat-plugin`); related entry points are no longer registered here.
- Transport-edge AES encryption (`websocket.secret_key`) is deprecated and fires a `DeprecationWarning`; its key-setup half was never completed.
- Coverage floor (65%) tracks pytest-cov's under-report of import-time lines, not real coverage (~93%).
