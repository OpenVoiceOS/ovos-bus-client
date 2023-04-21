import enum
import time
from threading import Lock
from uuid import uuid4

from ovos_bus_client.message import dig_for_message
from ovos_utils.log import LOG
from ovos_config.config import Configuration
from ovos_config.locale import get_default_lang


class UtteranceState(str, enum.Enum):
    INTENT = "intent"  # includes converse
    RESPONSE = "response"


class Session:
    """
    An class representing a Mycroft Session Identifier
    """

    def __init__(self, session_id=None, expiration_seconds=None, active_skills=None, history=None,
                 max_time=5, max_messages=5, utterance_states=None, lang=None, context=None):
        self.session_id = session_id or str(uuid4())
        self.lang = lang or get_default_lang()
        self.active_skills = active_skills or []  # [skill_id , timestamp]
        self.history = history or []  # [Message , timestamp]
        self.utterance_states = utterance_states or {}  # {skill_id: UtteranceState}
        self.max_time = max_time  # minutes
        self.max_messages = max_messages
        self.touch_time = int(time.time())
        if expiration_seconds is None:
            expiration_seconds = Configuration().get('session', {}).get("ttl", -1)
        self.expiration_seconds = expiration_seconds
        self.context = context or IntentContextManager(timeout=self.touch_time + expiration_seconds)

    @property
    def active(self):
        # NOTE: skills without converse implemented never
        # get added here unless using get_response
        return len(self.active_skills) > 0

    def touch(self):
        """
        update the touch_time on the session

        :return:
        """
        self.touch_time = int(time.time())

    def expired(self):
        """
        determine if the session has expired

        :return:
        """
        if self.expiration_seconds < 0:
            return False
        return int(time.time()) - self.touch_time > self.expiration_seconds

    def __str__(self):
        return "{%s,%d}" % (str(self.session_id), self.touch_time)

    def enable_response_mode(self, skill_id):
        self.utterance_states[skill_id] = UtteranceState.RESPONSE.value

    def disable_response_mode(self, skill_id):
        self.utterance_states[skill_id] = UtteranceState.INTENT.value

    def activate_skill(self, skill_id):
        # remove it from active list
        self.deactivate_skill(skill_id)
        # add skill with timestamp to start of active list
        self.active_skills.insert(0, [skill_id, time.time()])

    def deactivate_skill(self, skill_id):
        active_ids = [s[0] for s in self.active_skills]
        if skill_id in active_ids:
            idx = active_ids.index(skill_id)
            self.active_skills.pop(idx)

    def is_active(self, skill_id):
        self._prune_history()
        active_ids = [s[0] for s in self.active_skills]
        return skill_id in active_ids

    def _prune_history(self):
        # filter old messages from history
        now = time.time()
        self.history = [m for m in self.history
                        if now - m[1] < 60 * self.max_time]
        # keep only self.max_messages
        if len(self.history) > self.max_messages:
            self.history = self.history[self.max_messages * -1:]

    def clear(self):
        self.active_skills = []  # [skill_id , timestamp]
        self.history = []  # [Message , timestamp]

    def serialize(self):
        # safe for json dumping
        return {
            "active_skills": self.active_skills,
            "utterance_states": self.utterance_states,
            "session_id": self.session_id,
            "history": self.history,
            "lang": self.lang,
            "context": self.context.serialize()
        }

    def update_history(self, message=None):
        message = message or dig_for_message()
        if message:
            self.history.append((message.serialize(), time.time()))
        self._prune_history()

    @staticmethod
    def deserialize(data):
        uid = data.get("session_id")
        active = data.get("active_skills") or []
        history = data.get("history") or []
        max_time = data.get("max_time") or 5
        max_messages = data.get("max_messages") or 5
        states = data.get("utterance_states") or {}
        lang = data.get("lang")
        context = IntentContextManager.deserialize(data["context"])
        return Session(uid,
                       active_skills=active,
                       utterance_states=states,
                       history=history,
                       max_time=max_time,
                       lang=lang,
                       max_messages=max_messages,
                       context=context)

    @staticmethod
    def from_message(message=None):
        message = message or dig_for_message()
        if message:
            lang = message.context.get("lang") or \
                   message.data.get("lang")
            sid = None
            if "session_id" in message.context:
                sid = message.context["session_id"]
            if "session" in message.context:
                sess = message.context["session"]
                if sid and "session_id" not in sess:
                    sess["session_id"] = sid
                if "lang" not in sess:
                    sess["lang"] = lang
                sess = Session.deserialize(sess)
            elif sid:
                sess = SessionManager.sessions.get(sid) or \
                       Session(sid)
                if lang:
                    sess.lang = lang
            else:
                sess = Session(lang=lang)
        else:
            # new session
            sess = Session()
        return sess


