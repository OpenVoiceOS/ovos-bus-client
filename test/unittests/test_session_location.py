"""OVOS-SESSION-1 §3.5 ``location`` -- the {lat, lon, tz} carrier.

``location`` is a client-owned field: §3.5 makes the client the authoritative
source, and §2.1 resolves an OMITTED value at each consumer against THAT
consumer's deployment default. Across a HiveMind link the consumer is the
master, so an omitting satellite gets the master's location.

The two constructions are therefore different (ruling T-2292 on §4.1):

- the session ORIGIN (a direct ``Session(...)`` call) stamps its configured
  location, because omission cannot declare it;
- a session rebuilt from a RECEIVED carrier (``deserialize``) keeps only what
  the carrier carried. §4.1 forbids the reading process, which is not the
  origin, from synthesizing a field it did not receive. The deployment
  fallback stays a READ-time projection there (mirroring how
  ``Session.timezone`` falls back to config without storing it).
"""
import unittest
from unittest.mock import patch

import ovos_bus_client.session as session_module
from ovos_bus_client.session import Session

_CONFIG_WITH_LOCATION = {
    "location": {
        "city": {"code": "Lisbon", "name": "Lisbon",
                 "state": {"code": "LX", "name": "Lisbon",
                          "country": {"code": "PT", "name": "Portugal"}}},
        "coordinate": {"latitude": 38.7167, "longitude": -9.1333},
        "timezone": {"code": "Europe/Lisbon", "name": "Western European Time",
                    "dstOffset": 3600000, "offset": 0},
    },
}

_CONFIG_OTHER_LOCATION = {
    "location": {
        "city": {"code": "Kansas City", "name": "Kansas City"},
        "coordinate": {"latitude": 38.9717, "longitude": -95.2353},
        "timezone": {"code": "America/Chicago", "name": "Central Standard Time"},
    },
}


class TestLocationNeverMaterializedFromConfig(unittest.TestCase):
    """§4.1: a RECEIVED carrier's missing location MUST NOT be filled in."""

    def test_no_location_carrier_serializes_without_location_key(self):
        # Reviewer-proved regression: a no-location carrier must NOT come back
        # out re-serialized with the deployment's own coordinates, or a
        # HiveMind hop would permanently stamp the first node's location onto
        # every session that passes through it.
        with patch.object(session_module, "Configuration",
                          return_value=_CONFIG_WITH_LOCATION):
            sess = Session.deserialize({"session_id": "s1"})
        self.assertEqual(sess.location, {})
        data = sess.serialize()
        self.assertNotIn("location", data)

    def test_deserialize_with_no_config_at_all_stores_empty(self):
        with patch.object(session_module, "Configuration", return_value={}):
            sess = Session.deserialize({"session_id": "s1"})
        self.assertEqual(sess.location, {})


