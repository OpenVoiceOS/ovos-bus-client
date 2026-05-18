"""Coverage tests for ovos_bus_client.apis.enclosure — EnclosureAPI emitters."""
import unittest
from unittest import TestCase
from unittest.mock import MagicMock

from ovos_bus_client.apis.enclosure import EnclosureAPI
from ovos_bus_client.message import Message


def _last_emitted(bus) -> Message:
    return bus.emit.call_args[0][0]


class TestEnclosureAPIBasics(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.api = EnclosureAPI(bus=self.bus, skill_id="my.skill")

    def test_set_bus_and_id(self):
        new_bus = MagicMock()
        self.api.set_bus(new_bus)
        self.assertIs(self.api.bus, new_bus)
        self.api.set_id("other.skill")
        self.assertEqual(self.api.skill_id, "other.skill")

    def test_register(self):
        self.api.register("specified.skill")
        self.assertEqual(_last_emitted(self.bus).msg_type, "enclosure.active_skill")
        self.assertEqual(_last_emitted(self.bus).data["skill_id"], "specified.skill")

    def test_register_default_skill_id(self):
        self.api.register()
        self.assertEqual(_last_emitted(self.bus).data["skill_id"], "my.skill")

    def test_reset(self):
        self.api.reset()
        self.assertEqual(_last_emitted(self.bus).msg_type, "enclosure.reset")

    def test_system_reset(self):
        self.api.system_reset()
        self.assertEqual(_last_emitted(self.bus).msg_type, "enclosure.system.reset")

    def test_system_mute_unmute(self):
        self.api.system_mute()
        self.assertEqual(_last_emitted(self.bus).msg_type, "enclosure.system.mute")
        self.api.system_unmute()
        self.assertEqual(_last_emitted(self.bus).msg_type, "enclosure.system.unmute")

    def test_system_blink(self):
        self.api.system_blink(times=3)
        emitted = _last_emitted(self.bus)
        self.assertEqual(emitted.msg_type, "enclosure.system.blink")
        self.assertEqual(emitted.data["times"], 3)


class TestEnclosureEyes(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.api = EnclosureAPI(bus=self.bus, skill_id="s")

    def test_on_off(self):
        self.api.eyes_on()
        self.assertEqual(_last_emitted(self.bus).msg_type, "enclosure.eyes.on")
        self.api.eyes_off()
        self.assertEqual(_last_emitted(self.bus).msg_type, "enclosure.eyes.off")

    def test_blink(self):
        self.api.eyes_blink("b")
        emitted = _last_emitted(self.bus)
        self.assertEqual(emitted.msg_type, "enclosure.eyes.blink")
        self.assertEqual(emitted.data["side"], "b")

    def test_narrow(self):
        self.api.eyes_narrow()
        self.assertEqual(_last_emitted(self.bus).msg_type, "enclosure.eyes.narrow")

    def test_look(self):
        self.api.eyes_look("u")
        self.assertEqual(_last_emitted(self.bus).data["side"], "u")

    def test_color(self):
        self.api.eyes_color(r=10, g=20, b=30)
        emitted = _last_emitted(self.bus)
        self.assertEqual(emitted.msg_type, "enclosure.eyes.color")
        self.assertEqual(emitted.data, {"r": 10, "g": 20, "b": 30})

    def test_setpixel(self):
        self.api.eyes_setpixel(idx=5, r=1, g=2, b=3)
        emitted = _last_emitted(self.bus)
        self.assertEqual(emitted.msg_type, "enclosure.eyes.setpixel")
        self.assertEqual(emitted.data["idx"], 5)

    def test_setpixel_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            self.api.eyes_setpixel(idx=99)
        with self.assertRaises(ValueError):
            self.api.eyes_setpixel(idx=-1)

    def test_fill(self):
        self.api.eyes_fill(percentage=50)
        emitted = _last_emitted(self.bus)
        self.assertEqual(emitted.msg_type, "enclosure.eyes.fill")
        self.assertEqual(emitted.data["percentage"], 50)

    def test_fill_invalid(self):
        with self.assertRaises(ValueError):
            self.api.eyes_fill(101)
        with self.assertRaises(ValueError):
            self.api.eyes_fill(-1)

    def test_brightness(self):
        self.api.eyes_brightness(level=15)
        self.assertEqual(_last_emitted(self.bus).data["level"], 15)

    def test_reset(self):
        self.api.eyes_reset()
        self.assertEqual(_last_emitted(self.bus).msg_type, "enclosure.eyes.reset")

    def test_spin(self):
        self.api.eyes_spin()
        self.assertEqual(_last_emitted(self.bus).msg_type, "enclosure.eyes.spin")

    def test_timed_spin(self):
        self.api.eyes_timed_spin(length=500)
        self.assertEqual(_last_emitted(self.bus).data["length"], 500)

    def test_volume(self):
        self.api.eyes_volume(volume=5)
        self.assertEqual(_last_emitted(self.bus).data["volume"], 5)

    def test_volume_invalid(self):
        with self.assertRaises(ValueError):
            self.api.eyes_volume(20)
        with self.assertRaises(ValueError):
            self.api.eyes_volume(-1)


class TestEnclosureMouth(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.api = EnclosureAPI(bus=self.bus, skill_id="s")

    def test_simple_emitters(self):
        for method, expected in [
            (self.api.mouth_reset, "enclosure.mouth.reset"),
            (self.api.mouth_talk, "enclosure.mouth.talk"),
            (self.api.mouth_think, "enclosure.mouth.think"),
            (self.api.mouth_listen, "enclosure.mouth.listen"),
            (self.api.mouth_smile, "enclosure.mouth.smile"),
            (self.api.activate_mouth_events, "enclosure.mouth.events.activate"),
            (self.api.deactivate_mouth_events, "enclosure.mouth.events.deactivate"),
        ]:
            method()
            self.assertEqual(_last_emitted(self.bus).msg_type, expected)

    def test_text(self):
        self.api.mouth_text("hello")
        emitted = _last_emitted(self.bus)
        self.assertEqual(emitted.msg_type, "enclosure.mouth.text")
        self.assertEqual(emitted.data["text"], "hello")

    def test_viseme(self):
        self.api.mouth_viseme(start=1.0, viseme_pairs=[(0, 0.1), (1, 0.2)])
        emitted = _last_emitted(self.bus)
        self.assertEqual(emitted.msg_type, "enclosure.mouth.viseme_list")
        self.assertEqual(emitted.data["start"], 1.0)
        self.assertEqual(emitted.data["visemes"], [(0, 0.1), (1, 0.2)])

    def test_display(self):
        self.api.mouth_display(img_code="abc", x=1, y=2, refresh=False)
        emitted = _last_emitted(self.bus)
        self.assertEqual(emitted.msg_type, "enclosure.mouth.display")
        self.assertEqual(emitted.data["img_code"], "abc")
        self.assertEqual(emitted.data["xOffset"], 1)
        self.assertEqual(emitted.data["clearPrev"], False)

    def test_display_png(self):
        self.api.mouth_display_png("/path/img.png", invert=True, x=3, y=4)
        emitted = _last_emitted(self.bus)
        self.assertEqual(emitted.msg_type, "enclosure.mouth.display_image")
        self.assertEqual(emitted.data["img_path"], "/path/img.png")
        self.assertTrue(emitted.data["invert"])

    def test_weather_display(self):
        self.api.weather_display(img_code=2, temp=15)
        emitted = _last_emitted(self.bus)
        self.assertEqual(emitted.msg_type, "enclosure.weather.display")
        self.assertEqual(emitted.data, {"img_code": 2, "temp": 15})


class TestEnclosureGetters(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.api = EnclosureAPI(bus=self.bus, skill_id="s")

    def test_get_eyes_color_returns_pixels(self):
        self.bus.wait_for_response.return_value = Message(
            "enclosure.eyes.rgb", {"pixels": [(1, 2, 3)] * 24}
        )
        pixels = self.api.get_eyes_color()
        self.assertEqual(len(pixels), 24)

    def test_get_eyes_color_timeout(self):
        self.bus.wait_for_response.return_value = None
        with self.assertRaises(TimeoutError):
            self.api.get_eyes_color()

    def test_get_eyes_pixel_color(self):
        self.bus.wait_for_response.return_value = Message(
            "enclosure.eyes.rgb", {"pixels": [(i, i, i) for i in range(24)]}
        )
        self.assertEqual(self.api.get_eyes_pixel_color(5), (5, 5, 5))

    def test_get_eyes_pixel_color_invalid_idx(self):
        with self.assertRaises(ValueError):
            self.api.get_eyes_pixel_color(99)


if __name__ == "__main__":
    unittest.main()
