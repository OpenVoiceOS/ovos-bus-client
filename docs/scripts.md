# CLI tools

`ovos-bus-client` installs four small CLI utilities. Each opens a transient
`MessageBusClient`, emits one or two messages, and exits. They are useful for
sanity-checking a running OVOS install and for shell-level scripting.

Source: `ovos_bus_client/scripts.py`.

## `ovos-speak`

Synthesise speech via OVOS TTS.

```bash
ovos-speak "hello there"
ovos-speak "hej" sv-se
```

Emits `Message("speak", {"utterance": ..., "lang": ...})`. If `lang` is
omitted, falls back to the `lang` key in your `ovos-config` config (defaults
to `en-us`).

## `ovos-say-to`

Inject an utterance as if it had come from the microphone. Triggers the full
intent pipeline.

```bash
ovos-say-to "what time is it"
ovos-say-to "que horas são" pt-pt
```

Emits `Message("recognizer_loop:utterance", {"utterances": [...], "lang": ...})`.
The same message type STT would emit on a successful recognition.

## `ovos-listen`

Tell OVOS to start listening as if you had said the wake word.

```bash
ovos-listen
```

Emits `Message("mycroft.mic.listen")`. Useful for kiosk integrations where a
physical button should trigger listening.

## `ovos-simple-cli`

A minimal REPL: type lines, each gets emitted as a `recognizer_loop:utterance`.
Type `:exit` (or hit Ctrl-C) to quit.

```bash
ovos-simple-cli
ovos-simple-cli pt-pt    # set the lang once for the whole session
```

It uses `SessionManager.default_session` so every utterance carries a stable
`session_id` — handy for testing multi-turn skill behaviour from the shell.

## Implementation notes

Each script:

1. Constructs a `MessageBusClient` with default config.
2. `run_in_thread()` and `connected_event.wait()`.
3. Emits its one or two messages.
4. `time.sleep(0.5)` — avoids a documented crash in the C++ bus server when
   the client closes too quickly after `emit`.
5. `bus.close()`.

If you are scripting OVOS and need anything more sophisticated than these
four scripts, use the `send()` helper for one-shot fire-and-forget or build a
full `MessageBusClient` for anything interactive.

## Adding your own CLI tool

Register it under `console_scripts` in your own `setup.py` / `pyproject.toml`,
not in `ovos-bus-client`. The four built-in scripts here are deliberately
generic; anything domain-specific belongs in its own package.

Convention:

```toml
[project.scripts]
my-cli = "my_package.scripts:my_cli"
```

Inside, follow the same pattern: build a client, wait for connect, emit, sleep
briefly, close.
