# Session and SessionManager

Session state is the primary mechanism by which OVOS tracks per-user, per-device conversation context across the intent pipeline, skills, chat agents, and HiveMind agents.

## Session

`Session` — `ovos_bus_client/session.py:263`

Each `Session` holds:

| Attribute | Type | Description |
|---|---|---|
| `session_id` | `str` | UUID; `"default"` is the reserved in-process session |
| `lang` | `str` | BCP-47 language tag (standardised on assignment) |
| `pipeline` | `List[str]` | Ordered intent pipeline stage identifiers |
| `active_skills` | `List[List]` | `[skill_id, last_touch_timestamp]` pairs |
| `utterance_states` | `Dict[str, UtteranceState]` | Per-skill `INTENT` or `RESPONSE` state |
| `context` | `IntentContextManager` | Conversational entity/frame stack |
| `site_id` | `str` | Physical location identifier |
| `is_speaking` | `bool` | Audio output active flag |
| `is_recording` | `bool` | Microphone active flag |
| `blacklisted_skills` | `List[str]` | Skills excluded for this session |
| `blacklisted_intents` | `List[str]` | Intents excluded for this session |
| `persona_id` | `Optional[str]` | Persona override for this session |
| `expiration_seconds` | `int` | TTL; `-1` means never expires |

Sessions serialize to/from plain dicts via `Session.serialize()` / `Session.deserialize()` — `ovos_bus_client/session.py:441,493`. They are carried inside `message.context["session"]` on every bus message.

### Omitted and empty override fields

On the list-valued override fields — `pipeline`, the three `blacklisted_*`, and the
`*_transformers` chains — an empty list is wire-equivalent to an absent key
(OVOS-SESSION-1 §3.4). Both mean *"let the orchestrator decide"*, and a consumer
resolves them to its own deployment default from `ovos-config`. An explicit `null` is
malformed and is treated as absent. So `blacklisted_intents: []` does not assert
"this session blacklists nothing"; to do that, the deployment default must itself be
empty.

§3.4 states the omit-when-empty rule as a SHOULD, not a MUST — emitting a field that
already holds the deployment default is conformant, and consumers must tolerate it.
`Session.serialize()` therefore always emits `pipeline`, `blacklisted_skills` and
`blacklisted_intents` carrying their config-resolved values, so readers indexing the
raw dict always find a concrete list rather than having to re-derive the default.

### Session.from_message

`Session.from_message(message)` — `ovos_bus_client/session.py:537`

The canonical way to obtain a `Session` from an incoming bus message. It:

1. Reads `message.context["session"]` and deserialises it.
2. Merges any top-level `lang` key from `message.context` or `message.data` if absent from the session dict.
3. Falls back to `SessionManager.default_session` when no session context exists.

```python
from ovos_bus_client.session import Session

def handle_utterance(message):
    sess = Session.from_message(message)
    print(sess.session_id, sess.lang)
```

## SessionManager

`SessionManager` — `ovos_bus_client/session.py:568`

An in-process, class-level registry (`SessionManager.sessions: Dict[str, Session]`). Downstream consumers must not persist sessions independently — always go through `SessionManager` so that state remains consistent within the process.

### get

`SessionManager.get(message)` — `ovos_bus_client/session.py:638`

Returns the `Session` for a message, registering it in the sessions dict if its `session_id` is not `"default"`. Falls back to `default_session` when no message or no session context is available.

```python
from ovos_bus_client.session import SessionManager

sess = SessionManager.get(message)
```

### update

`SessionManager.update(sess, make_default=False)` — `ovos_bus_client/session.py:618`

Writes a session back into the registry. Pass `make_default=True` to promote it to the default session (also forces `session_id = "default"`).

```python
sess = SessionManager.get(message)
sess.lang = "pt-pt"
SessionManager.update(sess)
```

### Bus synchronisation

`SessionManager.connect_to_bus(bus)` — `ovos_bus_client/session.py:583` — registers listeners for `recognizer_loop:*` and `ovos.session.*` events and immediately pushes the current default session to `ovos-core` via `ovos.session.update_default`.

## Usage pattern for chat agents and HiveMind agents

The pattern expected by downstream consumers (e.g. `ovos-messagebus-chat-plugin`, `hivemind-ovos-agent-plugin`):

1. Receive an external request (HTTP / HiveMind message).
2. Look up or create a `Session` keyed by `session_id` from the external client.
3. Inject it into `message.context["session"]` before emitting to the bus.
4. After the pipeline completes, call `SessionManager.get(reply_message)` to retrieve the updated session and persist it back to the external client context.

This ensures multi-turn state (active skills, converse queue, context frames) is preserved correctly across turns without sharing state between unrelated callers.
