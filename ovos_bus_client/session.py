import enum
import time
from threading import Event
from typing import Optional, List, Tuple, Union, Iterable, Dict, Any
from uuid import uuid4

from ovos_config.config import Configuration
from ovos_config.locale import get_default_lang
from ovos_utils.log import LOG, log_deprecation
from ovos_spec_tools import standardize_lang, SpecMessage
from ovos_spec_tools.context import resolve_key
from ovos_spec_tools.session import (Session as _SpecSession,
                                     SessionManager as _SpecSessionManager,
                                     DEFAULT_CONVERSE_HANDLERS_CAP,
                                     DEFAULT_SESSION_ID,
                                     SESSION1_REGISTERED_FIELDS)
from ovos_bus_client.message import dig_for_message, Message
from ovos_bus_client.version import VERSION_MAJOR

# Deprecations are removed at the next major bump. Derive the version from
# version.py so the warning text can never drift out of date.
_NEXT_MAJOR_VERSION = f"{VERSION_MAJOR + 1}.0.0"

# Bidirectional-wire back-compat: the canonical parent applies SESSION-1 §2.1
# omit-when-empty semantics and stores an *empty* collection field as ``None``.
# On the wire that is equivalent to omission, but in-process it breaks the
# public contract that these fields are always iterable containers — consumers
# do ``intent in session.blacklisted_intents`` and a ``None`` raises
# ``TypeError: argument of type 'NoneType' is not iterable``. These fields are
# therefore folded back to their canonical empty container after construction
# so they ALWAYS deserialize to ``[]`` / ``{}``, never ``None``. Serialization
# omits them when empty (``to_dict`` drops falsy values), except for
# _ALWAYS_EMIT_LIST_FIELDS below, which are emitted carrying their
# config-resolved value. Scalar fields (``site_id``, ``persona_id``, the per-channel
# language overrides) and the single-object ``response_mode`` legitimately stay
# ``None`` and are intentionally excluded.
_CANONICAL_LIST_FIELDS = (
    "secondary_langs",
    "pipeline",
    "blacklisted_skills",
    "blacklisted_intents",
    "blacklisted_pipelines",
    "audio_transformers",
    "utterance_transformers",
    "metadata_transformers",
    "intent_transformers",
    "dialog_transformers",
    "tts_transformers",
    "blacklisted_audio_transformers",
    "blacklisted_utterance_transformers",
    "blacklisted_metadata_transformers",
    "blacklisted_intent_transformers",
    "blacklisted_dialog_transformers",
    "blacklisted_tts_transformers",
    "fallback_handlers",
    "active_handlers",
    "converse_handlers",
)
_CANONICAL_DICT_FIELDS = (
    "intent_context",
)

# SESSION-1 §3.4 states omit-when-wire-equivalent-to-omission as a SHOULD, not a
# MUST: emitting a redundant default-valued field is non-optimal but conformant,
# and a consumer MUST tolerate it. These three are already resolved from
# ovos-config at construction, so emit the resolved value rather than drop the
# key — a reader then always finds a concrete list instead of re-deriving the
# deployment default itself, and legacy readers indexing the raw dict keep
# working. Resolving an absent value is the reader's job (see the fold above);
# handing it a populated one is ours. Fields with no config default
# (``active_handlers``, ``converse_handlers``, …) keep the omit-when-empty shape.
_ALWAYS_EMIT_LIST_FIELDS = (
    "blacklisted_skills",
    "blacklisted_intents",
    "pipeline",
)


class UtteranceState(str, enum.Enum):
    INTENT = "intent"  # includes converse
    RESPONSE = "response"


class _UtteranceStatesView(dict):
    """Write-through back-compat view of the structured ``response_mode``.

    Legacy callers across the ecosystem treated ``Session.utterance_states`` as a
    plain mutable ``{skill_id: UtteranceState}`` dict, e.g.
    ``session.utterance_states[skill_id] = UtteranceState.RESPONSE`` to put a skill
    in response state, or ``del session.utterance_states[skill_id]`` to clear it.

    The canonical store is now the single-holder ``response_mode`` window
    (OVOS-CONVERSE-1 §2.2). This view is seeded with the current projection so
    reads behave exactly like the old dict, and in-place mutations are forwarded
    to the owning session's ``enable_response_mode`` / ``disable_response_mode``
    so they are not silently lost.
    """

    def __init__(self, session: "Session", initial: Dict):
        super().__init__(initial)
        self._session = session

    @staticmethod
    def _warn():
        log_deprecation("Session.utterance_states is a legacy view of the "
                        "canonical OVOS-CONVERSE-1 response_mode field and will "
                        "be removed; use response_mode directly",
                        _NEXT_MAJOR_VERSION)

    def _project(self) -> Dict:
        """Project the canonical response_mode holder to the legacy mapping."""
        mode = self._session.response_mode
        if mode and mode.get("skill_id"):
            return {mode["skill_id"]: UtteranceState.RESPONSE.value}
        return {}

    def __setitem__(self, skill_id, state):
        self._warn()
        state_value = getattr(state, "value", state)
        if state_value == UtteranceState.RESPONSE.value:
            self._session.enable_response_mode(skill_id)
        else:
            self._session.disable_response_mode(skill_id)
        # rebuild from the canonical store so the view stays consistent with the
        # single-holder invariant rather than accumulating stale keys. Use the
        # plain-dict ops (super()) here so we don't re-clear response_mode, and
        # project response_mode directly rather than re-reading the deprecated
        # utterance_states property, which would emit a second warning.
        super().clear()
        super().update(self._project())

    def __delitem__(self, skill_id):
        self._warn()
        self._session.disable_response_mode(skill_id)
        if skill_id in self:
            super().__delitem__(skill_id)

    def pop(self, skill_id, *args):
        self._warn()
        self._session.disable_response_mode(skill_id)
        return super().pop(skill_id, *args)

    def update(self, *args, **kwargs):
        for skill_id, state in dict(*args, **kwargs).items():
            self[skill_id] = state

    def clear(self):
        self._warn()
        self._session.clear_response_mode()
        super().clear()


class IntentContextManagerFrame:
    def __init__(self, entities: List[dict] = None, metadata: Dict = None):
        """
        Manages entities and context for a single frame of conversation.
        Provides simple equality querying.
        Attributes:
            entities(list): Entities that belong to ContextManagerFrame
            metadata(object): metadata to describe context belonging to ContextManagerFrame
        """
        self.entities = entities or []
        self.metadata = metadata or {}

    def serialize(self) -> dict:
        """
        Get a dict representation of this frame
        """
        return {"entities": self.entities,
                "metadata": self.metadata}

    @staticmethod
    def deserialize(data: Dict):
        """
        Build an IntentContextManagerFrame from serialized data
        @param data: serialized (dict) frame data
        @return: IntentContextManagerFrame for the specified data
        """
        return IntentContextManagerFrame(**data)

    def metadata_matches(self, query: Dict = None) -> bool:
        """
        Returns key matches to metadata
        Asserts that the contents of query exist within (logical subset of)
        metadata in this frame.
        Args:
            query(dict): metadata for matching
        Returns:
            bool:
                True: when key count in query is > 0 and all keys in query in
                    self.metadata
                False: if key count in query is <= 0 or any key in query not
                    found in self.metadata
        """
        query = query or {}
        result = len(query.keys()) > 0
        for key in query.keys():
            result = result and query[key] == self.metadata.get(key)

        return result

    def merge_context(self, tag: Dict, metadata: Dict):
        """
        merge into contextManagerFrame new entity and metadata.
        Appends tag as new entity and adds keys in metadata to keys in
        self.metadata.
        Args:
            tag(dict): entity to be added to self.entities
            metadata(dict): metadata contains keys to be added to self.metadata
        """
        self.entities.append(tag)
        for k, v in metadata.items():
            if k not in self.metadata:
                self.metadata[k] = v


