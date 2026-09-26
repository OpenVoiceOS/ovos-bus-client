"""Catch-all coverage tests — light tests for paths still uncovered."""
import os
import shutil
import tempfile
import unittest
from datetime import timedelta
from unittest import TestCase
from unittest.mock import MagicMock, patch

import pytest

from ovos_bus_client.apis.enclosure import EnclosureAPI
from ovos_bus_client.apis.events import EventSchedulerInterface
from ovos_bus_client.apis.gui import GUIInterface
from ovos_bus_client.message import Message


@pytest.mark.filterwarnings("ignore:EnclosureAPI moved to ovos-gui-api-client:DeprecationWarning")
class TestEnclosureExtra(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.api = EnclosureAPI(bus=self.bus, skill_id="s")

    def test_get_source_message_falls_back(self):
        msg = self.api._get_source_message()
        self.assertIsInstance(msg, Message)

    def test_mouth_thinking_listening_smile(self):
        # already covered the common ones; just exercising a couple more
        self.api.mouth_listen()
        self.api.mouth_think()
        self.api.mouth_smile()
        types = [c.args[0].msg_type for c in self.bus.emit.call_args_list]
        self.assertIn("enclosure.mouth.listen", types)


class TestEventsInterfaceExtra(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.api = EventSchedulerInterface(bus=self.bus, skill_id="my.skill")

    def test_cancel_nonexistent_event(self):
        # cancel of unknown event is a no-op — handler is not registered
        self.api.cancel_scheduled_event("never.scheduled")
        # no emission expected since events.remove returns False
        # so bus.emit must NOT include the remove message
        types = [c.args[0].msg_type for c in self.bus.emit.call_args_list]
        self.assertNotIn("mycroft.scheduler.remove_event", types)


class TestGUICacheAndUrl(TestCase):
    def setUp(self):
        self.bus = MagicMock()

    def test_gui_with_no_ui_directories(self):
        # exercises _cache_gui_files early-return branch
        gui = GUIInterface(skill_id="t.skill", bus=self.bus,
                           ui_directories=None)
        self.assertEqual(gui.ui_directories, {})

    def test_gui_with_ui_directories(self):
        tmp = tempfile.mkdtemp()
        try:
            qml_dir = os.path.join(tmp, "qt5")
            os.makedirs(qml_dir)
            with open(os.path.join(qml_dir, "Foo.qml"), "w") as f:
                f.write("// Foo")
            gui = GUIInterface(skill_id="cache.test", bus=self.bus,
                               ui_directories={"qt5": qml_dir})
            # cache succeeded; ui_directories preserved
            self.assertIn("qt5", gui.ui_directories)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_resolve_url_http(self):
        gui = GUIInterface(skill_id="t.skill", bus=self.bus)
        self.assertEqual(gui._resolve_url("https://example.com/img.png"),
                         "https://example.com/img.png")

    def test_resolve_url_invalid_raises(self):
        gui = GUIInterface(skill_id="t.skill", bus=self.bus)
        with self.assertRaises(ValueError):
            gui._resolve_url(None)
        with self.assertRaises(ValueError):
            gui._resolve_url("")

    def test_pages_property_with_value(self):
        gui = GUIInterface(skill_id="t.skill", bus=self.bus)
        gui._pages = ["A", "B"]
        gui.current_page_idx = 1
        self.assertEqual(gui.page, "B")
        self.assertEqual(gui.pages, ["A", "B"])

    def test_page_index_overflow_returns_none(self):
        gui = GUIInterface(skill_id="t.skill", bus=self.bus)
        gui._pages = ["A"]
        gui.current_page_idx = 99
        self.assertIsNone(gui.page)


class TestGUINotificationsAndExtras(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.gui = GUIInterface(skill_id="t.skill", bus=self.bus)

    def test_show_notification_no_bus_raises(self):
        # detach bus by recreating
        gui = GUIInterface(skill_id="t.skill")
        with self.assertRaises(RuntimeError):
            gui.show_notification("hi")

    def test_clear_without_bus_raises(self):
        gui = GUIInterface(skill_id="t.skill")
        # avoid the gui_disabled early-return by inspecting current state
        # clear emits a message — without a bus this raises
        with self.assertRaises(RuntimeError):
            gui.clear()


class TestClientMissingBusBranches(TestCase):
    def test_send_event_no_bus_raises(self):
        gui = GUIInterface(skill_id="t.skill")
        with self.assertRaises(RuntimeError):
            gui.send_event("clicked")

    def test_remove_all_pages_no_bus_raises(self):
        gui = GUIInterface(skill_id="t.skill")
        with self.assertRaises(RuntimeError):
            gui.remove_all_pages()

    def test_remove_pages_no_bus_raises(self):
        gui = GUIInterface(skill_id="t.skill")
        with self.assertRaises(RuntimeError):
            gui.remove_pages(["A"])


class TestRemainingOCPMethods(TestCase):
    def test_ocp_audio_service_invalid_play(self):
        from ovos_bus_client.apis.ocp import OCPAudioServiceInterface
        svc = OCPAudioServiceInterface(MagicMock())
        # play with non-list/non-string/non-tuple
        with self.assertRaises(ValueError):
            svc.play(tracks={"not": "valid"})


class TestCollectorAndWaiterExtras(TestCase):
    def test_waiter_filters_by_type(self):
        from ovos_bus_client.client.waiter import MessageWaiter
        from ovos_utils.fakebus import FakeBus
        import threading
        bus = FakeBus()
        waiter = MessageWaiter(bus, "want")
        threading.Timer(0.05, lambda: (
            bus.emit(Message("nope")), bus.emit(Message("want", {"x": 1}))
        )).start()
        out = waiter.wait(timeout=1)
        self.assertEqual(out.msg_type, "want")


if __name__ == "__main__":
    unittest.main()
