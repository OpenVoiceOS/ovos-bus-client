"""More coverage for OCPInterface — queue/play/populate_search_results."""
import unittest
from unittest import TestCase
from unittest.mock import MagicMock

from ovos_bus_client.apis.ocp import OCPInterface
from ovos_bus_client.message import Message


def _last(bus) -> Message:
    return bus.emit.call_args[0][0]


def _track_dicts():
    return [{"uri": "https://a", "title": "A", "match_confidence": 90},
            {"uri": "https://b", "title": "B", "match_confidence": 50}]


class TestOCPInterfaceQueueAndPlay(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.ocp = OCPInterface(bus=self.bus)
        self.src = Message("ctx", context={"session": {"session_id": "k"}})

    def test_queue_dict_tracks(self):
        self.ocp.queue(_track_dicts(), source_message=self.src)
        emitted = _last(self.bus)
        self.assertEqual(emitted.msg_type, "ovos.common_play.playlist.queue")
        self.assertEqual(len(emitted.data["tracks"]), 2)

    def test_populate_search_results_replace(self):
        self.ocp.populate_search_results(_track_dicts(),
                                         replace=True,
                                         source_message=self.src)
        emitted = _last(self.bus)
        self.assertEqual(emitted.msg_type, "ovos.common_play.search.populate")
        self.assertTrue(emitted.data["replace"])
        self.assertEqual(len(emitted.data["playlist"]), 2)

    def test_populate_search_results_extend(self):
        self.ocp.populate_search_results(_track_dicts(),
                                         replace=False,
                                         source_message=self.src)
        self.assertFalse(_last(self.bus).data["replace"])

    def test_play_simple(self):
        self.ocp.play(_track_dicts(), source_message=self.src)
        emitted = _last(self.bus)
        self.assertEqual(emitted.msg_type, "ovos.common_play.play")
        self.assertIn("media", emitted.data)
        self.assertIn("playlist", emitted.data)


if __name__ == "__main__":
    unittest.main()