class IntentContextManager:
    """
    Context Manager

    Use to track context throughout the course of a conversational session.
    How to manage a session's lifecycle is not captured here.
    """

    def __init__(self, timeout: int = None,
                 frame_stack: List[Tuple[IntentContextManagerFrame,
                 float]] = None,
                 greedy: bool = None, keywords: List[str] = None,
                 max_frames: int = None):

        config = Configuration().get('context', {})
        if timeout is None:
            timeout = config.get('timeout', 2) * 60  # minutes to seconds
        if greedy is None:
            greedy = config.get('greedy', False)
        if keywords is None:
            keywords = config.get('keywords', [])
        if max_frames is None:
            max_frames = config.get('max_frames', 3)

        self.frame_stack = frame_stack or []
        self.timeout = timeout
        self.context_keywords = keywords
        self.context_max_frames = max_frames
        self.context_greedy = greedy

    def serialize(self) -> dict:
        """
        Get a dict representation of this IntentContextManager
        """
        return {"timeout": self.timeout,
                "frame_stack": [(s.serialize(), t) for (s, t)
                                in self.frame_stack]}

    @staticmethod
    def deserialize(data: Dict):
        """
        Build an IntentContextManager from serialized data
        @param data: serialized (dict) data
        @return: IntentContextManager for the specified data
        """
        timeout = data.get("timeout", 2 * 60)
        framestack = [(IntentContextManagerFrame.deserialize(f), t)
                      for (f, t) in data.get("frame_stack", [])]
        return IntentContextManager(timeout, framestack)

    def update_context(self, entities: Dict):
        """
        Updates context with keyword from the intent.

        entity(dict): Format example...
                   {'data': 'Entity tag as <str>',
                    'key': 'entity proper name as <str>',
                    'confidence': <float>'
                   }

        Args:
            entities (list): Intent to scan for keywords
        """
        for context_entity in entities:
            if self.context_greedy:
                self.inject_context(context_entity)
            elif context_entity['data'][0][1] in self.context_keywords:
                self.inject_context(context_entity)

    def clear_context(self):
        """Remove all contexts."""
        self.frame_stack = []

    def remove_context(self, context_id: str):
        """Remove a specific context entry.

        Args:
            context_id (str): context entry to remove
        """
        self.frame_stack = [(f, t) for (f, t) in self.frame_stack
                            if context_id in f.entities[0].get('data', [])]

    def inject_context(self, entity: Dict, metadata: Dict = None):
        """
        Add context to the first frame in the stack. If no frame metadata
        doesn't match the passed metadata then a new one is inserted.
        Args:
            entity(dict): Format example...
                       {'data': 'Entity tag as <str>',
                        'key': 'entity proper name as <str>',
                        'confidence': <float>'
                       }
            metadata(dict): arbitrary metadata about entity injected
        """
        metadata = metadata or {}
        try:
            if self.frame_stack:
                top_frame = self.frame_stack[0]
            else:
                top_frame = None
            if top_frame and top_frame[0].metadata_matches(metadata):
                top_frame[0].merge_context(entity, metadata)
            else:
                frame = IntentContextManagerFrame(entities=[entity],
                                                  metadata=metadata.copy())
                self.frame_stack.insert(0, (frame, time.time()))
        except (IndexError, KeyError):
            pass

    @staticmethod
    def _strip_result(context_features: Iterable):
        """Keep only the latest instance of each keyword.

        Arguments
            context_features (iterable): context features to check.
        """
        stripped = []
        processed = []
        for feature in context_features:
            keyword = feature['data'][0][1]
            if keyword not in processed:
                stripped.append(feature)
                processed.append(keyword)
        return stripped

    def get_context(self, max_frames: int = None,
                    missing_entities: List[str] = None):
        """
        Constructs a list of entities from the context.

        Args:
            max_frames(int): maximum number of frames to look back
            missing_entities(list of str): a list or set of tag names,
            as strings

        Returns:
            list: a list of entities
        """
        missing_entities = missing_entities or []

        relevant_frames = [frame[0] for frame in self.frame_stack if
                           time.time() - frame[1] < self.timeout]
        if not max_frames or max_frames > len(relevant_frames):
            max_frames = len(relevant_frames)

        missing_entities = list(missing_entities)
        context = []
        last = ''
        depth = 0
        entity = {}
        for i in range(max_frames):
            frame_entities = [entity.copy() for entity in
                              relevant_frames[i].entities]
            for entity in frame_entities:
                entity['confidence'] = entity.get('confidence', 1.0) \
                                       / (2.0 + depth)
            context += frame_entities

            # Update depth
            if entity['origin'] != last or entity['origin'] == '':
                depth += 1
            last = entity['origin']

        result = []
        if missing_entities:
            for entity in context:
                if entity.get('data') in missing_entities:
                    result.append(entity)
                    # NOTE: this implies that we will only ever get one
                    # of an entity kind from context, unless specified
                    # multiple times in missing_entities. Cannot get
                    # an arbitrary number of an entity kind.
                    missing_entities.remove(entity.get('data'))
        else:
            result = context

        # Only use the latest  keyword
        return self._strip_result(result)


