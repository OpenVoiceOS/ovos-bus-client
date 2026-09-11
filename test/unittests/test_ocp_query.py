"""Coverage tests for ovos_bus_client.apis.ocp.OCPQuery."""
import time
import unittest
from unittest import TestCase
from unittest.mock import MagicMock, patch

from ovos_bus_client.message import Message


def _make_query():
    from ovos_bus_client.apis.ocp import OCPQuery
    from ovos_utils.ocp import MediaType
    bus = MagicMock()
    # is_gui_running / is_gui_connected called inside reset(); patch to False
    with patch("ovos_bus_client.apis.ocp.is_gui_running", return_value=False), \
         patch("ovos_bus_client.apis.ocp.is_gui_connected", return_value=False):
        q = OCPQuery("play music", bus, media_type=MediaType.GENERIC)
    return q, bus


class TestOCPQueryConstruction(TestCase):
    def test_construct(self):
        q, _ = _make_query()
        self.assertEqual(q.query, "play music")
        self.assertEqual(q.active_skills, {})
        self.assertFalse(q.searching)
        self.assertEqual(q.query_replies, [])


class TestOCPQuerySend(TestCase):
    def test_send_no_skill_id(self):
        q, bus = _make_query()
        src = Message("recognizer_loop:utterance", context={})
        q.send(source_message=src)
        # query message emitted
        emitted_types = [c.args[0].msg_type for c in bus.emit.call_args_list]
        self.assertIn("ovos.common_play.query", emitted_types)
        self.assertTrue(q.searching)

    def test_send_with_skill_id(self):
        q, bus = _make_query()
        src = Message("recognizer_loop:utterance", context={})
        q.send(skill_id="my.music.skill", source_message=src)
        emitted = bus.emit.call_args[0][0]
        self.assertEqual(emitted.msg_type, "ovos.common_play.query.my.music.skill")


class TestOCPQueryHandlers(TestCase):
    def setUp(self):
        self.q, self.bus = _make_query()

    def test_handle_search_start_registers_skill(self):
        msg = Message("ovos.common_play.skill.search_start",
                      {"skill_id": "music.skill"})
        self.q.handle_skill_search_start(msg)
        self.assertIn("music.skill", self.q.active_skills)

    def test_handle_response_for_wrong_phrase_ignored(self):
        self.q.searching = True
        msg = Message("ovos.common_play.query.response", {
            "phrase": "different query",
            "skill_id": "x", "results": [{}],
        })
        self.q.handle_skill_response(msg)
        self.assertEqual(self.q.query_replies, [])

    def test_handle_response_collects_results(self):
        self.q.searching = True
        msg = Message("ovos.common_play.query.response", {
            "phrase": "play music",
            "skill_id": "music.skill",
            "results": [{"uri": "x", "match_confidence": 50}],
        })
        self.q.handle_skill_response(msg)
        self.assertEqual(len(self.q.query_replies), 1)

    def test_handle_response_extends_timeout_when_searching_flag(self):
        self.q.searching = True
        baseline = self.q.query_timeouts
        msg = Message("ovos.common_play.query.response", {
            "phrase": "play music",
            "skill_id": "music.skill",
            "searching": True,
            "timeout": 2,
        })
        self.q.handle_skill_response(msg)
        self.assertGreater(self.q.query_timeouts, baseline)

    def test_handle_response_high_confidence_stops_search(self):
        self.q.searching = True
        self.q.search_start = time.time()
        msg = Message("ovos.common_play.query.response", {
            "phrase": "play music",
            "skill_id": "music.skill",
            "results": [{"uri": "x", "match_confidence": 99}],
        })
        # disable grace period so we don't block the test
        self.q.config = {"early_stop_grace_period": 0}
        self.q.handle_skill_response(msg)
        self.assertFalse(self.q.searching)

    def test_handle_search_end_removes_skill(self):
        self.q.active_skills["music.skill"] = MagicMock()
        # the lock is actually expected to be a real lock; replace with one
        import threading
        self.q.active_skills["music.skill"] = threading.Lock()
        self.q.handle_skill_search_end(
            Message("e", {"skill_id": "music.skill"})
        )
        self.assertNotIn("music.skill", self.q.active_skills)

    def test_results_filters_empty(self):
        self.q.query_replies = [
            {"results": [1]},
            {"results": []},
            {"other": "x"},
        ]
        self.assertEqual(self.q.results, [{"results": [1]}])


class TestOCPQueryEventLifecycle(TestCase):
    def test_register_and_remove_events(self):
        q, bus = _make_query()
        q.register_events()
        self.assertEqual(bus.on.call_count, 3)
        q.remove_events()
        self.assertEqual(bus.remove_all_listeners.call_count, 3)


class TestOCPQueryWait(TestCase):
    def test_wait_returns_when_searching_false(self):
        q, _ = _make_query()
        q.searching = False
        q.search_start = time.time()
        q.wait()  # should return immediately

    def test_wait_times_out(self):
        q, _ = _make_query()
        q.searching = True
        q.search_start = time.time() - 100  # way past
        q.wait()
        self.assertFalse(q.searching)


if __name__ == "__main__":
    unittest.main()
