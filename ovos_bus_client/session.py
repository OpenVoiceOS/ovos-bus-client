import enum
import time
from threading import Lock, Event
from typing import Optional, List, Tuple, Union, Iterable, Dict, Any
from uuid import uuid4

from ovos_config.config import Configuration
from ovos_config.locale import get_default_lang
from ovos_utils.log import LOG, log_deprecation
from ovos_spec_tools import standardize_lang, SpecMessage
from ovos_spec_tools.session import (Session as _SpecSession,
                                     DEFAULT_CONVERSE_HANDLERS_CAP,
                                     SESSION1_REGISTERED_FIELDS)
from ovos_bus_client.message import dig_for_message, Message


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

    def __setitem__(self, skill_id, state):
        state_value = getattr(state, "value", state)
        if state_value == UtteranceState.RESPONSE.value:
            self._session.enable_response_mode(skill_id)
        else:
            self._session.disable_response_mode(skill_id)
        # rebuild from the canonical store so the view stays consistent with the
        # single-holder invariant rather than accumulating stale keys. Use the
        # plain-dict ops (super()) here so we don't re-clear response_mode.
        super().clear()
        super().update(dict(self._session.utterance_states))

    def __delitem__(self, skill_id):
        self._session.disable_response_mode(skill_id)
        if skill_id in self:
            super().__delitem__(skill_id)

    def pop(self, skill_id, *args):
        self._session.disable_response_mode(skill_id)
        return super().pop(skill_id, *args)

    def update(self, *args, **kwargs):
        for skill_id, state in dict(*args, **kwargs).items():
            self[skill_id] = state

    def clear(self):
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
    - **bus-client-only state**: the ``IntentContextManager``, location /
      unit / format preferences, the speaking / recording flags;
    - **back-compat projections**: the legacy ``active_skills`` /
      ``utterance_states`` views (and their ``activate_skill`` /
      ``enable_response_mode`` / ``clear`` shims), plus the legacy
      ``serialize`` / ``deserialize`` dict shape ecosystem readers expect.
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
            context (IntentContextManager): Conversational context manager for the session.
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
        self.context = context or IntentContextManager()
        self.location_preferences = location_prefs or Configuration().get("location", {})
        # persona_id is an inherited canonical field (OVOS-PERSONA-1, registered
        # on ovos_spec_tools.Session); it is forwarded to super().__init__ above
        # so the parent owns its validation + omit-when-empty serialization.

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
        return [[h["skill_id"], h["activated_at"]] for h in self.active_handlers]

    @active_skills.setter
    def active_skills(self, value: Optional[List[List[Union[str, float]]]]):
        """Assigning legacy pairs rewrites the canonical `active_handlers` store."""
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
        if self.response_mode and self.response_mode.get("skill_id"):
            projection = {self.response_mode["skill_id"]: UtteranceState.RESPONSE.value}
        else:
            projection = {}
        return _UtteranceStatesView(self, projection)

    @utterance_states.setter
    def utterance_states(self, value: Optional[Dict]):
        """Assigning legacy utterance_states rewrites the structured `response_mode`."""
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
        """
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
        # canonical SESSION-1 wire shape (spec fields, omit-when-empty)
        data = super().to_dict()
        # legacy back-compat projections + bus-client-only state
        # NOTE: persona_id is a canonical inherited field — the parent's
        # to_dict() above already emits it with SESSION-1 §2.1 omit-when-empty
        # handling, so it is intentionally NOT re-emitted here.
        data.update({
            "active_skills": self.active_skills,
            "utterance_states": self.utterance_states,
            "session_id": self.session_id,
            "context": self.context.serialize(),
            "location": self.location_preferences,
            "system_unit": self.system_unit,
            "time_format": self.time_format,
            "date_format": self.date_format,
            "is_speaking": self.is_speaking,
            "is_recording": self.is_recording,
            # always emit raw lists for legacy readers (canonical omits when empty)
            "blacklisted_skills": self.blacklisted_skills or [],
            "blacklisted_intents": self.blacklisted_intents or [],
            "pipeline": self.pipeline or [],
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

        # bus-client-only state the canonical class does not carry.
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

    def update_from(self, other: "Session") -> "Session":
        """Mutate this session in place to mirror ``other``'s state.

        The OVOS bus is value-passing: every message carries a serialized
        snapshot of its session, so each :meth:`from_message` would otherwise
        build a *fresh* ``Session`` object for the same id. ``SessionManager``
        keeps exactly one live object per ``session_id`` and folds incoming
        snapshots onto it through here, so a reference taken at one point in a
        flow still observes writes made through a later snapshot of the same id
        (e.g. ``is_speaking`` flipped by an ``audio_output_*`` handler).

        Last-writer-wins: ``other`` fully replaces this session's fields. The
        ``session_id`` is preserved — it is the registry key and must not drift.
        """
        if other is self:
            return self
        sid = self.session_id
        self.__dict__.update(other.__dict__)
        self.session_id = sid
        return self

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
            if "lang" not in sess:
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
            # new session
            sess = SessionManager.default_session
        if sess and sess.expired():
            LOG.debug(f"unexpiring session {sess.session_id}")
        return sess


class SessionManager:
    """ Keeps track of the current active session. """
    default_session: Session = Session("default")
    __lock = Lock()
    sessions = {"default": default_session}
    bus = None

    @classmethod
    def sync(cls, message=None):
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

    @staticmethod
    def prune_sessions():
        """
        Discard any expired sessions
        """
        # TODO: Consider when to prune sessions; an event or callback scheduled
        #   on `touch`, periodically scheduled event, or triggered on some
        #   interaction with `SessionManager` (ideally threaded to not slow
        #   down references)
        SessionManager.sessions = {sid: s for sid, s in
                                   SessionManager.sessions.items()
                                   if not s.expired}

    @staticmethod
    def reset_default_session() -> Session:
        """
        Define and return a new default_session
        """
        with SessionManager.__lock:
            sess = Session("default")
            LOG.info(f"Default Session reset")
            SessionManager.default_session = SessionManager.sessions["default"] = sess
            SessionManager.sync()
        return SessionManager.default_session

    @staticmethod
    def update(sess: Session, make_default: bool = False) -> Session:
        """
        Register ``sess`` in the singleton store and return the live object
        for its id.

        @param sess: Session to update
        @param make_default: if true, set default_session to sess
        @return: the canonical (singleton) Session for ``sess.session_id`` —
            the same object on every call for that id, so the caller can rebind
            to it. When the id is already known the incoming snapshot is folded
            onto the existing instance rather than replacing it.
        """
        if not sess:
            raise ValueError(f"Expected Session and got None")

        if make_default:
            sess.session_id = "default"
            # this log is dangerous, session may contain things like passwords and access keys
            # this comment is here to avoid reintroducing it by accident
            # LOG.debug(f"replacing default session with: {sess.serialize()}") # DO NOT re-enable in production

        return SessionManager._store(sess)

    @classmethod
    def _store(cls, sess: Session) -> Session:
        """Register ``sess`` and return the one live object for its id.

        Every ``session_id`` maps to a single, stable ``Session`` instance for
        the lifetime of the process. When the id is already known, the incoming
        snapshot is folded onto the existing object (see
        :meth:`Session.update_from`) and that object — not the snapshot — is
        returned, so object identity per id never changes. This is what lets a
        held session reference observe later mutations to the same id, instead
        of each bus message silently swapping in a fresh, disconnected object.
        """
        with cls.__lock:
            existing = cls.sessions.get(sess.session_id)
            if existing is not None and existing is not sess:
                existing.update_from(sess)
                sess = existing
            cls.sessions[sess.session_id] = sess
            if sess.session_id == "default":
                cls.default_session = sess
        return sess

    @staticmethod
    def get(message: Optional[Message] = None) -> Session:
        """
        Get the active session for a given Message

        @param message: Message to get session for
        @return: Session from message or default_session
        """
        sess = SessionManager.default_session
        message = message or dig_for_message()

        # A message exists, get a real session
        if message:
            msg_sess = Session.from_message(message)
            if msg_sess:
                if msg_sess.session_id != "default":  # reserved namespace for ovos-core
                    # fold the message snapshot onto the singleton for this id
                    # and hand back that single live object (never the snapshot)
                    return SessionManager._store(msg_sess)
            else:
                LOG.debug(f"No session from message, use default session")
        else:
            LOG.debug(f"No message, use default session")

        return sess

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