class _IntentContextView(IntentContextManager):
    """Adapt-facing frame-stack VIEW projected over ``session.intent_context``.

    The adapt engine drives conversational context through the legacy
    ``IntentContextManager`` API — ``get_context`` to read tagging hints,
    ``inject_context`` / ``update_context`` / ``remove_context`` /
    ``clear_context`` to write. The canonical store is the flat OVOS-CONTEXT-1
    ``session.intent_context`` map (``key -> {value, expires_at,
    turns_remaining}``). This view keeps the exact adapt API but holds no state
    of its own: every read projects from ``intent_context`` and every write
    folds back into it, so the two are one store rather than a parallel pair.

    Projection mapping (adapt entity <-> CONTEXT-1 entry):

    - a context entity's ``data[0]`` is ``(value, entity_type)``; the
      ``entity_type`` is the CONTEXT-1 **key** (bare == shared scope, §3) and
      the ``value`` is the entry ``value``;
    - a CONTEXT-1 entry projects back to the entity
      ``{"key": value, "data": [(value, key)], "confidence": .., "origin": key}``;
    - decay is carried as ``expires_at = now + timeout`` on write; a dead entry
      (``expires_at`` in the past) is not projected into the frame stack and a
      ``value: null`` flag entry has no taggable surface form so it is omitted
      from the stack (it still gates directly via ``intent_context``, §6).

    Frame timestamps are reconstructed from ``expires_at`` so the inherited
    ``get_context`` timeout filter (``now - ts < timeout``) reproduces the
    canonical liveness test.
    """

    def __init__(self, session: "Session", timeout: int = None,
                 greedy: bool = None, keywords: List[str] = None,
                 max_frames: int = None):
        # Deliberately NOT calling super().__init__: the parent stores a
        # ``frame_stack`` list, but here the stack is a derived property with no
        # backing storage. Construction must stay side-effect-free (a fresh view
        # is built on every ``session.context`` access), so only the config-read
        # scalars are set up here.
        config = Configuration().get('context', {})
        self._session = session
        self.timeout = (config.get('timeout', 2) * 60) if timeout is None else timeout
        self.context_greedy = config.get('greedy', False) if greedy is None else greedy
        self.context_keywords = config.get('keywords', []) if keywords is None else keywords
        self.context_max_frames = config.get('max_frames', 3) if max_frames is None else max_frames

    # --- entity <-> CONTEXT-1 entry mapping ------------------------------
    def _entity_to_entry(self, entity: Dict) -> Tuple[Optional[str], Optional[Dict]]:
        """Map an adapt context entity to a ``(key, CONTEXT-1 entry)`` pair."""
        data = entity.get('data')
        value = key = None
        if isinstance(data, (list, tuple)) and data \
                and isinstance(data[0], (list, tuple)) and len(data[0]) >= 2:
            value, key = data[0][0], data[0][1]
        elif isinstance(data, str):
            key, value = data, entity.get('key')
        if not key:
            return None, None
        entry: Dict[str, Any] = {"value": value}
        if self.timeout and self.timeout > 0:
            entry["expires_at"] = time.time() + self.timeout
        return key, entry

    @staticmethod
    def _entry_to_entity(key: str, entry: Dict,
                         confidence: float = 1.0) -> Dict:
        """Project a CONTEXT-1 entry back to an adapt context entity."""
        value = entry.get("value")
        return {"key": value,
                "data": [(value, key)],
                "confidence": confidence,
                "origin": key}

    def _write(self, payload: Dict[str, Any]):
        """Fold ``payload`` (CONTEXT-1 set/null-delete) into intent_context.

        Merged in place, like the canonical mutators: the map keeps its object
        identity so a holder of the dict sees the write, and it stays an empty
        container rather than becoming ``None`` when the last entry is removed.
        """
        if self._session.intent_context is None:
            self._session.intent_context = {}
        SessionManager.merge_intent_context(self._session.intent_context, payload)
        self._session.touch()

    # --- derived frame stack (read path for the inherited get_context) ---
    @property
    def frame_stack(self) -> List[Tuple[IntentContextManagerFrame, float]]:
        """Project the live, taggable ``intent_context`` entries to a stack.

        Newest-expiring entry first, so the inherited depth-based confidence
        decay in ``get_context`` weights the freshest context highest.
        """
        now = time.time()
        rows = []
        for key, entry in (self._session.intent_context or {}).items():
            if not isinstance(entry, dict):
                continue
            value = entry.get("value")
            # Only a non-null STRING value is a taggable surface form
            # (OVOS-CONTEXT-1 §7 context-supplied capture). ``null`` flags — and
            # non-string presence markers some engines use — gate directly via
            # ``intent_context`` (§6) and have no place in the tagging stack.
            if not isinstance(value, str):
                continue
            exp = entry.get("expires_at")
            if exp is not None and exp <= now:  # dead entry
                continue
            timestamp = (exp - self.timeout) if exp is not None else now
            frame = IntentContextManagerFrame(
                entities=[self._entry_to_entity(key, entry)])
            rows.append((frame, timestamp, exp if exp is not None else float("inf")))
        rows.sort(key=lambda r: r[2], reverse=True)
        return [(frame, ts) for frame, ts, _ in rows]

    @frame_stack.setter
    def frame_stack(self, value):
        """Ingest a legacy frame stack back into ``intent_context``."""
        self._write(self._frames_to_entries(value))

    def _frames_to_entries(self, frames) -> Dict[str, Any]:
        """Project a legacy ``[(frame, ts), ...]`` stack to CONTEXT-1 entries."""
        payload: Dict[str, Any] = {}
        for frame, _ts in (frames or []):
            for entity in getattr(frame, "entities", []):
                key, entry = self._entity_to_entry(entity)
                if key:
                    payload[key] = entry
        return payload

    # --- write path overrides -------------------------------------------
    def inject_context(self, entity: Dict, metadata: Dict = None):
        key, entry = self._entity_to_entry(entity)
        if key is None:
            return
        self._write({key: entry})

    def remove_context(self, context_id: str):
        self._write({context_id: None})

    def clear_context(self):
        # cleared in place, mirroring Session.clear_intent_context: the field
        # stays an empty container, never ``None``.
        if self._session.intent_context:
            self._session.intent_context.clear()
        self._session.touch()


