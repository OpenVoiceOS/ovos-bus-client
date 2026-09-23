"""The two fixes the review of #365 asked for.

Both are about the same confusion: which box a value belongs to.

1. The DEFAULT session is the box's own session, not a received one, so it
   takes the §3.5 stamp like any other session this box originates.
   OVOS-SESSION-1 §4.1 binds "every session other than the default session",
   so the default is the one case §4.1 never bound.

2. ``location_preferences`` is a legacy nested VIEW of that field. When the
   session declares a ``tz``, the whole ``timezone`` block must come from
   that zone. Mixing the session's ``code`` with the reading box's ``offset``
   produces one object whose own two fields disagree.
"""
import unittest
from unittest.mock import patch

import ovos_bus_client.session as session_module
from ovos_bus_client.session import Session, SessionManager, DEFAULT_SESSION_ID

_KANSAS_CITY = {
    "location": {
        "city": {"code": "Kansas City", "name": "Kansas City"},
        "coordinate": {"latitude": 38.9717, "longitude": -95.2353},
        "timezone": {"code": "America/Chicago", "name": "Central Standard Time",
                     "dstOffset": 3600000, "offset": -21600000},
    },
}

_LISBON_SESSION = {"lat": 38.7223, "lon": -9.1393, "tz": "Europe/Lisbon"}


def _fresh_manager():
    """Drop the process-wide default session so it is built again."""
    SessionManager.sessions.pop(DEFAULT_SESSION_ID, None)
    SessionManager.default_session = None


class TestDefaultSessionIsStamped(unittest.TestCase):
    """Fix 1: the box's own default session carries its configured position."""

    def setUp(self):
        _fresh_manager()

    tearDown = setUp

    def test_default_session_carries_the_configured_location(self):
        with patch.object(session_module, "Configuration",
                          return_value=_KANSAS_CITY):
            sess = SessionManager.get_default_session()
        self.assertEqual(sess.location, {"lat": 38.9717, "lon": -95.2353,
                                         "tz": "America/Chicago"})

    def test_the_default_session_carrier_carries_it_too(self):
        """It has to survive serialize, or no consumer ever sees it."""
        with patch.object(session_module, "Configuration",
                          return_value=_KANSAS_CITY):
            carrier = SessionManager.get_default_session().serialize()
        self.assertEqual(carrier.get("location", {}).get("tz"),
                         "America/Chicago")

    def test_a_box_with_no_configured_location_stamps_nothing(self):
        with patch.object(session_module, "Configuration", return_value={}):
            sess = SessionManager.get_default_session()
        self.assertEqual(sess.location, {})

    def test_a_received_default_carrier_is_still_never_materialized(self):
        """§4.1 is untouched: deserialize remains the rebuild path."""
        with patch.object(session_module, "Configuration",
                          return_value=_KANSAS_CITY):
            rebuilt = Session.deserialize({"session_id": DEFAULT_SESSION_ID})
        self.assertEqual(rebuilt.location, {})


class TestTimezoneViewAgreesWithItself(unittest.TestCase):
    """Fix 2: the legacy view never pairs one zone's code with another's offset."""

    def _timezone_for(self, location, config=_KANSAS_CITY):
        sess = Session("s", location=location)
        with patch.object(session_module, "Configuration", return_value=config):
            return sess.location_preferences["timezone"]

    def test_a_session_zone_supplies_every_field_of_the_block(self):
        tz = self._timezone_for(_LISBON_SESSION)
        self.assertEqual(tz["code"], "Europe/Lisbon")
        # Lisbon's standard offset is UTC+0. The master's is -21600000, and
        # that number must not appear here.
        self.assertEqual(tz["offset"], 0)
        self.assertIn(tz["dstOffset"], (0, 3600000))
        self.assertNotEqual(tz["name"], "Central Standard Time")

    def test_the_block_is_internally_consistent(self):
        """The defect stated as a property, not as one expected value."""
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        tz = self._timezone_for(_LISBON_SESSION)
        now = datetime.now(ZoneInfo(tz["code"]))
        dst = now.dst() or timedelta(0)
        self.assertEqual(tz["offset"],
                         int((now.utcoffset() - dst).total_seconds() * 1000))
        self.assertEqual(tz["dstOffset"], int(dst.total_seconds() * 1000))

    def test_a_session_with_no_zone_keeps_the_reading_box_block(self):
        tz = self._timezone_for({})
        self.assertEqual(tz, _KANSAS_CITY["location"]["timezone"])

    def test_an_unknown_zone_returns_the_code_alone(self):
        """Better a block with one field than a code wearing another zone's offsets."""
        tz = self._timezone_for({"tz": "Mars/Olympus"})
        self.assertEqual(tz, {"code": "Mars/Olympus"})

    def test_the_rest_of_the_legacy_view_is_unchanged(self):
        sess = Session("s", location=_LISBON_SESSION)
        with patch.object(session_module, "Configuration",
                          return_value=_KANSAS_CITY):
            view = sess.location_preferences
        # the coordinate is the session's
        self.assertEqual(view["coordinate"],
                         {"latitude": 38.7223, "longitude": -9.1393})
        # the city stays the reading box's: it has no slot in the §3.5 field
        self.assertEqual(view["city"], _KANSAS_CITY["location"]["city"])


if __name__ == "__main__":
    unittest.main()
