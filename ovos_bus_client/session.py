import enum
import time
from threading import Lock, Event
from typing import Optional, List, Tuple, Union, Iterable, Dict
from uuid import uuid4

from ovos_config.config import Configuration
from ovos_config.locale import get_default_lang
from ovos_utils.log import LOG, log_deprecation
from ovos_utils.lang import standardize_lang_tag
from ovos_bus_client.message import dig_for_message, Message


class UtteranceState(str, enum.Enum):
    INTENT = "intent"  # includes converse
    RESPONSE = "response"


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


class Session:
    def __init__(self, session_id: str = None,
                 expiration_seconds: int = None,
                 active_skills: List[List[Union[str, float]]] = None,
                 utterance_states: Dict = None,
                 lang: str = None,
                 context: IntentContextManager = None,
                 site_id: str = "unknown",
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
                 blacklisted_skills: Optional[List[str]] = None):
        """
        Construct a session identifier
        @param session_id: string UUID for the session
        @param expiration_seconds: TTL for session (-1 for no expiration)
        @param active_skills: List of list skill_id, last reference
        @param utterance_states: dict of skill_id to UtteranceState
        @param lang: language associated with this Session
        @param context: IntentContextManager for this Session
        """
        if tts_prefs:
            log_deprecation("'tts_prefs' kwarg has been deprecated! value will be ignored", "0.1.0")
        if stt_prefs:
            log_deprecation("'stt_prefs' kwarg has been deprecated! value will be ignored", "0.1.0")
        self.session_id = session_id or str(uuid4())
        self.blacklisted_skills = (blacklisted_skills or
                                   Configuration().get("skills", {}).get("blacklisted_skills", []))
        self.blacklisted_intents = (blacklisted_intents or
                                    Configuration().get("intents", {}).get("blacklisted_intents", []))
        self.lang = standardize_lang_tag(lang or get_default_lang())
        self.system_unit = system_unit or Configuration().get("system_unit", "metric")
        self.date_format = date_format or Configuration().get("date_format", "DMY")
        self.time_format = time_format or Configuration().get("time_format", "full")

        self.is_recording = is_recording
        self.is_speaking = is_speaking
        self.site_id = site_id or Configuration().get("site_id") or "unknown"  # indoors placement info

        self.active_skills = active_skills or []  # [skill_id , timestamp]# (Message , timestamp)
        self.utterance_states = utterance_states or {}  # {skill_id: UtteranceState}

        self.touch_time = int(time.time())
        self.expiration_seconds = expiration_seconds or \
                                  Configuration().get('session', {}).get("ttl", -1)
        self.pipeline = pipeline or Configuration().get('intents', {}).get("pipeline") or [
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
        self.context = context or IntentContextManager()

        self.location_preferences = location_prefs or Configuration().get("location", {})

    @property
    def active(self) -> bool:
        """
        Return true if any skills attached to this session are active.
        NOTE: skills without converse implemented never get added here unless
        using get_response
        """
        return len(self.active_skills) > 0

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
        Mark a skill as expecting a response
        @param skill_id: ID of skill expecting a response
        """
        self.utterance_states[skill_id] = UtteranceState.RESPONSE.value
        self.touch()

    def disable_response_mode(self, skill_id: str):
        """
        Mark a skill as not expecting a response (handling intents normally)
        @param skill_id: ID of skill expecting a response
        """
        self.utterance_states[skill_id] = UtteranceState.INTENT.value
        self.touch()

    def activate_skill(self, skill_id: str):
        """
        Add a skill to the front of the active_skills list
        @param skill_id: ID of skill to activate
        """
        # remove it from active list
        self.deactivate_skill(skill_id)
        # add skill with timestamp to start of active list
        self.active_skills.insert(0, [skill_id, time.time()])
        self.touch()

    def deactivate_skill(self, skill_id: str):
        """
        Remove a skill from the active_skills list
        @param skill_id: ID of skill to deactivate
        """
        active_ids = [s[0] for s in self.active_skills]
        if skill_id in active_ids:
            idx = active_ids.index(skill_id)
            self.active_skills.pop(idx)
        self.touch()

    def is_active(self, skill_id: str) -> bool:
        """
        Check if a skill is active
        @param skill_id: ID of skill to check
        @return: True if the requested skill is active
        """
        active_ids = [s[0] for s in self.active_skills]
        return skill_id in active_ids

    def clear(self):
        """
        Clear active_skills
        """
        self.active_skills = []
        self.touch()

    def serialize(self) -> dict:
        """
        Get a json-serializable dict representation of this session
        """
        # safe for json dumping
        return {
            "active_skills": self.active_skills,
            "utterance_states": self.utterance_states,
            "session_id": self.session_id,
            "lang": self.lang,
            "context": self.context.serialize(),
            "site_id": self.site_id,
            "pipeline": self.pipeline,
            "location": self.location_preferences,
            "system_unit": self.system_unit,
            "time_format": self.time_format,
            "date_format": self.date_format,
            "is_speaking": self.is_speaking,
            "is_recording": self.is_recording,
            "blacklisted_skills": self.blacklisted_skills,
            "blacklisted_intents": self.blacklisted_intents
        }

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
        Build a Session object from dict data
        @param data: dict serialized Session object
        @return: Session representation of data
        """
        uid = data.get("session_id")
        active = data.get("active_skills") or []
        states = data.get("utterance_states") or {}
        lang = data.get("lang")
        context = IntentContextManager.deserialize(data.get("context", {}))
        site_id = data.get("site_id", "unknown")
        pipeline = data.get("pipeline", [])
        location = data.get("location", {})
        system_unit = data.get("system_unit")
        date_format = data.get("date_format")
        time_format = data.get("time_format")
        is_recording = data.get("is_recording", False)
        is_speaking = data.get("is_speaking", False)
        blacklisted_skills = data.get("blacklisted_skills", [])
        blacklisted_intents = data.get("blacklisted_intents", [])
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
                       blacklisted_skills=blacklisted_skills)

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
            message = message or Message("ovos.session.sync")
            cls.bus.emit(message.reply("ovos.session.update_default",
                                       {"session_data": cls.default_session.serialize()}))

    @classmethod
    def connect_to_bus(cls, bus):
        cls.bus = bus
        cls.bus.on("recognizer_loop:record_begin", cls.handle_recording_start)
        cls.bus.on("recognizer_loop:record_end", cls.handle_recording_end)
        cls.bus.on("recognizer_loop:audio_output_start", cls.handle_audio_output_start)
        cls.bus.on("recognizer_loop:audio_output_end", cls.handle_audio_output_end)
        cls.bus.on("ovos.session.sync", cls.handle_default_session_request)
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
    def update(sess: Session, make_default: bool = False):
        """
        Update the last_touch timestamp on the current session
        @param sess: Session to update
        @param make_default: if true, set default_session to sess
        """
        if not sess:
            raise ValueError(f"Expected Session and got None")

        if make_default:
            sess.session_id = "default"
            # this log is dangerous, session may contain things like passwords and access keys
            # this comment is here to avoid reintroducing it by accident
            # LOG.debug(f"replacing default session with: {sess.serialize()}") # DO NOT re-enable in production

        if sess.session_id == "default":
            SessionManager.default_session = sess
        SessionManager.sessions[sess.session_id] = sess

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
                    SessionManager.sessions[msg_sess.session_id] = msg_sess
                    return msg_sess
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

    @classmethod
    def handle_default_session_request(cls, message=None):
        cls.sync(message)