class Session(_SpecSession):
    """OVOS-SESSION-1 carrier with the bus-client lifecycle layered on top.

    The wire shape — every OVOS-SESSION-1 §3 / OVOS-PIPELINE-1 §7.1 /
    OVOS-CONVERSE-1 §2.1, §2.2 field, the omission-not-null serialize rule,
    the recency / cap / prune handler helpers — is inherited unchanged from
    :class:`ovos_spec_tools.session.Session`, the canonical reference
    implementation. This subclass adds only what the spec primitive
    deliberately omits (it is pure data + stdlib, config-agnostic):

    - **deployment defaults**: a uuid4 ``session_id`` when none is given, the
      configured ``lang`` / blacklists / ``pipeline`` / converse cap, read
      from ``ovos-config`` and injected into the parent constructor;
    - **lifecycle bookkeeping**: ``touch()`` / ``touch_time`` /
      ``expiration_seconds`` / ``expired()`` and ``SessionManager``
      registration on every mutation;
    - **bus-client-only state**: location / unit / format preferences, the
      speaking / recording flags;
    - **back-compat projections**: the legacy ``active_skills`` /
      ``utterance_states`` / ``context`` views (and their ``activate_skill`` /
      ``enable_response_mode`` / ``clear`` shims). Each is a DERIVED view over a
      canonical spec field — ``active_handlers`` / ``response_mode`` /
      ``intent_context`` — never a parallel store: the ``context`` frame stack
      (:class:`_IntentContextView`, the adapt-engine ``context_manager`` shape)
      projects from and folds back into ``intent_context``. Access warns; the
      legacy ``serialize`` / ``deserialize`` dict shape ecosystem readers expect
      is emitted as derived duplicates and folded back on read.
    """

    def __init__(self, session_id: str = None,
                 expiration_seconds: int = None,
                 active_skills: List[List[Union[str, float]]] = None,
                 utterance_states: Dict = None,
                 active_handlers: Optional[List[Dict]] = None,
                 converse_handlers: Optional[List[Dict]] = None,
                 response_mode: Optional[Dict] = None,
                 lang: str = None,
                 context: IntentContextManager = None,
                 site_id: Optional[str] = None,
                 pipeline: List[str] = None,
                 stt_prefs: Dict = None,
                 tts_prefs: Dict = None,
                 location_prefs: Dict = None,
                 system_unit: str = None,
                 time_format: str = None,
                 date_format: str = None,
                 is_speaking: bool = False,
                 is_recording: bool = False,
                 blacklisted_intents: Optional[List[str]] = None,
                 blacklisted_skills: Optional[List[str]] = None,
                 persona_id: Optional[str] = None,
                 fallback_handlers: Optional[List[str]] = None,
                 **canonical_kwargs):
        """
        Create a new Session with identifiers, preferences, state flags, and conversational context.

        Parameters:
            session_id (str): Session identifier; a new UUID is generated if not provided.
            expiration_seconds (int): Time-to-live in seconds for the session; use -1 for no expiration.
            active_skills (List[List[Union[str, float]]]): DEPRECATED back-compat shape — ordered list of
                active skills as [skill_id, activated_at]. Projects to/from `active_handlers`, which is the
                canonical OVOS-PIPELINE-1 §7.1 field. Prefer `active_handlers`.
            utterance_states (Dict): DEPRECATED back-compat shape — mapping of skill_id to its UtteranceState.
                Response mode is now carried by the structured `response_mode` field (OVOS-CONVERSE-1 §2.2).
            active_handlers (List[Dict]): OVOS-PIPELINE-1 §7.1 dispatch-recency record — a head-first list of
                {skill_id, activated_at} objects (most recently activated first).
            converse_handlers (List[Dict]): OVOS-CONVERSE-1 §2.1 converse-eligibility list — a head-first,
                deduplicated, capped list of {skill_id, activated_at} objects.
            response_mode (Dict): OVOS-CONVERSE-1 §2.2 pending-response window — a single {skill_id, expires_at}
                object, or None when no holder awaits a direct response.
            lang (str): Language tag for the session (standardized internally) — defaults to system default.
            context (IntentContextManager): DEPRECATED legacy adapt-style frame stack.
                Its entities fold into the canonical OVOS-CONTEXT-1 `intent_context`
                map (only when `intent_context` is empty, so a canonical value wins).
                Prefer passing `intent_context` directly.
            site_id (str): Identifier for the site/location associated with the session.
            pipeline (List[str]): Ordered intent processing pipeline identifiers.
            stt_prefs (Dict): Deprecated; provided value will be ignored.
            tts_prefs (Dict): Deprecated; provided value will be ignored.
            location_prefs (Dict): Location preferences or metadata for the session.
            system_unit (str): Measurement system preference (e.g., "metric" or "imperial").
            time_format (str): Time format preference identifier.
            date_format (str): Date format preference identifier.
            is_speaking (bool): Initial speaking state flag.
            is_recording (bool): Initial recording state flag.
            blacklisted_intents (Optional[List[str]]): Intents to ignore for this session.
            blacklisted_skills (Optional[List[str]]): Skills to ignore for this session.
            persona_id (Optional[str]): Optional persona identifier associated with this session.
            fallback_handlers (Optional[List[str]]): OVOS-FALLBACK-1 §4 registered session field —
                ordered skill-id strings. Inherited canonical field; forwarded to the parent.
            **canonical_kwargs: Every remaining canonical ``ovos_spec_tools.Session``
                SESSION-1 field — ``secondary_langs``, ``output_lang``, ``stt_lang``,
                ``request_lang``, ``detected_lang``, ``intent_context``,
                ``blacklisted_pipelines``, the six ``*_transformers`` lists, the six
                ``blacklisted_*_transformers`` lists, and ``extras`` — is accepted here
                and forwarded verbatim to the parent so the full registered field set
                round-trips. Unknown keys raise (the parent ``__init__`` rejects them),
                preserving the typo-catching contract.
        """
        if tts_prefs:
            log_deprecation("'tts_prefs' kwarg has been deprecated! value will be ignored", "0.1.0")
        if stt_prefs:
            log_deprecation("'stt_prefs' kwarg has been deprecated! value will be ignored", "0.1.0")

        # --- ovos-config deployment defaults the canonical class omits -------
        session_id = session_id or str(uuid4())
        blacklisted_skills = (blacklisted_skills or
                              Configuration().get("skills", {}).get("blacklisted_skills", []))
        blacklisted_intents = (blacklisted_intents or
                               Configuration().get("intents", {}).get("blacklisted_intents", []))
        lang = standardize_lang(lang or get_default_lang())
        # OVOS-BRIDGE-1 §3.3: site_id is the opaque group identifier. A deployer
        # MAY configure one (the bridge's own determination, step 1), but an
        # unset site_id MUST stay absent — it is NOT fabricated as a sentinel
        # such as "unknown". `None` round-trips as an omitted wire field via the
        # canonical parent's omit-when-None serialization, and consumers MUST
        # treat an absent site_id as an unknown group (§3.3 step 3).
        site_id = site_id or Configuration().get("site_id") or None
        pipeline = pipeline or Configuration().get('intents', {}).get("pipeline") or [
            "stop_high",
            "converse",
            "padatious_high",
            "adapt_high",
            "fallback_high",
            "stop_medium",
            "padatious_medium",
            "adapt_medium",
            "adapt_low",
            "common_qa",
            "fallback_medium",
            "fallback_low"
        ]

        # back-compat: seed the canonical active_handlers store from the legacy
        # active_skills [skill_id, ts] pair shape when no spec field was given.
        # The parent _coerce_handlers only accepts the spec {skill_id,
        # activated_at} object shape, so translate the legacy pairs here.
        if not active_handlers and active_skills:
            active_handlers = self._pairs_to_handler_objects(active_skills)
        # back-compat: a legacy {skill_id: "response"} entry becomes a holder.
        if response_mode is None and utterance_states:
            ttl = Configuration().get("converse", {}).get("response_timeout", 300)
            for skill_id, state in utterance_states.items():
                if state == UtteranceState.RESPONSE.value:
                    response_mode = {"skill_id": skill_id,
                                     "expires_at": time.time() + ttl}

        # --- canonical SESSION-1 fields / helpers (inherited) ----------------
        # Every registered field is forwarded to the canonical parent so the
        # whole SESSION-1 §3 surface round-trips. The explicitly-named params
        # above are the ones bus-client applies deployment defaults to (or that
        # have legacy back-compat aliases / dedicated docs); the rest arrive via
        # canonical_kwargs and pass straight through.
        super().__init__(session_id=session_id,
                         lang=lang,
                         site_id=site_id,
                         pipeline=pipeline,
                         blacklisted_skills=blacklisted_skills,
                         blacklisted_intents=blacklisted_intents,
                         active_handlers=active_handlers,
                         converse_handlers=converse_handlers,
                         response_mode=response_mode,
                         persona_id=persona_id,
                         fallback_handlers=fallback_handlers,
                         **canonical_kwargs)

        # --- bus-client-only state the canonical class does not carry --------
        self.system_unit = system_unit or Configuration().get("system_unit", "metric")
        self.date_format = date_format or Configuration().get("date_format", "DMY")
        self.time_format = time_format or Configuration().get("time_format", "full")
        self.is_recording = is_recording
        self.is_speaking = is_speaking
        self.touch_time = int(time.time())
        self.expiration_seconds = expiration_seconds or \
                                  Configuration().get('session', {}).get("ttl", -1)
        self.location_preferences = location_prefs or Configuration().get("location", {})
        # Legacy back-compat: a caller (or a legacy wire payload via
        # deserialize) may hand an ``IntentContextManager`` frame stack. It is
        # NOT stored as a parallel object — its entities fold into the canonical
        # ``intent_context`` map, but only when the canonical field is empty so
        # a modern peer's ``intent_context`` always wins (never double-counted).
        # Written directly (no touch/registry round-trip): this runs inside
        # construction, which the SessionManager fold re-enters via
        # deserialize(); touching here would recurse under the registry lock.
        if context is not None and not self.intent_context:
            entries = _IntentContextView(self)._frames_to_entries(
                getattr(context, "frame_stack", []))
            if entries:
                self.intent_context = entries
        # persona_id is an inherited canonical field (OVOS-PERSONA-1, registered
        # on ovos_spec_tools.Session); it is forwarded to super().__init__ above
        # so the parent owns its validation + omit-when-empty serialization.

        self._normalize_empty_containers()

    def _normalize_empty_containers(self):
        """Fold ``None`` canonical collection fields back to empty containers.

        The parent stores an empty list/dict field as ``None`` (SESSION-1 §2.1
        omit-when-empty). Re-materialize the empty container so the field stays
        iterable in-process, honouring the bidirectional-wire contract: a legacy
        or explicitly-null wire value folds to ``[]`` / ``{}`` rather than
        leaking a ``None`` that breaks ``x in session.<field>``.
        """
        for name in _CANONICAL_LIST_FIELDS:
            if getattr(self, name, None) is None:
                setattr(self, name, [])
        for name in _CANONICAL_DICT_FIELDS:
            if getattr(self, name, None) is None:
                setattr(self, name, {})

    @property
    def context(self) -> "_IntentContextView":
        """DEPRECATED adapt-style frame-stack view of ``intent_context``.

        Returns an :class:`IntentContextManager`-API-compatible view (the shape
        the adapt engine consumes as ``context_manager``) projected over the
        canonical OVOS-CONTEXT-1 ``session.intent_context`` map. It is not a
        parallel store: reads project from ``intent_context`` and writes fold
        back into it. Prefer reading/writing ``intent_context`` directly.
        """
        log_deprecation("Session.context (the IntentContextManager frame stack) "
                        "is a legacy view of the canonical "
                        "OVOS-CONTEXT-1 session.intent_context map and will be "
                        "removed; use intent_context directly",
                        _NEXT_MAJOR_VERSION)
        return _IntentContextView(self)

    @property
    def timezone(self) -> Optional[str]:
        """
        Return the session's configured timezone code.

        Returns:
            timezone_code (Optional[str]): Timezone identifier like 'America/Los_Angeles' if set in location preferences, `None` otherwise.
        """
        return self.location_preferences.get('timezone', {}).get('code')

    # ------------------------------------------------------------------
    # touch() on mutation — lifecycle bookkeeping the canonical class omits.
    # Thin overrides: delegate to super(), then register the change.
    # ------------------------------------------------------------------
    def add_active_handler(self, skill_id: str,
                           activated_at: Optional[float] = None):
        super().add_active_handler(skill_id, activated_at)
        self.touch()

    def remove_active_handler(self, skill_id: str):
        super().remove_active_handler(skill_id)
        self.touch()

    def add_converse_handler(self, skill_id: str,
                             activated_at: Optional[float] = None,
                             cap: Optional[int] = DEFAULT_CONVERSE_HANDLERS_CAP):
        # `cap` is the OVOS-CONVERSE-1 §2.1 per-insertion limit. It is NOT
        # session state: the orchestrator passes the deployment-configured
        # cap at call time (e.g. from converse.max_active_skills). This
        # Session never reads or stores the cap itself.
        super().add_converse_handler(skill_id, activated_at, cap=cap)
        self.touch()

    def remove_converse_handler(self, skill_id: str):
        super().remove_converse_handler(skill_id)
        self.touch()

    def prune_converse_handlers(self, ttl: float, now: Optional[float] = None):
        super().prune_converse_handlers(ttl, now)
        self.touch()

    def set_response_mode(self, skill_id: str, expires_at: float):
        super().set_response_mode(skill_id, expires_at)
        self.touch()

    def clear_response_mode(self, skill_id: Optional[str] = None):
        super().clear_response_mode(skill_id)
        self.touch()

    # ------------------------------------------------------------------
    # OVOS-CONTEXT-1 §2/§3.1 intent-context mutators — the CANONICAL write
    # path into ``intent_context``. Every entry lands on the flat §2 map at
    # its §3.1 resolved stored key; the legacy ``Session.context`` frame stack
    # (``_IntentContextView``) is a derived read/fold view over this same map,
    # never a parallel store. All three mutate the map in place so the
    # singleton Session — and the map object other views hold — keep identity.
    # ------------------------------------------------------------------
    def set_intent_context(self, key: str, value: Any = None, *,
                           scope: str = "private",
                           owner_id: Optional[str] = None,
                           expires_at: Optional[float] = None,
                           turns_remaining: Optional[int] = None):
        """Write/replace one OVOS-CONTEXT-1 §2 intent-context entry.

        The caller-chosen ``key`` is resolved to its stored form per §3.1
        (``private`` -> ``<owner_id>:<key>``, ``shared`` -> the bare ``<key>``)
        and mapped to the §2 entry ``{value, expires_at?, turns_remaining?}``
        (the optional decay fields are omitted when unset, per §2). A private
        write with no ``owner_id`` cannot resolve a key and is a no-op.

        @param key: the caller-chosen sub-key (unprefixed).
        @param value: the entry value; ``None`` marks a valueless gate (§6).
        @param scope: ``"private"`` (default) or ``"shared"``.
        @param owner_id: the declaring ``skill_id`` / ``pipeline_id``;
            required for private scope.
        @param expires_at: optional §2/§4 wall-clock expiry.
        @param turns_remaining: optional §2/§4 turn-count decay budget.
        """
        stored = resolve_key(key, scope, owner_id)
        if stored is None:
            LOG.warning(f"cannot set private intent_context '{key}' without "
                        f"an owner_id; ignoring")
            return
        entry: Dict[str, Any] = {"value": value}
        if expires_at is not None:
            entry["expires_at"] = expires_at
        if turns_remaining is not None:
            entry["turns_remaining"] = turns_remaining
        if self.intent_context is None:
            self.intent_context = {}
        self.intent_context[stored] = entry
        self.touch()

    def remove_intent_context(self, key: str, *,
                              scope: str = "private",
                              owner_id: Optional[str] = None):
        """Drop one OVOS-CONTEXT-1 intent-context entry by its declaration.

        The ``key`` is resolved per §3.1 and popped from the canonical map; a
        missing key (or an unresolvable private lookup) is a silent no-op.
        """
        stored = resolve_key(key, scope, owner_id)
        if stored is None or not self.intent_context:
            return
        if self.intent_context.pop(stored, None) is not None:
            self.touch()

    def clear_intent_context(self):
        """Empty the whole OVOS-CONTEXT-1 intent-context map.

        Cleared in place so the field stays an empty container (SESSION-1 §2.1
        in-process normalization), never ``None``.
        """
        if self.intent_context:
            self.intent_context.clear()
            self.touch()

    # ------------------------------------------------------------------
    # Back-compat projections — legacy readers across the ecosystem still
    # use `active_skills` (list of [skill_id, ts] pairs) and `utterance_states`
    # ({skill_id: state}). These project to/from the canonical spec fields so
    # there is a single source of truth.
    # ------------------------------------------------------------------
    @property
    def active_skills(self) -> List[List[Union[str, float]]]:
        """
        DEPRECATED back-compat view of `active_handlers` as `[skill_id, activated_at]` pairs.

        Reads project the canonical `active_handlers` object list to the legacy
        pair shape, head-first.
        """
        log_deprecation("Session.active_skills is a legacy view of the canonical "
                        "OVOS-PIPELINE-1 active_handlers field and will be "
                        "removed; use active_handlers directly",
                        _NEXT_MAJOR_VERSION)
        return [[h["skill_id"], h["activated_at"]] for h in self.active_handlers]

    @active_skills.setter
    def active_skills(self, value: Optional[List[List[Union[str, float]]]]):
        """Assigning legacy pairs rewrites the canonical `active_handlers` store."""
        log_deprecation("Session.active_skills is a legacy view of the canonical "
                        "OVOS-PIPELINE-1 active_handlers field and will be "
                        "removed; use active_handlers directly",
                        _NEXT_MAJOR_VERSION)
        self.active_handlers = self._coerce_handlers(
            self._pairs_to_handler_objects(value))

    @staticmethod
    def _pairs_to_handler_objects(value) -> List[Dict[str, Any]]:
        """Translate the legacy active_skills shapes into spec handler objects.

        The canonical `_coerce_handlers` (OVOS-PIPELINE-1 §7.1 / OVOS-CONVERSE-1
        §2.1) accepts only `{skill_id, activated_at}` objects. Legacy callers
        still hand a `[skill_id, activated_at]` pair (or a bare `skill_id`
        string); map those to the object shape here, at the bus-client
        boundary, and pass anything already in object shape straight through so
        the parent stays the single normalizer/validator.
        """
        out: List[Dict[str, Any]] = []
        for entry in value or []:
            if isinstance(entry, dict):
                out.append(entry)
            elif isinstance(entry, str):
                out.append({"skill_id": entry, "activated_at": None})
            elif isinstance(entry, (list, tuple)) and entry:
                skill_id = entry[0]
                activated_at = entry[1] if len(entry) > 1 else None
                out.append({"skill_id": skill_id, "activated_at": activated_at})
            else:
                out.append(entry)
        return out

    @property
    def utterance_states(self) -> Dict:
        """
        DEPRECATED back-compat view of response mode as `{skill_id: state}`.

        Only the current `response_mode` holder (if any) is reported as
        `RESPONSE`; everything else is implicitly `INTENT`.

        Returns a write-through view: legacy in-place mutations
        (``session.utterance_states[skill_id] = UtteranceState.RESPONSE`` /
        ``del session.utterance_states[skill_id]``) are forwarded to the
        structured `response_mode` store, preserving the pre-refactor behavior
        where `utterance_states` was a plain mutable dict.
        """
        log_deprecation("Session.utterance_states is a legacy view of the "
                        "canonical OVOS-CONVERSE-1 response_mode field and will "
                        "be removed; use response_mode directly",
                        _NEXT_MAJOR_VERSION)
        if self.response_mode and self.response_mode.get("skill_id"):
            projection = {self.response_mode["skill_id"]: UtteranceState.RESPONSE.value}
        else:
            projection = {}
        return _UtteranceStatesView(self, projection)

    @utterance_states.setter
    def utterance_states(self, value: Optional[Dict]):
        """Assigning legacy utterance_states rewrites the structured `response_mode`."""
        log_deprecation("Session.utterance_states is a legacy view of the "
                        "canonical OVOS-CONVERSE-1 response_mode field and will "
                        "be removed; use response_mode directly",
                        _NEXT_MAJOR_VERSION)
        self.response_mode = None
        for skill_id, state in (value or {}).items():
            if state == UtteranceState.RESPONSE.value:
                self.enable_response_mode(skill_id)

    def touch(self):
        """
        update the touch_time on the session
        """
        self.touch_time = int(time.time())
        SessionManager.update(self)

    def expired(self) -> bool:
        """
        Return True if the session has expired

        DEPRECATED: session expiry / ``touch_time`` / ``expiration_seconds`` are
        not part of OVOS-SESSION-1; the singleton registry never expires
        sessions. Retained only for back-compat.
        """
        log_deprecation("Session.expired()/touch_time/expiration_seconds are "
                        "not part of OVOS-SESSION-1 and will be removed",
                        _NEXT_MAJOR_VERSION)
        if self.expiration_seconds < 0:
            return False
        return int(time.time()) - self.touch_time > self.expiration_seconds

    def __str__(self):
        return "{%s,%d}" % (str(self.session_id), self.touch_time)

    def enable_response_mode(self, skill_id: str):
        """
        Mark a skill as expecting a response (OVOS-CONVERSE-1 §2.2 response_mode).

        Back-compat shim around `set_response_mode`: sets the single-holder window
        with a deployment-default TTL.
        @param skill_id: ID of skill expecting a response
        """
        ttl = Configuration().get("converse", {}).get("response_timeout", 300)
        self.set_response_mode(skill_id, time.time() + ttl)

    def disable_response_mode(self, skill_id: str):
        """
        Mark a skill as not expecting a response (OVOS-CONVERSE-1 §2.2).

        Back-compat shim around `clear_response_mode`: clears the window only if
        `skill_id` currently holds it.
        @param skill_id: ID of skill no longer expecting a response
        """
        self.clear_response_mode(skill_id)

    def activate_skill(self, skill_id: str):
        """
        Add a skill to the front of the active-handler recency list.

        Back-compat shim around `add_active_handler` (OVOS-PIPELINE-1 §7.1).
        @param skill_id: ID of skill to activate
        """
        self.add_active_handler(skill_id)

    def deactivate_skill(self, skill_id: str):
        """
        Remove a skill from the active-handler recency list.

        Back-compat shim around `remove_active_handler`.
        @param skill_id: ID of skill to deactivate
        """
        self.remove_active_handler(skill_id)

    def is_active(self, skill_id: str) -> bool:
        """
        Check if a skill is active
        @param skill_id: ID of skill to check
        @return: True if the requested skill is active
        """
        return any(h.get("skill_id") == skill_id for h in self.active_handlers)

    def clear(self):
        """
        Clear active_handlers
        """
        self.active_handlers = []
        self.touch()

    def serialize(self) -> dict:
        """
        Produce a dictionary representation of the session suitable for JSON serialization.

        The OVOS-SESSION-1 §3 spec fields (including the OVOS-PIPELINE-1 §7.1 /
        OVOS-CONVERSE-1 §2.1, §2.2 handler fields) are emitted by the canonical
        parent following SESSION-1 §2.1 omission-not-null: `active_handlers`,
        `converse_handlers`, and `response_mode` are present only when non-empty,
        and are never serialized as JSON `null`.

        On top of the parent's wire dict this layers the legacy
        `active_skills` (list of `[skill_id, activated_at]` pairs),
        `utterance_states`, `context`, and `location` keys (plus the
        bus-client-only preference / flag fields), projected from the canonical
        state, so that ecosystem readers parsing the raw serialized dict keep
        working.

        Returns:
            dict: A JSON-serializable mapping of session state.
        """
        # canonical SESSION-1 wire shape (spec fields, omit-when-empty). This
        # already emits active_handlers / response_mode / intent_context /
        # blacklisted_* / pipeline / persona_id following SESSION-1 §2.1
        # omission-not-null — empty values are absent, never serialized as null
        # or forced ``[]``.
        data = super().to_dict()

        # The canonical parent drops these when empty. Re-add them carrying their
        # config-resolved value, so every reader gets a populated list rather than
        # an absent key it must resolve itself (SESSION-1 §3.4 permits this).
        for _name in _ALWAYS_EMIT_LIST_FIELDS:
            data[_name] = list(getattr(self, _name) or [])

        # Legacy back-compat wire keys, DERIVED from the canonical state (never a
        # parallel store) so old ecosystem readers parsing the raw dict keep
        # working for one deprecation cycle:
        #  - ``active_skills`` mirrors active_handlers as [skill_id, ts] pairs;
        #  - ``utterance_states`` mirrors the response_mode holder;
        #  - ``context`` mirrors intent_context as an IntentContextManager frame
        #    stack. Computed inline (not via the deprecated properties) so
        #    serializing does not emit deprecation warnings.
        legacy_active = [[h["skill_id"], h["activated_at"]]
                         for h in self.active_handlers]
        if self.response_mode and self.response_mode.get("skill_id"):
            legacy_states = {self.response_mode["skill_id"]:
                             UtteranceState.RESPONSE.value}
        else:
            legacy_states = {}
        data.update({
            "active_skills": legacy_active,
            "utterance_states": legacy_states,
            "session_id": self.session_id,
            "context": _IntentContextView(self).serialize(),
            "location": self.location_preferences,
            "system_unit": self.system_unit,
            "time_format": self.time_format,
            "date_format": self.date_format,
            "is_speaking": self.is_speaking,
            "is_recording": self.is_recording,
        })
        return data

    def update_history(self, message: Message = None):
        """
        Add a message to history and then prune history
        @param message: Message to append to history
        """
        LOG.warning("update_history has been deprecated, "
                    "session no longer has a message history")

    @staticmethod
    def deserialize(data: Dict):
        """
        Construct a Session object from a serialized session dictionary.

        Every canonical OVOS-SESSION-1 §3 field is restored by delegating field
        extraction to the canonical parser (``_SpecSession.from_dict`` then reading
        the populated attributes), so the full registered field set —
        ``secondary_langs``, the per-channel language overrides, the six
        ``*_transformers`` lists, the six ``blacklisted_*_transformers`` lists,
        ``blacklisted_pipelines``, ``intent_context``, ``fallback_handlers``,
        ``persona_id``, … — round-trips rather than a hand-enumerated subset. On
        top of the canonical kwargs this overlays the bus-client-only state
        (``context``, ``location``, unit/format prefs, the speaking / recording
        flags) and the legacy back-compat aliases (``active_skills`` /
        ``utterance_states``).

        Parameters:
            data (dict): Serialized session data as produced by Session.serialize().

        Returns:
            Session: A Session instance reconstructed from the provided data.
        """
        data = data or {}
        # Delegate canonical field extraction to the parent: from_dict() applies
        # the SESSION-1 §2 parsing rules (null-as-omitted, unknown→extras). Read
        # the recognised registered fields back off the populated instance — this
        # is the single source of truth for which keys are canonical, so adding a
        # field to the spec needs no edit here. Reading attributes (not to_dict())
        # avoids re-absorbing the bus-client-only overlay keys, which from_dict()
        # routes into `extras`. Empty lists / None are dropped (matching
        # to_dict()'s omit-when-empty rule): an empty active_handlers[] must NOT
        # be forwarded, or it would block the legacy active_skills back-compat
        # seeding in __init__.
        _canon = _SpecSession.from_dict(data)
        canonical_kwargs = {name: getattr(_canon, name)
                            for name in SESSION1_REGISTERED_FIELDS
                            if getattr(_canon, name)}
        # session_id / lang / site_id / pipeline / blacklisted_* are explicit
        # bus-client params (they carry deployment defaults); pull them out of the
        # canonical bag so they are not also passed via **canonical_kwargs.
        uid = canonical_kwargs.pop("session_id", None)
        lang = canonical_kwargs.pop("lang", None)
        # OVOS-BRIDGE-1 §3.3: an absent site_id on the wire stays absent through
        # the deserialize derivation — it is not fabricated as a sentinel here.
        site_id = canonical_kwargs.pop("site_id", None)
        pipeline = canonical_kwargs.pop("pipeline", [])
        blacklisted_skills = canonical_kwargs.pop("blacklisted_skills", [])
        blacklisted_intents = canonical_kwargs.pop("blacklisted_intents", [])

        # legacy back-compat aliases — only seed the canonical handler/response
        # stores from these when the canonical keys were absent. Legacy wire shape
        # carries active_skills as [skill_id, ts] pairs; map them to spec handler
        # objects the constructor expects.
        active = Session._pairs_to_handler_objects(data.get("active_skills") or [])
        states = data.get("utterance_states") or {}
        if "active_handlers" in canonical_kwargs:
            active = []  # canonical field wins over the legacy alias
        if "response_mode" in canonical_kwargs:
            states = {}

        # Legacy wire fold: a legacy producer ships a standalone ``context``
        # frame stack instead of the canonical ``intent_context`` map. Rebuild
        # it and hand it to the constructor, which projects its entities INTO
        # ``intent_context`` — but only when the canonical field is absent, so a
        # modern peer that carries both keys is never overridden (canonical
        # wins, no double-count). ``from_dict`` above already populated
        # ``intent_context`` in ``canonical_kwargs`` when present.
        context = IntentContextManager.deserialize(data.get("context", {}))
        location = data.get("location", {})
        system_unit = data.get("system_unit")
        date_format = data.get("date_format")
        time_format = data.get("time_format")
        is_recording = data.get("is_recording", False)
        is_speaking = data.get("is_speaking", False)

        return Session(uid,
                       active_skills=active,
                       utterance_states=states,
                       lang=lang,
                       context=context,
                       pipeline=pipeline,
                       site_id=site_id,
                       location_prefs=location,
                       system_unit=system_unit,
                       date_format=date_format,
                       time_format=time_format,
                       is_recording=is_recording,
                       is_speaking=is_speaking,
                       blacklisted_intents=blacklisted_intents,
                       blacklisted_skills=blacklisted_skills,
                       **canonical_kwargs)

    # update_from is inherited from ovos_spec_tools.session.Session: it rebuilds
    # via type(self).deserialize(other.serialize()), so a bus-client Session
    # rebuilds as a bus-client Session (full §2 semantics, no aliasing).

    @staticmethod
    def from_message(message: Message = None):
        """
        Get a Session for the given message. If no session in message context,
        SessionManager.default_session is returned.
        If SessionManager.default_session is None, a default session is created
        @param message: Message to get session for
        @return: Session object
        """
        message = message or dig_for_message()
        if message and "session" in message.context:
            lang = message.context.get("lang") or \
                   message.data.get("lang")
            sess = message.context["session"]
            if lang and "lang" not in sess:
                sess["lang"] = lang
            sess = Session.deserialize(sess)
        else:
            if message:
                LOG.warning(f"No session context in message:{message.msg_type}")
                LOG.debug(f"Update ovos-bus-client or add `session` to "
                          f"`message.context` where emitted. "
                          f"context={message.context}")
            else:
                LOG.warning(f"No message found, using default session")
            # no session on the message -> the default session
            sess = SessionManager.get_default_session()
        return sess