class TestOriginStampsItsConfiguredLocation(unittest.TestCase):
    """§3.5 + T-2292: the origin declares its own configured location."""

    def test_constructing_without_location_prefs_stamps_config(self):
        with patch.object(session_module, "Configuration",
                          return_value=_CONFIG_WITH_LOCATION):
            sess = Session()
        self.assertEqual(sess.location, {"lat": 38.7167, "lon": -9.1333,
                                         "tz": "Europe/Lisbon"})

    def test_the_stamp_reaches_the_wire(self):
        # The point of the stamp: the value must be ON the carrier, because a
        # consumer resolves an omitted location against its OWN configuration.
        with patch.object(session_module, "Configuration",
                          return_value=_CONFIG_WITH_LOCATION):
            data = Session("sat-1").serialize()
        self.assertEqual(data["location"]["lat"], 38.7167)
        self.assertEqual(data["location"]["tz"], "Europe/Lisbon")

    def test_a_far_end_with_a_different_config_reads_the_satellite_value(self):
        # The HiveMind-voice-sat#56 shape, in one process: the satellite
        # serializes under its own configuration, the master deserializes
        # under its own, and the master must see the satellite's location.
        with patch.object(session_module, "Configuration",
                          return_value=_CONFIG_WITH_LOCATION):
            carrier = Session("sat-1").serialize()
        with patch.object(session_module, "Configuration",
                          return_value=_CONFIG_OTHER_LOCATION):
            at_master = Session.deserialize(carrier)
            self.assertEqual(at_master.timezone, "Europe/Lisbon")
        self.assertEqual(at_master.location["lat"], 38.7167)

    def test_explicit_location_wins_over_the_configured_one(self):
        with patch.object(session_module, "Configuration",
                          return_value=_CONFIG_WITH_LOCATION):
            sess = Session(location_prefs={"tz": "Asia/Tokyo"})
        self.assertEqual(sess.location, {"tz": "Asia/Tokyo"})

    def test_no_configured_location_stamps_nothing(self):
        with patch.object(session_module, "Configuration", return_value={}):
            sess = Session()
        self.assertEqual(sess.location, {})
        self.assertNotIn("location", sess.serialize())

    def test_a_non_numeric_coordinate_is_dropped_not_declared(self):
        # §3.5 keys are typed; a string latitude is not declared as one.
        cfg = {"location": {"coordinate": {"latitude": "not-a-number",
                                           "longitude": -9.1333},
                            "timezone": {"code": "Europe/Lisbon"}}}
        with patch.object(session_module, "Configuration", return_value=cfg):
            sess = Session()
        self.assertEqual(sess.location, {"lon": -9.1333,
                                         "tz": "Europe/Lisbon"})

    def test_a_string_coordinate_is_coerced(self):
        cfg = {"location": {"coordinate": {"latitude": "38.7167",
                                           "longitude": "-9.1333"},
                            "timezone": {"code": "Europe/Lisbon"}}}
        with patch.object(session_module, "Configuration", return_value=cfg):
            sess = Session()
        self.assertEqual(sess.location, {"lat": 38.7167, "lon": -9.1333,
                                         "tz": "Europe/Lisbon"})

    def test_empty_location_object_stores_empty_not_config(self):
        # §2.1: an object with none of the three keys is equivalent to an
        # omitted location -- it still must not be materialized into state.
        with patch.object(session_module, "Configuration",
                          return_value=_CONFIG_WITH_LOCATION):
            sess = Session.deserialize({"session_id": "s1", "location": {}})
        self.assertEqual(sess.location, {})
        self.assertNotIn("location", sess.serialize())

    def test_all_malformed_location_stores_empty_not_config(self):
        with patch.object(session_module, "Configuration",
                          return_value=_CONFIG_WITH_LOCATION):
            sess = Session.deserialize({"session_id": "s1",
                                       "location": {"lat": "x", "tz": 123}})
        self.assertEqual(sess.location, {})
        self.assertNotIn("location", sess.serialize())


class TestRoundTrip(unittest.TestCase):
    def test_no_location_key_round_trips_to_no_location_key(self):
        sess = Session.deserialize({"session_id": "s1"})
        data = sess.serialize()
        self.assertNotIn("location", data)

    def test_partial_object_round_trips_byte_stable(self):
        sess = Session.deserialize({"location": {"tz": "Asia/Tokyo"}})
        data = sess.serialize()
        # the legacy nested mycroft.conf projection (location.timezone.code)
        # is emitted alongside the flat keys for one stable cycle
        self.assertEqual(data["location"],
                         {"tz": "Asia/Tokyo", "timezone": {"code": "Asia/Tokyo"}})

    def test_full_object_round_trips_byte_stable(self):
        sess = Session.deserialize({"location": {"lat": 1.5, "lon": -2.5,
                                                  "tz": "Europe/Lisbon"}})
        self.assertEqual(sess.serialize()["location"],
                         {"lat": 1.5, "lon": -2.5, "tz": "Europe/Lisbon",
                          "timezone": {"code": "Europe/Lisbon"}})


