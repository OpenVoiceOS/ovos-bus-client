# Glossary

Terms you will encounter reading OVOS code and these docs.

| Term | Meaning |
|---|---|
| **Bus** / **messagebus** | The OVOS WebSocket pub/sub server. The "nervous system" of an OVOS install. Internal, no auth — keep it bound to localhost. |
| **`MessageBusClient`** | Python WebSocket client for the bus. The class this package exists to ship. |
| **`FakeBus`** | In-memory `MessageBusClient` lookalike shipped in `ovos-utils`. Use it for tests. |
| **`Message`** | A bus message: `(msg_type, data, context)`. JSON-serialisable. |
| **`ovos-pydantic-models`** | Authoritative Pydantic v2 index of every known OVOS bus message type. Not a runtime dependency of `ovos-bus-client`; opt-in for validation. [Repo](https://github.com/OpenVoiceOS/ovos-pydantic-models) · [Docs](https://openvoiceos.github.io/ovos-pydantic-models/). |
| **`msg_type`** | The event name. Dot- or colon-delimited by convention. |
| **`data`** | Payload of a message. Producer-defined schema. |
| **`context`** | Routing/metadata: session, source, destination, etc. Carries the session. |
| **`Session`** | Per-user / per-device state: lang, active skills, intent context, etc. Lives in `context["session"]`. |
| **`session_id`** | UUID identifying a `Session`. `"default"` is the reserved in-process session. |
| **`SessionManager`** | Class-level singleton; the canonical `session_id → Session` map for the process. |
| **`IntentContextManager`** | The conversational entity/frame stack inside a `Session`. Drives multi-turn skills. |
| **`UtteranceState`** | Per-skill flag inside a session: `INTENT` or `RESPONSE`. `RESPONSE` means the skill is waiting on `get_response`. |
| **HiveMind** | External-access layer in front of the bus. Adds encryption, auth (by `api_key`), and policy plugins. The right tool for remote clients. |
| **Hive client** | A device or app connected to the bus *via HiveMind*. Identified by `api_key`. |
| **OCP** | OVOS Common Playback. The media-player subsystem. `OCPInterface` is its API. |
| **OPM** | OVOS Plugin Manager. Provides plugin discovery via entry-point groups like `opm.agents.chat`. |
| **OPM agent plugin** | A plugin under `opm.agents.*`. The modern replacement for the deprecated solver plugins (`opm.solver.*`). |
| **OPM solver plugin** | Deprecated single-shot Q&A plugin under `opm.solver.*`. Replaced by agent plugins (`opm.agents.chat`, etc.). |
| **`ChatEngine`** | Base class for `opm.agents.chat` plugins. Implements `continue_chat(messages, session_id, lang, units)`. |
| **`AgentMemory`** | Base class for `opm.agents.memory` plugins. Augments `messages` before a `ChatEngine` sees them. |
| **GUI bus** | A second WebSocket bus dedicated to OVOS GUI traffic. Different port (typically 18181). Uses `GUIWebsocketClient` and `GUIMessage`. |
| **Skill** | A package implementing a Mycroft / OVOS skill: intents, dialogues, optional GUI. Inherits from `MycroftSkill` (in `ovos-workshop`). |
| **Persona** | A named bundle of agent backends and prompts. Lives in `ovos-persona`. Routed to via the persona pipeline stage. |
| **Pipeline** | The ordered list of intent-resolution stages run on each utterance: padatious, adapt, common_query, fallback, persona, etc. Configurable per session. |
| **Common Query (`question:query`)** | The OVOS multi-handler Q&A protocol. Several skills may answer; the highest-confidence wins. Implemented via `collect_responses`. |
| **`recognizer_loop:utterance`** | The bus message that kicks off intent processing. Emitted by STT (or anything pretending to be STT). |
| **`speak`** | The bus message that produces speech. The data carries `utterance` and `lang`. |
| **`.response` suffix** | Reply-type convention used by `wait_for_response`. If you query `foo.bar`, the reply is `foo.bar.response`. |
| **`.handling` suffix** | Extend-timeout ack convention used by `collect_responses`. Handlers emit `<type>.handling` to register, optionally with a longer timeout. |
| **`source` / `destination`** | Routing keys in `context`. Used by relay layers (HiveMind, multi-bus bridges) to know where a message came from and where it should go next. |
| **`dig_for_message`** | Walks the call stack looking for a local variable named `message` of type `Message`. Last-resort way for nested helpers to find their current message. |