class SessionManager:
    """ Keeps track of the current active session. """
    default_session = None
    __lock = Lock()
    sessions = {}

    @staticmethod
    def reset_default_session():
        with SessionManager.__lock:
            sess = Session()
            LOG.info(f"New Default Session Start: {sess.session_id}")
            SessionManager.default_session = sess
            SessionManager.sessions[sess.session_id] = sess
        return SessionManager.default_session

    @staticmethod
    def update(sess: Session, make_default: bool = False):
        """
        Update the last_touch timestamp on the current session

        :return: None
        """
        sess.touch()
        SessionManager.sessions[sess.session_id] = sess
        if make_default:
            SessionManager.default_session = sess

    @staticmethod
    def get(message=None):
        """
        get the active session.

        :return: An active session
        """
        sess = SessionManager.default_session
        message = message or dig_for_message()

        if not sess or sess.expired():
            if sess is not None and sess.session_id in SessionManager.sessions:
                SessionManager.sessions.pop(sess.session_id)
            sess = SessionManager.reset_default_session()
        if message:
            sess = Session.from_message(message)
            SessionManager.sessions[sess.session_id] = sess
        return sess

    @staticmethod
    def touch(message=None):
        """
        Update the last_touch timestamp on the current session

        :return: None
        """
        SessionManager.get(message).touch()


class IntentContextManagerFrame:
    """
    Manages entities and context for a single frame of conversation.
    Provides simple equality querying.
    Attributes:
        entities(list): Entities that belong to ContextManagerFrame
        metadata(object): metadata to describe context belonging to ContextManagerFrame
    """

    def __init__(self, entities=None, metadata=None):
        """
        Initialize ContextManagerFrame
        Args:
            entities(list): List of Entities...
            metadata(object): metadata to describe context?
        """
        self.entities = entities or []
        self.metadata = metadata or {}

    def serialize(self):
        return {"entities": self.entities,
                "metadata": self.metadata}

    @staticmethod
    def deserialize(data):
        return IntentContextManagerFrame(**data)

    def metadata_matches(self, query=None):
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

    def merge_context(self, tag, metadata):
        """
        merge into contextManagerFrame new entity and metadata.
        Appends tag as new entity and adds keys in metadata to keys in
        self.metadata.
        Args:
            tag(str): entity to be added to self.entities
            metadata(dict): metadata contains keys to be added to self.metadata
        """
        self.entities.append(tag)
        for k, v in metadata.items():
            if k not in self.metadata:
                self.metadata[k] = v


class IntentContextManager:
    """Context Manager

    Use to track context throughout the course of a conversational session.
    How to manage a session's lifecycle is not captured here.
    """

    def __init__(self, timeout=None, frame_stack=None,
                 greedy=None, keywords=None, max_frames=None):

        config = Configuration().get('context', {})
        if timeout is None:
            timeout = config.get('timeout', 2)
        if greedy is None:
            greedy = config.get('greedy', False)
        if keywords is None:
            keywords = config.get('keywords', [])
        if max_frames is None:
            max_frames = config.get('max_frames', 3)

        self.frame_stack = frame_stack or []
        self.timeout = timeout * 60  # minutes to seconds
        self.context_keywords = keywords
        self.context_max_frames = max_frames
        self.context_greedy = greedy

    def serialize(self):
        return {"timeout": self.timeout,
                "frame_stack": [s.serialize() for s in self.frame_stack]}

    @staticmethod
    def deserialize(data):
        timeout = data["timeout"]
        framestack = [IntentContextManagerFrame.deserialize(f) for f in data["frame_stack"]]
        return IntentContextManager(timeout, framestack)

    def update_context(self, entities):
        """Updates context with keyword from the intent.

        entity(dict): Format example...
                   {'data': 'Entity tag as <str>',
                    'key': 'entity proper name as <str>',
                    'confidence': <float>'
                   }
                               
        Args:
            entities (list): Intent to scan for keywords
        """
        for context_entity in entities:
            #  entity(dict): Format example...
            #   {'data': 'Entity tag as <str>',
            #   'key': 'entity proper name as <str>',
            #   'confidence': <float>' }
            if self.context_greedy:
                self.inject_context(context_entity)
            elif context_entity['data'][0][1] in self.context_keywords:
                self.inject_context(context_entity)

    def clear_context(self):
        """Remove all contexts."""
        self.frame_stack = []

    def remove_context(self, context_id):
        """Remove a specific context entry.

        Args:
            context_id (str): context entry to remove
        """
        self.frame_stack = [(f, t) for (f, t) in self.frame_stack
                            if context_id in f.entities[0].get('data', [])]

    def inject_context(self, entity, metadata=None):
        """
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
    def _strip_result(context_features):
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

    def get_context(self, max_frames=None, missing_entities=None):
        """ Constructs a list of entities from the context.

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