class TestLocationIngest(unittest.TestCase):
    def test_spec_shape_round_trips(self):
        sess = Session.deserialize({"session_id": "s1",
                                    "location": {"lat": 1.5, "lon": -2.5,
                                                "tz": "Europe/Lisbon"}})
        self.assertEqual(sess.location, {"lat": 1.5, "lon": -2.5,
                                         "tz": "Europe/Lisbon"})

    def test_legacy_nested_shape_normalizes_and_warns_once(self):
        legacy = {
            "coordinate": {"latitude": 38.7167, "longitude": -9.1333},
            "timezone": {"code": "Europe/Lisbon"},
        }
        with patch.object(session_module, "log_deprecation") as mock_warn:
            sess = Session.deserialize({"session_id": "s1", "location": legacy})
        self.assertEqual(sess.location, {"lat": 38.7167, "lon": -9.1333,
                                         "tz": "Europe/Lisbon"})
        mock_warn.assert_called_once()
        self.assertIn("nested", mock_warn.call_args[0][0])

    def test_malformed_keys_dropped_rest_kept(self):
        sess = Session.deserialize({
            "session_id": "s1",
            "location": {"lat": "x", "lon": 200, "tz": 123},
        })
        self.assertEqual(sess.location, {})

        sess = Session.deserialize({
            "session_id": "s1",
            "location": {"lat": 95, "lon": 10.0, "tz": 123},
        })
        self.assertEqual(sess.location, {"lon": 10.0})

    def test_unlisted_keys_tolerated_but_not_reemitted(self):
        sess = Session.deserialize({
            "session_id": "s1",
            "location": {"lat": 1.0, "city": "Lisbon"},
        })
        self.assertEqual(sess.location, {"lat": 1.0})
        self.assertNotIn("city", sess.serialize()["location"])


class TestTimezoneProperty(unittest.TestCase):
    def test_prefers_session_location_tz(self):
        with patch.object(session_module, "Configuration",
                          return_value=_CONFIG_WITH_LOCATION):
            sess = Session.deserialize({"session_id": "s1",
                                       "location": {"tz": "America/New_York"}})
            self.assertEqual(sess.timezone, "America/New_York")

    def test_falls_back_to_deployment_timezone_without_mutating_state(self):
        with patch.object(session_module, "Configuration",
                          return_value=_CONFIG_WITH_LOCATION):
            sess = Session.deserialize({"session_id": "s1"})
            self.assertEqual(sess.timezone, "Europe/Lisbon")
        # the fallback is read-time only -- stored state stays empty.
        self.assertEqual(sess.location, {})

    def test_no_config_and_no_location_is_none_without_mutating_state(self):
        with patch.object(session_module, "Configuration", return_value={}):
            sess = Session.deserialize({"session_id": "s1"})
            self.assertIsNone(sess.timezone)
        self.assertEqual(sess.location, {})


class TestLocationPreferencesLegacyView(unittest.TestCase):
    def test_reconstructs_nested_shape_from_three_keys(self):
        with patch.object(session_module, "Configuration",
                          return_value=_CONFIG_WITH_LOCATION):
            sess = Session.deserialize({"session_id": "s1",
                                       "location": {"lat": 1.0, "lon": 2.0,
                                                    "tz": "America/New_York"}})
            prefs = sess.location_preferences
        self.assertEqual(prefs["coordinate"]["latitude"], 1.0)
        self.assertEqual(prefs["coordinate"]["longitude"], 2.0)
        self.assertEqual(prefs["timezone"]["code"], "America/New_York")
        # city/state/country have no slot on the three-key field: read from config.
        self.assertEqual(prefs["city"], _CONFIG_WITH_LOCATION["location"]["city"])

    def test_view_on_empty_location_returns_config_derived_dict_unstored(self):
        with patch.object(session_module, "Configuration",
                          return_value=_CONFIG_WITH_LOCATION):
            sess = Session.deserialize({"session_id": "s1"})
            prefs = sess.location_preferences
        self.assertEqual(prefs["coordinate"]["latitude"], 38.7167)
        self.assertEqual(prefs["timezone"]["code"], "Europe/Lisbon")
        # the view is computed at read time -- it must never be written back.
        self.assertEqual(sess.location, {})

    def test_setter_normalizes_legacy_nested_input(self):
        sess = Session()
        sess.location_preferences = {
            "coordinate": {"latitude": 5.0, "longitude": 6.0},
            "timezone": {"code": "Europe/Lisbon"},
        }
        self.assertEqual(sess.location, {"lat": 5.0, "lon": 6.0,
                                         "tz": "Europe/Lisbon"})

    def test_setter_with_only_config_default_stores_empty_not_config(self):
        with patch.object(session_module, "Configuration",
                          return_value=_CONFIG_WITH_LOCATION):
            sess = Session()
            sess.location_preferences = {"timezone": {}}
        self.assertEqual(sess.location, {})


if __name__ == '__main__':
    unittest.main()