class _BusSessionManagerMixin:
    """OVOS bus integration grafted onto the spec-tools SessionManager registry.

    There is intentionally **one** ``SessionManager`` class / registry. These
    bus methods are *copied onto* :class:`ovos_spec_tools.session.SessionManager`
    at import (see the bottom of this module), not held on a subclass. A subclass
    would inherit the registry's shared ``sessions`` dict only until something
    reassigned ``SessionManager.sessions = {...}`` — at which point the subclass
    would shadow it and desync from ``Message.forward`` / ``reply`` stamping
    (which reaches the base class). Grafting onto the one class makes that
    impossible: a reassignment hits the single shared registry.

    Adds the bus integration — default-session broadcast, recording / speaking
    state handlers, intent-context sync — and overrides ``get`` / ``update`` /
    ``reset_default_session`` with the bus-flavoured variants. The folding,
    ``_store`` and ``sync_message_session`` stamping stay the spec-tools base's.
    """
    bus = None

    @classmethod
    def sync(cls, message=None):
        """Broadcast the default session on the bus.

        DEPRECATED: the default session propagates by value-passing like any
        other session (it folds on receive, stamps on forward/reply); an
        explicit default-session broadcast is legacy and will be removed.
        """
        log_deprecation("SessionManager.sync / the default-session broadcast is "
                        "legacy; the default session propagates by value-passing "
                        "like any other session",
                        _NEXT_MAJOR_VERSION)
        if cls.bus:
            message = message or Message(SpecMessage.SESSION_SYNC)
            cls.bus.emit(message.reply("ovos.session.update_default",
                                       {"session_data": cls.default_session.serialize()}))

    @classmethod
    def connect_to_bus(cls, bus):
        cls.bus = bus
        cls.bus.on("recognizer_loop:record_begin", cls.handle_recording_start)
        cls.bus.on("recognizer_loop:record_end", cls.handle_recording_end)
        cls.bus.on("recognizer_loop:audio_output_start", cls.handle_audio_output_start)
        cls.bus.on("recognizer_loop:audio_output_end", cls.handle_audio_output_end)
        cls.bus.on(SpecMessage.SESSION_SYNC, cls.handle_session_sync)
        cls.sync()

    @classmethod
    def prune_sessions(cls):
        """
        Discard any expired sessions

        DEPRECATED: session expiry is not part of OVOS-SESSION-1; the singleton
        registry does not expire sessions. Retained as a no-op-ish back-compat
        shim.
        """
        log_deprecation("SessionManager.prune_sessions / session expiry are not "
                        "part of OVOS-SESSION-1 and will be removed",
                        _NEXT_MAJOR_VERSION)
        # mutate the shared registry dict in place (do NOT rebind it, or this
        # class would shadow the base's shared ``sessions`` and forward/reply
        # stamping would diverge from the bus handlers).
        keep = {sid: s for sid, s in cls.sessions.items()
                if not (s.expiration_seconds >= 0
                        and int(time.time()) - s.touch_time > s.expiration_seconds)}
        cls.sessions.clear()
        cls.sessions.update(keep)

    @classmethod
    def reset_default_session(cls) -> Session:
        """
        Define and return a new default_session (then broadcast it on the bus)
        """
        sess = cls.session_cls.deserialize({"session_id": DEFAULT_SESSION_ID})
        cls.sessions[DEFAULT_SESSION_ID] = sess
        cls.default_session = sess
        LOG.info("Default Session reset")
        cls.sync()
        return sess

    @classmethod
    def update(cls, sess: Session, make_default: bool = False) -> Session:
        """Register ``sess`` in the shared singleton store; return the live object.

        Folding semantics live in the spec-tools base; this override only keeps
        the deprecated ``make_default`` flag.

        @param sess: Session to update
        @param make_default: DEPRECATED. if true, rewrite ``sess.session_id`` to
            "default". Redundant under the singleton store: any session whose id
            is already "default" syncs ``default_session`` automatically, so
            promote a session by setting its id to "default" instead.
        @return: the canonical (singleton) Session for ``sess.session_id``.
        """
        if make_default:
            log_deprecation("'make_default' kwarg is deprecated and will be "
                            "removed; set session_id='default' on the session "
                            "(the singleton store syncs default_session by id)",
                            _NEXT_MAJOR_VERSION)
            sess.session_id = "default"
            # this log is dangerous, session may contain things like passwords and access keys
            # this comment is here to avoid reintroducing it by accident
            # LOG.debug(f"replacing default session with: {sess.serialize()}") # DO NOT re-enable in production
        if not sess:
            raise ValueError("Expected Session and got None")
        return cls._store(sess)

    @classmethod
    def get(cls, message: Optional[Message] = None) -> Session:
        """
        Get the active session for a given Message

        Adds the bus-client niceties over the spec-tools base: a
        ``dig_for_message`` fallback and the legacy ``Session.from_message``
        extraction (which carries the context ``lang`` onto a session that
        omits it). Folding onto the shared singleton is the base's job.

        @param message: Message to get session for
        @return: Session from message or default_session
        """
        message = message or dig_for_message()
        if message is None:
            LOG.debug("No message, use default session")
            return cls.get_default_session()
        # every session — including the default id — folds onto the one live
        # object for its id (the wire is value-passing; nothing is owner-only).
        msg_sess = Session.from_message(message)
        return cls._store(msg_sess) if msg_sess else cls.get_default_session()

    @staticmethod
    def touch(message: Message = None):
        """
        Update the last_touch timestamp on the current session

        @param message: Message to get Session for to update
        """
        sess = SessionManager.get(message)
        sess.touch()

    ##############################
    # util methods for skill consumption
    @classmethod
    def is_speaking(cls, session: Session = None):
        session = session or SessionManager.get()
        return session.is_speaking

    @classmethod
    def wait_while_speaking(cls, timeout=15, session: Session = None):
        """ wait until audio service reports end of audio output """
        if isinstance(timeout, bool):
            LOG.warning(f"expected timeout in seconds, got boolean '{timeout}', "
                        f"defaulting to 15 seconds")
            timeout = 15

        if not cls.bus:
            LOG.error("SessionManager not connected to bus, can not monitor speech state")
            return

        session = session or SessionManager.get()
        if not cls.is_speaking(session):
            LOG.warning(f"can't 'wait_while_speaking' because "
                        f"session '{session.session_id}' is not currently speaking")
            return

        # wait until end of speech
        LOG.debug(f"waiting for session '{session.session_id}' audio output to end with timeout: {timeout}")
        event = Event()
        sessid = session.session_id

        def handle_output_end(msg):
            nonlocal sessid, event
            sess = SessionManager.get(msg)
            if sessid == sess.session_id:
                LOG.debug(f"session: {sessid} audio output ended")
                event.set()

        cls.bus.on("recognizer_loop:audio_output_end", handle_output_end)
        event.wait(timeout=timeout)
        if not event.is_set():
            LOG.warning("waiting for audio output end timed out! not waiting anymore")
        cls.bus.remove("recognizer_loop:audio_output_end", handle_output_end)

    @classmethod
    def is_recording(cls, session: Session = None):
        session = session or SessionManager.get()
        return session.is_recording

    @classmethod
    def wait_while_recording(cls, timeout=45, session: Session = None):
        """ wait until listener service reports end of recording"""
        if not cls.bus:
            LOG.error("SessionManager not connected to bus, can not monitor recording state")
            return

        session = session or SessionManager.get()
        if not cls.is_recording(session):
            return

        # wait until end of recording
        event = Event()
        sessid = session.session_id

        def handle_rec_end(msg):
            nonlocal sessid, event
            sess = SessionManager.get(msg)
            if sessid == sess.session_id:
                event.set()

        cls.bus.on("recognizer_loop:record_end", handle_rec_end)
        event.wait(timeout=timeout)
        cls.bus.remove("recognizer_loop:record_end", handle_rec_end)

    ###############################
    # State tracking events
    @classmethod
    def handle_recording_start(cls, message):
        """track when a session is recording audio"""
        sess = cls.get(message)
        sess.is_recording = True
        cls.update(sess)

    @classmethod
    def handle_recording_end(cls, message):
        """track when a session stops recording audio"""
        sess = cls.get(message)
        sess.is_recording = False
        cls.update(sess)

    @classmethod
    def handle_audio_output_start(cls, message):
        """track when a session is outputting audio"""
        sess = cls.get(message)
        sess.is_speaking = True
        cls.update(sess)

    @classmethod
    def handle_audio_output_end(cls, message):
        """track when a session stops outputting audio"""
        sess = cls.get(message)
        sess.is_speaking = False
        cls.update(sess)

    @staticmethod
    def merge_intent_context(target: Dict[str, Any],
                             payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """OVOS-CONTEXT-1 §5.3 — apply an ``ovos.session.sync``
        ``intent_context`` payload **entry-by-entry** onto a target map.

        The merge is the spec's set + null-delete semantics, applied in
        place on ``target`` (the session's working ``intent_context`` map):

        - a key mapping to an **entry object** sets or replaces that key;
        - a key mapping to JSON ``null`` **removes** that key;
        - keys absent from the payload are left **unchanged**.

        Concurrent handlers writing **disjoint** keys therefore do not
        overwrite each other (§5.3). This is plain dict-level logic — the
        SessionManager singleton owns session state, so it owns the merge.

        @param target: the session's current ``intent_context`` map
            (mutated in place).
        @param payload: the inbound ``intent_context`` sync payload.
        @return: the merged ``target`` map.
        """
        if not payload:
            return target
        for key, entry in payload.items():
            if entry is None:
                target.pop(key, None)
            elif isinstance(entry, dict):
                target[key] = entry
            else:
                LOG.warning(f"ignoring malformed intent_context entry "
                            f"for key '{key}': {entry!r}")
        return target

    @classmethod
    def handle_session_sync(cls, message=None):
        """OVOS-CONTEXT-1 §5.3 — handle an ``ovos.session.sync`` request.

        The sync carries the emitter's updated session snapshot in
        ``Message.context.session`` (the standard session carrier). The
        singleton resolves the target session and merges the snapshot's
        ``intent_context`` entry-by-entry onto it (set + null-delete, §5.3),
        keeping the managed session the authoritative owner of intent
        context. The legacy default-session echo is then preserved for
        callers that emit a bare ``ovos.session.sync`` to *request* the
        current default session.
        """
        if message is not None:
            # the session rides in ``message.context.session`` — resolve it
            # via the canonical carrier rather than ``message.data``.
            inbound = Session.from_message(message)
            payload = inbound.intent_context if inbound else None
            if payload is not None:
                # merge onto the *managed* (authoritative) session if we
                # already track it — the singleton owns the working map.
                # A session we have never seen is adopted from the carrier.
                sid = inbound.session_id
                sess = cls.sessions.get(sid, inbound)
                merged = cls.merge_intent_context(
                    dict(sess.intent_context or {}), payload)
                sess.intent_context = merged or None
                cls.update(sess)
                LOG.debug(f"merged intent_context sync for session "
                          f"'{sess.session_id}': {list(payload.keys())}")
        cls.sync(message)

    # legacy alias — ``ovos.session.sync`` historically routed here to echo
    # the default session; it now also performs the OVOS-CONTEXT-1 §5.3 merge
    handle_default_session_request = handle_session_sync


# Graft the bus integration onto the ONE shared spec-tools registry class (not a
# subclass — see _BusSessionManagerMixin) and expose it under the historical name
# so ``from ovos_bus_client.session import SessionManager`` is unchanged. Because
# it is the same object as ``ovos_spec_tools.session.SessionManager``, there is a
# single ``sessions`` registry: Message.forward/reply stamping and the bus
# handlers always see the same live sessions, even if a caller reassigns
# ``SessionManager.sessions``.
for _name, _attr in vars(_BusSessionManagerMixin).items():
    if not _name.startswith("__"):
        setattr(_SpecSessionManager, _name, _attr)
# point the registry at the richer bus-client Session subclass so every
# fold/stamp builds it, then materialize the default session as that class.
_SpecSessionManager.session_cls = Session
SessionManager = _SpecSessionManager
SessionManager.get_default_session()
