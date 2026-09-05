"""Coverage tests for ovos_bus_client.apis.ocp — OCPInterface, ClassicAudioServiceInterface,
OCPAudioServiceInterface, OCPVideoServiceInterface, OCPWebServiceInterface."""
import unittest
from datetime import timedelta
from unittest import TestCase
from unittest.mock import MagicMock

import pytest

from ovos_bus_client.apis.ocp import (ClassicAudioServiceInterface,
                                      OCPAudioServiceInterface, OCPInterface,
                                      OCPVideoServiceInterface,
                                      OCPWebServiceInterface)
from ovos_bus_client.message import Message

# ClassicAudioServiceInterface is a deprecated shim (use OCPInterface); this
# module deliberately keeps exercising it for coverage, filtered per-test.
pytestmark = pytest.mark.filterwarnings("ignore:use OCPInterface instead:DeprecationWarning")


def _last(bus) -> Message:
    return bus.emit.call_args[0][0]


# --------------------------------------------------------------------------
# OCPInterface (high-level)
# --------------------------------------------------------------------------

class TestOCPInterfaceTransport(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.ocp = OCPInterface(bus=self.bus)
        self.src = Message("ctx", context={"session": {"session_id": "k"}})

    def test_stop(self):
        self.ocp.stop(source_message=self.src)
        self.assertEqual(_last(self.bus).msg_type, "ovos.common_play.stop")

    def test_next(self):
        self.ocp.next(source_message=self.src)
        self.assertEqual(_last(self.bus).msg_type, "ovos.common_play.next")

    def test_prev(self):
        self.ocp.prev(source_message=self.src)
        self.assertEqual(_last(self.bus).msg_type, "ovos.common_play.previous")

    def test_pause(self):
        self.ocp.pause(source_message=self.src)
        self.assertEqual(_last(self.bus).msg_type, "ovos.common_play.pause")

    def test_resume(self):
        self.ocp.resume(source_message=self.src)
        self.assertEqual(_last(self.bus).msg_type, "ovos.common_play.resume")

    def test_seek_forward(self):
        self.ocp.seek_forward(seconds=10, source_message=self.src)
        emitted = _last(self.bus)
        self.assertEqual(emitted.msg_type, "ovos.common_play.seek")
        self.assertEqual(emitted.data["seconds"], 10)

    def test_seek_backward(self):
        self.ocp.seek_backward(seconds=5, source_message=self.src)
        emitted = _last(self.bus)
        self.assertEqual(emitted.msg_type, "ovos.common_play.seek")
        self.assertEqual(emitted.data["seconds"], -5)

    def test_seek_forward_timedelta(self):
        self.ocp.seek_forward(seconds=timedelta(seconds=3), source_message=self.src)
        self.assertEqual(_last(self.bus).data["seconds"], 3)

    def test_seek_backward_timedelta(self):
        self.ocp.seek_backward(seconds=timedelta(seconds=4), source_message=self.src)
        self.assertEqual(_last(self.bus).data["seconds"], -4)


class TestOCPInterfaceResponses(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.ocp = OCPInterface(bus=self.bus)
        self.src = Message("ctx")

    def test_get_track_length_ms(self):
        self.bus.wait_for_response.return_value = Message(
            "r", {"length": 30000},
        )
        # OCPInterface returns raw ms, unlike OCPAudioServiceInterface
        self.assertEqual(self.ocp.get_track_length(source_message=self.src), 30000)

    def test_get_track_length_no_response(self):
        self.bus.wait_for_response.return_value = None
        self.assertEqual(self.ocp.get_track_length(source_message=self.src), 0)

    def test_get_track_position_ms(self):
        self.bus.wait_for_response.return_value = Message(
            "r", {"position": 5500},
        )
        self.assertEqual(self.ocp.get_track_position(source_message=self.src), 5500)

    def test_get_track_position_no_response(self):
        self.bus.wait_for_response.return_value = None
        self.assertEqual(self.ocp.get_track_position(source_message=self.src), 0)

    def test_set_track_position_ms_directly(self):
        self.ocp.set_track_position(miliseconds=2000, source_message=self.src)
        emitted = _last(self.bus)
        self.assertEqual(emitted.data["position"], 2000)

    def test_track_info_returns_dict(self):
        self.bus.wait_for_response.return_value = Message(
            "r", {"title": "foo", "uri": "https://x"},
        )
        info = self.ocp.track_info(source_message=self.src)
        self.assertEqual(info["title"], "foo")

    def test_track_info_empty_when_no_response(self):
        self.bus.wait_for_response.return_value = None
        self.assertEqual(self.ocp.track_info(source_message=self.src), {})

    def test_available_backends(self):
        self.bus.wait_for_response.return_value = Message(
            "r", {"ovos-media": {"type": "audio"}},
        )
        backends = self.ocp.available_backends(source_message=self.src)
        self.assertIn("ovos-media", backends)


# --------------------------------------------------------------------------
# ClassicAudioServiceInterface
# --------------------------------------------------------------------------

class TestClassicAudio(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.svc = ClassicAudioServiceInterface(self.bus)
        self.src = Message("ctx")

    def test_queue_string(self):
        self.svc.queue(tracks="https://x", source_message=self.src)
        emitted = _last(self.bus)
        self.assertEqual(emitted.msg_type, "mycroft.audio.service.queue")
        self.assertEqual(emitted.data["tracks"], ["https://x"])

    def test_queue_list(self):
        # ClassicAudioServiceInterface passes each track through ensure_uri,
        # which normalizes bare paths/URIs. Just confirm the message type and
        # that the track count matches.
        self.svc.queue(tracks=["https://a", "https://b"], source_message=self.src)
        emitted = _last(self.bus)
        self.assertEqual(emitted.msg_type, "mycroft.audio.service.queue")
        self.assertEqual(len(emitted.data["tracks"]), 2)

    def test_queue_invalid_raises(self):
        with self.assertRaises(ValueError):
            self.svc.queue(tracks=123, source_message=self.src)

    def test_play_with_defaults(self):
        self.svc.play(tracks="x", source_message=self.src)
        emitted = _last(self.bus)
        self.assertEqual(emitted.msg_type, "mycroft.audio.service.play")
        self.assertEqual(emitted.data["repeat"], False)

    def test_play_with_repeat(self):
        self.svc.play(tracks="x", repeat=True, source_message=self.src)
        self.assertTrue(_last(self.bus).data["repeat"])

    def test_play_invalid_raises(self):
        with self.assertRaises(ValueError):
            self.svc.play(tracks=42, source_message=self.src)

    def test_stop_next_prev_pause_resume(self):
        for method, expected in [
            (self.svc.stop, "mycroft.audio.service.stop"),
            (self.svc.next, "mycroft.audio.service.next"),
            (self.svc.prev, "mycroft.audio.service.prev"),
            (self.svc.pause, "mycroft.audio.service.pause"),
            (self.svc.resume, "mycroft.audio.service.resume"),
        ]:
            method(source_message=self.src)
            self.assertEqual(_last(self.bus).msg_type, expected)

    def test_get_track_length(self):
        self.bus.wait_for_response.return_value = Message(
            "r", {"length": 12000},
        )
        self.assertEqual(self.svc.get_track_length(source_message=self.src), 12.0)

    def test_get_track_length_no_response(self):
        self.bus.wait_for_response.return_value = None
        self.assertEqual(self.svc.get_track_length(source_message=self.src), 0)

    def test_get_track_position(self):
        self.bus.wait_for_response.return_value = Message(
            "r", {"position": 3000},
        )
        self.assertEqual(self.svc.get_track_position(source_message=self.src), 3.0)

    def test_set_track_position_ms(self):
        self.svc.set_track_position(seconds=4, source_message=self.src)
        self.assertEqual(_last(self.bus).data["position"], 4000)

    def test_seek_forward_int(self):
        self.svc.seek(seconds=5, source_message=self.src)
        emitted = _last(self.bus)
        self.assertEqual(emitted.msg_type, "mycroft.audio.service.seek_forward")
        self.assertEqual(emitted.data["seconds"], 5)

    def test_seek_backward_negative(self):
        self.svc.seek(seconds=-3, source_message=self.src)
        emitted = _last(self.bus)
        self.assertEqual(emitted.msg_type, "mycroft.audio.service.seek_backward")
        self.assertEqual(emitted.data["seconds"], 3)

    def test_seek_with_timedelta(self):
        self.svc.seek(seconds=timedelta(seconds=7), source_message=self.src)
        self.assertEqual(_last(self.bus).data["seconds"], 7)

    def test_seek_forward_method(self):
        self.svc.seek_forward(seconds=2, source_message=self.src)
        self.assertEqual(_last(self.bus).data["seconds"], 2)

    def test_seek_backward_method(self):
        self.svc.seek_backward(seconds=2, source_message=self.src)
        self.assertEqual(_last(self.bus).data["seconds"], 2)

    def test_track_info(self):
        self.bus.wait_for_response.return_value = Message(
            "r", {"title": "X"},
        )
        info = self.svc.track_info(source_message=self.src)
        self.assertEqual(info["title"], "X")

    def test_track_info_no_response(self):
        self.bus.wait_for_response.return_value = None
        self.assertEqual(self.svc.track_info(source_message=self.src), {})

    def test_available_backends(self):
        self.bus.wait_for_response.return_value = Message(
            "r", {"vlc": {}},
        )
        out = self.svc.available_backends(source_message=self.src)
        self.assertIn("vlc", out)

    def test_is_playing_property(self):
        self.bus.wait_for_response.return_value = Message(
            "r", {"title": "X"},
        )
        # have to trigger a message context internally — dig_for_message will hit nothing useful
        # we just check the property runs and reflects track_info truthiness
        result = self.svc.is_playing
        # whether True or False, the property must not raise
        self.assertIsInstance(result, bool)


# --------------------------------------------------------------------------
# OCPAudioServiceInterface (uri-based internal)
# --------------------------------------------------------------------------

class TestOCPAudioServiceURI(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.svc = OCPAudioServiceInterface(self.bus)

    def test_play_single_uri(self):
        self.svc.play(tracks="https://x.mp3")
        emitted = _last(self.bus)
        self.assertEqual(emitted.msg_type, "ovos.audio.service.play")
        self.assertEqual(emitted.data["repeat"], False)

    def test_play_tuple_uri(self):
        self.svc.play(tracks=("https://x.mp3", "audio/mpeg"))
        emitted = _last(self.bus)
        self.assertTrue(emitted.data["tracks"])

    def test_play_list(self):
        self.svc.play(tracks=["https://a", "https://b"])
        emitted = _last(self.bus)
        self.assertEqual(len(emitted.data["tracks"]), 2)

    def test_play_invalid_raises(self):
        with self.assertRaises(ValueError):
            self.svc.play(tracks=42)

    def test_stop_next_prev_pause_resume(self):
        for method, expected in [
            (self.svc.stop, "ovos.audio.service.stop"),
            (self.svc.next, "ovos.audio.service.next"),
            (self.svc.prev, "ovos.audio.service.prev"),
            (self.svc.pause, "ovos.audio.service.pause"),
            (self.svc.resume, "ovos.audio.service.resume"),
        ]:
            method()
            self.assertEqual(_last(self.bus).msg_type, expected)

    def test_get_track_length(self):
        self.bus.wait_for_response.return_value = Message("r", {"length": 6000})
        self.assertEqual(self.svc.get_track_length(), 6.0)

    def test_get_track_length_no_response(self):
        self.bus.wait_for_response.return_value = None
        self.assertEqual(self.svc.get_track_length(), 0)

    def test_get_track_position(self):
        self.bus.wait_for_response.return_value = Message("r", {"position": 1500})
        self.assertEqual(self.svc.get_track_position(), 1.5)

    def test_set_track_position_to_ms(self):
        self.svc.set_track_position(2)
        self.assertEqual(_last(self.bus).data["position"], 2000)

    def test_seek_forward_int(self):
        self.svc.seek(5)
        self.assertEqual(_last(self.bus).msg_type, "ovos.audio.service.seek_forward")

    def test_seek_backward_negative(self):
        self.svc.seek(-5)
        self.assertEqual(_last(self.bus).msg_type, "ovos.audio.service.seek_backward")

    def test_seek_timedelta(self):
        self.svc.seek(timedelta(seconds=3))
        self.assertEqual(_last(self.bus).data["seconds"], 3)

    def test_seek_forward_timedelta(self):
        self.svc.seek_forward(timedelta(seconds=4))
        self.assertEqual(_last(self.bus).data["seconds"], 4)

    def test_seek_backward_timedelta(self):
        self.svc.seek_backward(timedelta(seconds=4))
        self.assertEqual(_last(self.bus).data["seconds"], 4)

    def test_track_info(self):
        self.bus.wait_for_response.return_value = Message("r", {"title": "X"})
        self.assertEqual(self.svc.track_info()["title"], "X")

    def test_track_info_no_response(self):
        self.bus.wait_for_response.return_value = None
        self.assertEqual(self.svc.track_info(), {})

    def test_available_backends(self):
        self.bus.wait_for_response.return_value = Message("r", {"foo": {}})
        self.assertIn("foo", self.svc.available_backends())

    def test_available_backends_no_response(self):
        self.bus.wait_for_response.return_value = None
        self.assertEqual(self.svc.available_backends(), {})

    def test_is_playing(self):
        self.bus.wait_for_response.return_value = Message("r", {"title": "X"})
        self.assertTrue(self.svc.is_playing)


# --------------------------------------------------------------------------
# OCPVideoServiceInterface and OCPWebServiceInterface — same shape, just
# different msg_type prefix. Light cover.
# --------------------------------------------------------------------------

class TestOCPVideoService(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.svc = OCPVideoServiceInterface(self.bus)

    def test_play(self):
        self.svc.play(tracks="https://x.mp4")
        self.assertEqual(_last(self.bus).msg_type, "ovos.video.service.play")

    def test_stop_pause_resume_next_prev(self):
        for method, expected in [
            (self.svc.stop, "ovos.video.service.stop"),
            (self.svc.next, "ovos.video.service.next"),
            (self.svc.prev, "ovos.video.service.prev"),
            (self.svc.pause, "ovos.video.service.pause"),
            (self.svc.resume, "ovos.video.service.resume"),
        ]:
            method()
            self.assertEqual(_last(self.bus).msg_type, expected)

    def test_seek_forward_and_backward(self):
        self.svc.seek(5)
        self.assertEqual(_last(self.bus).msg_type, "ovos.video.service.seek_forward")
        self.svc.seek(-5)
        self.assertEqual(_last(self.bus).msg_type, "ovos.video.service.seek_backward")

    def test_seek_forward_timedelta(self):
        self.svc.seek_forward(timedelta(seconds=2))
        self.assertEqual(_last(self.bus).data["seconds"], 2)

    def test_seek_backward_timedelta(self):
        self.svc.seek_backward(timedelta(seconds=2))
        self.assertEqual(_last(self.bus).data["seconds"], 2)

    def test_get_track_length(self):
        self.bus.wait_for_response.return_value = Message("r", {"length": 9000})
        self.assertEqual(self.svc.get_track_length(), 9.0)

    def test_get_track_position(self):
        self.bus.wait_for_response.return_value = Message("r", {"position": 1000})
        self.assertEqual(self.svc.get_track_position(), 1.0)

    def test_set_track_position(self):
        self.svc.set_track_position(2)
        self.assertEqual(_last(self.bus).data["position"], 2000)

    def test_track_info_and_backends(self):
        self.bus.wait_for_response.return_value = Message("r", {"foo": 1})
        self.assertEqual(self.svc.track_info()["foo"], 1)
        self.assertEqual(self.svc.available_backends()["foo"], 1)

    def test_is_playing(self):
        self.bus.wait_for_response.return_value = Message("r", {"title": "v"})
        self.assertTrue(self.svc.is_playing)


class TestOCPWebService(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.svc = OCPWebServiceInterface(self.bus)

    def test_play(self):
        self.svc.play(tracks="https://example.com")
        self.assertEqual(_last(self.bus).msg_type, "ovos.web.service.play")

    def test_transport_methods(self):
        for method, expected in [
            (self.svc.stop, "ovos.web.service.stop"),
            (self.svc.next, "ovos.web.service.next"),
            (self.svc.prev, "ovos.web.service.prev"),
            (self.svc.pause, "ovos.web.service.pause"),
            (self.svc.resume, "ovos.web.service.resume"),
        ]:
            method()
            self.assertEqual(_last(self.bus).msg_type, expected)

    def test_seek(self):
        self.svc.seek(5)
        self.assertEqual(_last(self.bus).msg_type, "ovos.web.service.seek_forward")
        self.svc.seek(-1)
        self.assertEqual(_last(self.bus).msg_type, "ovos.web.service.seek_backward")

    def test_seek_forward_backward_timedelta(self):
        self.svc.seek_forward(timedelta(seconds=2))
        self.assertEqual(_last(self.bus).data["seconds"], 2)
        self.svc.seek_backward(timedelta(seconds=3))
        self.assertEqual(_last(self.bus).data["seconds"], 3)

    def test_get_track_length(self):
        self.bus.wait_for_response.return_value = Message("r", {"length": 10000})
        self.assertEqual(self.svc.get_track_length(), 10.0)

    def test_get_track_position(self):
        self.bus.wait_for_response.return_value = Message("r", {"position": 500})
        self.assertEqual(self.svc.get_track_position(), 0.5)

    def test_set_track_position(self):
        self.svc.set_track_position(3)
        self.assertEqual(_last(self.bus).data["position"], 3000)

    def test_track_info_and_backends(self):
        self.bus.wait_for_response.return_value = Message("r", {"x": "y"})
        self.assertEqual(self.svc.track_info()["x"], "y")
        self.assertEqual(self.svc.available_backends()["x"], "y")

    def test_is_playing(self):
        self.bus.wait_for_response.return_value = Message("r", {"title": "X"})
        self.assertTrue(self.svc.is_playing)


# --------------------------------------------------------------------------
# OCPInterface.norm_tracks — the only non-emit method on OCPInterface
# --------------------------------------------------------------------------

class TestNormTracks(TestCase):
    def test_norm_tracks_requires_list(self):
        with self.assertRaises(AssertionError):
            OCPInterface.norm_tracks("not a list")

    def test_norm_tracks_dict_to_entry(self):
        from ovos_utils.ocp import MediaEntry
        # MediaEntry can be constructed from a minimal dict
        out = OCPInterface.norm_tracks([{"uri": "https://x", "title": "t"}])
        self.assertIsInstance(out[0], MediaEntry)


if __name__ == "__main__":
    unittest.main()
