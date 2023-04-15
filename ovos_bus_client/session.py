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
                 max_time=5, max_messages=5, utterance_states=None, lang=None):
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
            "lang": self.lang
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
        return Session(uid,
                       active_skills=active,
                       utterance_states=states,
                       history=history,
                       max_time=max_time,
                       lang=lang,
                       max_messages=max_messages)

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
    def touch():
        """
        Update the last_touch timestamp on the current session

        :return: None
        """
        SessionManager.get().touch()
