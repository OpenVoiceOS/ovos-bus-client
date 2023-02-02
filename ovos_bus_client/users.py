import enum
import time
from threading import Lock
from uuid import uuid4

from ovos_config.config import Configuration
from ovos_utils.log import LOG

from ovos_bus_client.message import dig_for_message, Message
from ovos_bus_client.session import SessionManager, Session


def _config2preferences(cfg=None):
    # TODO - filter relevant keys only, ensure no sensistive or irrelevant info passed along
    if cfg is None:  # allow empty dict to mean "no preferences"
        cfg = dict(Configuration.get())
    return cfg or {}


class UserData:
    """
    An class representing a user, this session data comes in message.context
    It may be provided by a OVOS plugin along the stack
    (eg, speaker recognition) or from clients (eg, a chat application)
    """

    def __init__(self, user_id=None, session=None, preferences=None):
        self.user_id = user_id or str(uuid4())
        self.session = session or SessionManager.get()
        self.preferences = _config2preferences(preferences)

    def as_dict(self):
        return {
            "user_id": self.user_id,
            "session": self.session.as_dict(),
            "preferences": self.preferences
        }

    @staticmethod
    def from_dict(data):
        uid = data.get("user_id")
        sid = data.get("session_id")
        sess = data.get("session") or {}
        if sid:
            sess["session_id"] = sid
        sess = Session.from_dict(sess)
        prefs = data.get("preferences")
        return UserData(uid, session=sess, preferences=prefs)

    @staticmethod
    def from_message(message=None):
        message = message or dig_for_message()
        sess = SessionManager.get(message)
        uid = prefs = None
        if message:
            data = message.context.get("user") or {}
            uid = data.get("user_id") or message.context.get("user_id")
            prefs = data.get("preferences") or message.context.get("config")
        return UserData(uid, session=sess, preferences=prefs)


class UserManager:
    """ Keeps track of the current active user. """
    default_user = None  # dummy user, representing default values from mycroft.conf
    users = {}

    @staticmethod
    def set_default_user(user):
        if isinstance(user, dict):
            user = UserData.from_dict(user)
        if isinstance(user, Message):
            user = UserData.from_message(user)
        assert isinstance(user, UserData)
        UserManager.default_user = user

    @staticmethod
    def get(message=None):
        """
        get the active user.

        :return: An user object
        """
        message = message or dig_for_message()
        user = UserData.from_message(message)
        UserManager.users[user.uid] = user
        return user
