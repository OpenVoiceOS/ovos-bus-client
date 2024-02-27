import enum
import time
from threading import Lock
from uuid import uuid4

from ovos_config.config import Configuration
from ovos_config.locale import get_default_tz
from ovos_utils.log import LOG

from ovos_bus_client.message import dig_for_message, Message
from ovos_bus_client.session import SessionManager, Session
from ovos_backend_client.api import GeolocationApi


def _config2preferences(cfg=None):
    # TODO - filter relevant keys only, ensure no sensistive or irrelevant info passed along
    if cfg is None:  # allow empty dict to mean "no preferences"
        cfg = dict(Configuration.get())
    return cfg or {}


def _config2location(cfg=None):
    loc = cfg or Configuration.get()["location"]
    if isinstance(loc, dict):
        loc = Location.from_dict(cfg)
    assert isinstance(loc, Location)
    return loc


class Location:
    geolocation = GeolocationApi()

    def __init__(self, lat, lon):
        self.latitude = lat
        self.longitude = lon
        # everything is automatically derived from lat/lon
        # this ensures location is not ambiguous
        self._address = None
        self._timezone_code = None
        self._city = None
        self._state = None
        self._country = None
        self._country_code = None

    @property
    def timezone_code(self):
        if self._timezone_code is None:
            self._timezone_code = get_timezone(self.latitude, self.longitude)
        return self._timezone_code

    @property
    def timezone(self):
        if self.timezone_code:
            return gettz(self.timezone_code)
        return get_default_tz()

    @property
    def current_time(self):
        return datetime.datetime.now(self.timezone)

    @property
    def address(self):
        if not self._address:
            if not self._city or not self._country:
                self._reverse_geolocate()
            self._address = self._city
            if self._state:
                self._address += ", " + self._state
            if self._country:
                self._address += ", " + self._country
        return self._address

    @property
    def city(self):
        if not self._city:
            self._reverse_geolocate()
        return self._city

    @property
    def state(self):
        if not self._state:
            self._reverse_geolocate()
        return self._state

    @property
    def country(self):
        if not self._country:
            self._reverse_geolocate()
        return self._country

    @property
    def country_code(self):
        if not self._country_code:
            self._reverse_geolocate()
        return self._country_code

    @classmethod
    def _reverse_geolocate(cls):
        data = cls.geolocation.get_reverse_geolocation(lat, lon)
        loc = cls.from_dict(data)
        return loc

    @classmethod
    def from_address(cls, address):
        data = cls.geolocation.get_geolocation(address) or {}
        data["address"] = address
        location = cls.from_dict(data)
        return location

    @classmethod
    def from_dict(cls, data):
        if isinstance(data, str):
            data = json.loads(data)

        latitude = data["coordinate"]["latitude"]
        longitude = data["coordinate"]["longitude"]
        loc = Location(latitude, longitude)
        # avoid geolocation api calls if possible
        loc._city = data["city"]["name"]
        loc._state = data["city"]["state"].get("name")
        loc._country = data["city"]["state"]["country"]["name"]
        loc._timezone_code = data.get("timezone", {}).get("name")
        loc._address = data.get("address")
        return loc

    def as_dict(self):
        return {
            "city": {
                "name": self.city,
                "state": {
                    "name": self.state,
                    "country": {
                        "code": self.country_code,
                        "name": self.country
                    }
                }
            },
            "coordinate": {
                "latitude": self.latitude,
                "longitude": self.longitude
            },
            "timezone": {
                "name": self.timezone_code
            },
            "address": self.address
        }


class UserData:
    """
    An class representing a user, this session data comes in message.context
    It may be provided by a OVOS plugin along the stack
    (eg, speaker recognition) or from clients (eg, a chat application)
    """

    def __init__(self, user_id=None, session=None,
                 preferences=None, location=None):
        self.user_id = user_id or str(uuid4())
        self.session = session or SessionManager.get()
        self.preferences = _config2preferences(preferences)  # equivalent to a mycroft.conf per user
        location = location or self.preferences.get("location")
        self.location = _config2location(location)

    def as_dict(self):
        return {
            "user_id": self.user_id,
            "session": self.session.as_dict(),
            "preferences": self.preferences,
            "location": self.location
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
        location = data.get("location")
        return UserData(uid, session=sess,
                        preferences=prefs, location=location)

    @staticmethod
    def from_message(message=None):
        message = message or dig_for_message()
        sess = SessionManager.get(message)
        uid = prefs = location = None
        if message:
            data = message.context.get("user") or {}
            uid = data.get("user_id") or message.context.get("user_id")
            prefs = data.get("preferences") or message.context.get("config")
            location = data.get("location")
        return UserData(uid, session=sess,
                        preferences=prefs, location=location)


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
