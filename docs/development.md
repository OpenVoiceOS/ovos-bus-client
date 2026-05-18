# Development

Working on `ovos-bus-client` itself, or hacking on a fork.

## Repository layout

```
ovos_bus_client/
├── __init__.py          # public re-exports: MessageBusClient, Message, Session, ...
├── client/
│   ├── client.py        # MessageBusClient, GUIWebsocketClient
│   ├── collector.py     # MessageCollector — multi-handler collect-call
│   ├── waiter.py        # MessageWaiter — single-reply waiter
│   └── events.py        # internal emitter
├── message.py           # Message, GUIMessage, CollectionMessage, dig_for_message,
│                        #   encrypt_as_dict, decrypt_from_dict
├── session.py           # Session, SessionManager, IntentContextManager,
│                        #   IntentContextManagerFrame, UtteranceState
├── send_func.py         # send() one-shot helper
├── conf.py              # load_message_bus_config, client_from_config
├── scripts.py           # CLI entry points (ovos-speak, ovos-listen, ...)
├── apis/
│   ├── gui.py           # GUIInterface — skill GUI API
│   ├── ocp.py           # OCPInterface, ClassicAudioServiceInterface
│   ├── enclosure.py     # EnclosureAPI — Mark 1/2 hardware
│   └── events.py        # EventSchedulerInterface
├── util/
│   └── scheduler.py     # legacy scheduler utilities
└── version.py           # VERSION_* constants, parsed by setup.py
test/
└── unittests/           # pytest tests
docs/                    # this folder
```

## Running tests

```bash
pip install -e .
pip install -r test/requirements.txt   # or .[test] if you prefer the extra
pytest test/unittests -v
```

CI runs the same command across Python 3.10 – 3.14 via the
`OpenVoiceOS/gh-automations/.github/workflows/build-tests.yml@dev` reusable
workflow.

## Entry points (2.0)

Only CLI scripts are registered by this package:

| Entry point        | Function                                |
|--------------------|-----------------------------------------|
| `ovos-listen`      | `ovos_bus_client.scripts:ovos_listen`   |
| `ovos-speak`       | `ovos_bus_client.scripts:ovos_speak`    |
| `ovos-say-to`      | `ovos_bus_client.scripts:ovos_say_to`   |
| `ovos-simple-cli`  | `ovos_bus_client.scripts:simple_cli`    |

The `hivemind.agent.protocol` and `neon.plugin.solver` groups are no longer
registered here — they moved to their own packages in 2.0. See
[Migration](migration.md).

## Versioning

`ovos_bus_client/version.py` carries the four `VERSION_*` constants. The
publish workflows in `.github/workflows/` bump them automatically:

- `release_workflow.yml` (alpha): bumps `VERSION_ALPHA` on every merge to
  `dev`, publishes to PyPI, and proposes a stable-release PR `dev → master`.
- `publish_stable.yml`: on push to `master`, sets `VERSION_ALPHA=0`,
  publishes to PyPI, and syncs the bump back to `dev`.

Branch model: `dev` is active development. `master` carries stable releases.
PRs target `dev`.

## Conventional commits

Commit titles follow [Conventional Commits](https://www.conventionalcommits.org/).
The label workflow auto-labels PRs based on the title, and the release-preview
workflow predicts the resulting version bump:

| Prefix      | Bump  | Notes                                           |
|-------------|-------|-------------------------------------------------|
| `fix:`      | patch |                                                 |
| `feat:`     | minor |                                                 |
| `feat!:`    | major | Trailing `!` means breaking change.             |
| `chore:` `docs:` `ci:` | none  | Won't trigger a release. |

## Code style

- Apache 2.0 licensed; preserve the existing top-of-file headers.
- Python ≥ 3.10. Type hints encouraged on new public surface; not enforced.
- `ruff` is run by the lint workflow but is informational only.

## Adding new functionality

This package is **foundational**. Things that should go here:

- New helpers on `MessageBusClient`, `Message`, `Session`.
- New `apis/` wrappers — but only if they're generally useful, not
  domain-specific.

Things that should **not** go here:

- Domain-specific message vocabularies — those belong in the package that
  owns the vocabulary.
- Plugin-manager entry points other than core CLI scripts. The previous
  `hpm.py` (HiveMind) and `opm.py` (solver) were removed in 2.0 because
  registering plugins under foreign plugin-manager groups inverted the
  dependency direction.
- Anything pulling in `hivemind-*`, `ovos-plugin-manager`, or any other
  higher-layer package as a dependency. Lower-layer libs do not import their
  consumers.

When in doubt: would `ovos-core` and every skill have to grow this dependency
to import `ovos-bus-client`? If yes, the new code belongs elsewhere.

## Logging

Uses `ovos_utils.log.LOG`. Adopt the same logger rather than `logging`
directly so users get the unified OVOS log format.

## Releasing

You almost never do this manually. Merge your PR to `dev`, the release
workflow takes it from there. To force a release without merging, run the
"Release Alpha and Propose Stable" workflow via the GitHub Actions UI
(`workflow_dispatch`).
