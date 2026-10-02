# AGENTS.md

Conventions for AI coding agents (internal and community) working in this
repository.

## What this repo is

`ovos-bus-client` is the Python client for the OVOS messagebus, the
websocket bus that every OVOS component (STT, intent parsing, skills, TTS,
audio, GUI) uses to talk to each other. It defines the `Message` envelope,
the `MessageBusClient`, and the `Session` object that travels on every
message.

`ovos-core`, `ovos-workshop`, and effectively every skill and plugin in the
ecosystem depend on it. It is one of the lowest-level, most widely consumed
packages in the stack, so a change here has ecosystem-wide blast radius.

The bus has no authentication. This client assumes it is bound to
`127.0.0.1`, and code added here should not encourage exposing it on a
network interface.

## Ground rules

- Work on a feature branch. Never push to `dev` or `master` directly.
- Open pull requests against `dev` as **drafts** until CI is green and the
  change is ready for review.
- One commit per PR. Squash before pushing if history accumulates.

- Use conventional commit prefixes (`fix:`, `feat:`, `refactor:`, `docs:`,
  `test:`, `chore:`). Reserve `feat:` for changes a user or downstream
  consumer can actually observe.
- Never hand-edit `version.py`. CI computes and bumps the version from
  conventional commit history.

- Every PR description and issue you write or edit carries an AI-authorship
  disclosure at the top, naming the exact model used, and states the text is
  not human-reviewed.

## Dependencies

- Use `uv`, never `pip`, for installing and resolving dependencies.
- Pin floors only, and always allow prereleases: `>=X.Y.Za1`, matching the
  existing `ovos-spec-tools[langcodes]>=1.7.0a1` style in `pyproject.toml`.

- All dependency and metadata declarations live in `pyproject.toml`.
- Never install a dependency from a git URL. Publish an alpha to PyPI and
  depend on that.

- This package has four console-script entry points (`ovos-listen`,
  `ovos-speak`, `ovos-say-to`, `ovos-simple-cli`, all in
  `ovos_bus_client/scripts.py`). Keep them working when touching the
  client's public connect/send/wait API.

## Testing

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -e ".[test]"
pytest test/
```

`[tool.coverage.run]` in `pyproject.toml` already excludes `version.py` from
coverage. Do not add coverage exclusions elsewhere without a stated reason.

A regression test for a bug must be shown to fail against the code before the
fix and pass after it. A test that passes against unfixed code proves
nothing and does not satisfy this gate.

## Docs discipline

Any change that touches observable behavior updates `README.md` and the
relevant file under `docs/` (`client.md`, `messages.md`, `session.md`,
`patterns.md`, `configuration.md`, `namespace-migration.md`,
`waiter_and_collector.md`, `scripts.md`) in the same PR.

Also add a version-stamped entry at the top of `docs/prerelease-quirks.md`
describing the change (create the file if it does not exist yet), newest
entry first.

## Repo-specific notes

- `docs/namespace-migration.md` documents a real migration in this codebase.
  Older message-type and session-field names are translated to current
  ones for backward compatibility. When adding a new migration mapping,
  extend the existing translation path rather than special-casing it, and
  update `namespace-migration.md` in the same PR.

- `message.py` deliberately keeps legacy behavior in a few places, called
  out in comments. `Message.publish()` is a legacy convenience method
  attached onto the spec-tools `Message`, encryption helpers
  (`encrypt_as_dict`/decrypt) accept a legacy web-crypto tag format as a
  fallback, and `reply()` intentionally preserves an older return-type
  behavior.

  These exist for wire and API back-compat with deployed satellites. Do
  not "clean them up" without confirming nothing downstream still relies
  on the old shape.

- `test/` (singular) is the test directory.
