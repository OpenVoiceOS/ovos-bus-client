"""Coverage tests for ovos_bus_client.apis.gui — GUIInterface, GUIWidgets."""
import unittest
from unittest import TestCase
from unittest.mock import MagicMock, patch

from ovos_bus_client.apis.gui import GUIInterface, GUIWidgets, _GUIDict
from ovos_bus_client.message import Message


def _last(bus) -> Message:
    return bus.emit.call_args[0][0]


def _emitted_types(bus):
    return [c.args[0].msg_type for c in bus.emit.call_args_list]


class TestGUIWidgets(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.widgets = GUIWidgets(bus=self.bus)

    def test_show_widget(self):
        self.widgets.show_widget("clock", {"face": "round"})
        emitted = _last(self.bus)
        self.assertEqual(emitted.msg_type, "ovos.widgets.display")
        self.assertEqual(emitted.data["type"], "clock")

    def test_remove_widget(self):
        self.widgets.remove_widget("clock", {})
        self.assertEqual(_last(self.bus).msg_type, "ovos.widgets.remove")

    def test_update_widget(self):
        self.widgets.update_widget("clock", {"hand": "minute"})
        self.assertEqual(_last(self.bus).msg_type, "ovos.widgets.update")


class TestGUIInterfaceConstructionAndProps(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.gui = GUIInterface(skill_id="t.skill", bus=self.bus)

    def test_skill_id_property_and_setter(self):
        self.assertEqual(self.gui.skill_id, "t.skill")
        self.gui.skill_id = "new.skill"
        self.assertEqual(self.gui.skill_id, "new.skill")

    def test_bus_property_and_setter(self):
        new_bus = MagicMock()
        self.gui.bus = new_bus
        self.assertIs(self.gui.bus, new_bus)

    def test_build_message_type_prepends_skill_id(self):
        self.assertEqual(self.gui.build_message_type("clicked"), "t.skill.clicked")

    def test_build_message_type_already_prefixed(self):
        self.assertEqual(
            self.gui.build_message_type("t.skill.clicked"),
            "t.skill.clicked",
        )

    def test_setup_default_handlers_registers_set(self):
        self.bus.reset_mock()
        self.gui.setup_default_handlers()
        registered = {c.args[0] for c in self.bus.on.call_args_list}
        self.assertIn("t.skill.set", registered)

    def test_page_property_empty(self):
        self.assertIsNone(self.gui.page)

    def test_pages_property_initial_empty(self):
        self.assertEqual(self.gui.pages, [])

    def test_connected_false_without_bus(self):
        gui = GUIInterface(skill_id="t.skill")
        self.assertFalse(gui.connected)

    def test_gui_disabled_default(self):
        # default config should not disable
        self.assertFalse(self.gui.gui_disabled)


class TestGUIRegisterHandler(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.gui = GUIInterface(skill_id="t.skill", bus=self.bus)

    def test_register_handler_registers_with_prefix(self):
        cb = MagicMock()
        self.gui.register_handler("clicked", cb)
        registered = {c.args[0] for c in self.bus.on.call_args_list}
        self.assertIn("t.skill.clicked", registered)

    def test_register_handler_without_bus_raises(self):
        gui = GUIInterface(skill_id="t.skill")
        with self.assertRaises(RuntimeError):
            gui.register_handler("clicked", lambda m: None)

    def test_set_on_gui_changed(self):
        cb = lambda: None
        self.gui.set_on_gui_changed(cb)
        self.assertIs(self.gui.on_gui_changed_callback, cb)


class TestGUIDictAccess(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.gui = GUIInterface(skill_id="t.skill", bus=self.bus)

    def test_setitem_stores_value(self):
        self.gui["temperature"] = 22
        self.assertEqual(self.gui["temperature"], 22)

    def test_setitem_same_value_skips_sync(self):
        self.gui._pages = ["page"]  # so page property is non-None and sync happens
        self.gui.current_page_idx = 0
        self.gui["k"] = "v"
        self.bus.reset_mock()
        self.gui["k"] = "v"  # same → no sync
        self.assertFalse(self.bus.emit.called)

    def test_setitem_dict_gets_wrapped(self):
        self.gui["meta"] = {"a": 1}
        self.assertIsInstance(self.gui["meta"], _GUIDict)

    def test_get_method(self):
        self.gui["k"] = "v"
        self.assertEqual(self.gui.get("k"), "v")
        self.assertIsNone(self.gui.get("missing"))
        self.assertEqual(self.gui.get("missing", "default"), "default")

    def test_contains_operator(self):
        self.gui["k"] = "v"
        self.assertIn("k", self.gui)
        self.assertNotIn("missing", self.gui)

    def test_guidict_sync_on_change(self):
        d = _GUIDict(gui=self.gui, x=1)
        d["x"] = 99
        # _sync_data raises if there's no bus; we have one, so it just emits
        # whether emit fired depends on gui_disabled, but no exception is fine
        d["x"] = 99   # same value, no sync needed


class TestGUIClear(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.gui = GUIInterface(skill_id="t.skill", bus=self.bus)

    def test_clear_resets_state_and_emits(self):
        self.gui["a"] = 1
        self.gui._pages = ["p1", "p2"]
        self.gui.clear()
        self.assertEqual(self.gui.pages, [])
        self.assertEqual(self.gui.current_page_idx, -1)
        self.assertIn("gui.clear.namespace", _emitted_types(self.bus))


class TestGUISendEvent(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.gui = GUIInterface(skill_id="t.skill", bus=self.bus)

    def test_send_event_payload(self):
        self.gui.send_event("clicked", {"target": "btn"})
        emitted = _last(self.bus)
        self.assertEqual(emitted.msg_type, "gui.event.send")
        self.assertEqual(emitted.data["event_name"], "clicked")
        self.assertEqual(emitted.data["params"], {"target": "btn"})

    def test_send_event_default_params(self):
        self.gui.send_event("idle")
        self.assertEqual(_last(self.bus).data["params"], {})


class TestNormalizePageName(TestCase):
    def test_strips_qml_extension(self):
        self.assertEqual(GUIInterface._normalize_page_name("Foo.qml"), "Foo")

    def test_passes_through_non_qml(self):
        self.assertEqual(GUIInterface._normalize_page_name("Foo"), "Foo")

    def test_raises_on_existing_filepath(self):
        with patch("ovos_bus_client.apis.gui.isfile", return_value=True):
            with self.assertRaises(ValueError):
                GUIInterface._normalize_page_name("/tmp/foo.qml")


class TestShowPages(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.gui = GUIInterface(skill_id="t.skill", bus=self.bus)

    def test_show_page_basic(self):
        self.gui.show_page("Weather")
        types = _emitted_types(self.bus)
        self.assertIn("gui.value.set", types)
        self.assertIn("gui.page.show", types)
        self.assertEqual(self.gui._pages, ["Weather"])

    def test_show_page_with_overrides(self):
        self.gui.show_page("Weather", override_idle=30, override_animations=True)
        emitted = [c.args[0] for c in self.bus.emit.call_args_list
                   if c.args[0].msg_type == "gui.page.show"][0]
        self.assertEqual(emitted.data["__idle"], 30)
        self.assertTrue(emitted.data["__animations"])

    def test_show_pages_list(self):
        self.gui.show_pages(["A", "B"])
        self.assertEqual(self.gui._pages, ["A", "B"])

    def test_show_pages_string_treated_as_list(self):
        self.gui.show_pages("OnlyOne")
        self.assertEqual(self.gui._pages, ["OnlyOne"])

    def test_show_pages_invalid_type_raises(self):
        with self.assertRaises(ValueError):
            self.gui.show_pages(42)

    def test_show_pages_strips_qml(self):
        self.gui.show_pages(["Foo.qml", "Bar.qml"])
        self.assertEqual(self.gui._pages, ["Foo", "Bar"])

    def test_show_pages_index_clamped(self):
        self.gui.show_pages(["A"], index=10)
        # current_page_idx clamped to len-1
        self.assertEqual(self.gui.current_page_idx, 0)

    def test_show_pages_without_bus_raises(self):
        gui = GUIInterface(skill_id="t.skill")
        with self.assertRaises(RuntimeError):
            gui.show_pages(["X"])


class TestRemovePages(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.gui = GUIInterface(skill_id="t.skill", bus=self.bus)

    def test_remove_page(self):
        self.gui.remove_page("Weather")
        emitted = _last(self.bus)
        self.assertEqual(emitted.msg_type, "gui.page.delete")
        self.assertEqual(emitted.data["page_names"], ["Weather"])

    def test_remove_pages_list(self):
        self.gui.remove_pages(["A", "B"])
        self.assertEqual(_last(self.bus).data["page_names"], ["A", "B"])

    def test_remove_pages_invalid_raises(self):
        with self.assertRaises(ValueError):
            self.gui.remove_pages(42)

    def test_remove_pages_strips_qml(self):
        self.gui.remove_pages(["Foo.qml"])
        self.assertEqual(_last(self.bus).data["page_names"], ["Foo"])

    def test_remove_all_pages(self):
        self.gui.remove_all_pages()
        emitted = _last(self.bus)
        self.assertEqual(emitted.msg_type, "gui.page.delete.all")

    def test_remove_all_pages_with_keep_list(self):
        self.gui.remove_all_pages(except_pages=["A"])
        self.assertEqual(_last(self.bus).data["except"], ["A"])


class TestShowNotificationVariants(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.gui = GUIInterface(skill_id="t.skill", bus=self.bus)

    def test_show_notification(self):
        self.gui.show_notification("hello")
        emitted = _last(self.bus)
        self.assertEqual(emitted.msg_type, "ovos.notification.api.set")
        self.assertEqual(emitted.data["text"], "hello")
        self.assertEqual(emitted.data["sender"], "t.skill")
        self.assertEqual(emitted.data["duration"], 10)

    def test_show_notification_with_action(self):
        self.gui.show_notification("hi", action="my.event",
                                   callback_data={"x": 1})
        emitted = _last(self.bus)
        self.assertEqual(emitted.data["action"], "my.event")
        self.assertEqual(emitted.data["callback_data"], {"x": 1})

    def test_show_controlled_notification(self):
        self.gui.show_controlled_notification("hi", style="warning")
        emitted = _last(self.bus)
        self.assertEqual(emitted.msg_type, "ovos.notification.api.set.controlled")
        self.assertEqual(emitted.data["style"], "warning")

    def test_remove_controlled_notification(self):
        self.gui.remove_controlled_notification()
        self.assertEqual(_last(self.bus).msg_type, "ovos.notification.api.remove.controlled")


class TestShowTemplates(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.gui = GUIInterface(skill_id="t.skill", bus=self.bus)

    def test_show_text(self):
        self.gui.show_text("hello", title="greeting")
        self.assertEqual(self.gui["text"], "hello")
        self.assertEqual(self.gui["title"], "greeting")
        self.assertEqual(self.gui._pages, ["SYSTEM_TextFrame"])

    def test_show_face_awake(self):
        self.gui.show_face(awake=True)
        self.assertFalse(self.gui["sleeping"])
        self.assertEqual(self.gui._pages, ["SYSTEM_Face"])

    def test_show_face_asleep(self):
        self.gui.show_face(awake=False)
        self.assertTrue(self.gui["sleeping"])

    def test_show_loading_animation(self):
        self.gui.show_loading_animation("loading...")
        self.assertEqual(self.gui["label"], "loading...")
        self.assertEqual(self.gui._pages, ["SYSTEM_Loading"])

    def test_show_status_animation_success(self):
        self.gui.show_status_animation("done", success=True)
        self.assertEqual(self.gui["status"], "Enabled")
        self.assertEqual(self.gui._pages, ["SYSTEM_Status"])

    def test_show_status_animation_failure(self):
        self.gui.show_status_animation("failed", success=False)
        self.assertEqual(self.gui["status"], "Disabled")

    def test_show_html(self):
        self.gui.show_html("<h1>hi</h1>")
        self.assertEqual(self.gui["html"], "<h1>hi</h1>")
        self.assertEqual(self.gui._pages, ["SYSTEM_HtmlFrame"])

    def test_show_url(self):
        self.gui.show_url("https://example.com")
        self.assertEqual(self.gui["url"], "https://example.com")
        self.assertEqual(self.gui._pages, ["SYSTEM_UrlFrame"])

    def test_show_input_box_defaults(self):
        self.gui.show_input_box()
        self.assertEqual(self.gui["confirm_text"], "Confirm")
        self.assertEqual(self.gui["exit_text"], "Exit")

    def test_show_input_box_custom(self):
        self.gui.show_input_box(title="Q", placeholder="...",
                                confirm_text="OK", exit_text="Cancel")
        self.assertEqual(self.gui["confirm_text"], "OK")
        self.assertEqual(self.gui["exit_text"], "Cancel")

    def test_remove_input_box_single_page_releases(self):
        # one page → release path
        self.gui._pages = ["SYSTEM_InputBox"]
        self.gui.remove_input_box()
        # release emits gui.clear.namespace and mycroft.gui.screen.close
        types = _emitted_types(self.bus)
        self.assertIn("mycroft.gui.screen.close", types)

    def test_remove_input_box_multi_page_removes_only_input(self):
        self.gui._pages = ["A", "SYSTEM_InputBox"]
        self.gui.remove_input_box()
        types = _emitted_types(self.bus)
        self.assertIn("gui.page.delete", types)


class TestShowImage(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.gui = GUIInterface(skill_id="t.skill", bus=self.bus)

    def test_show_image_http(self):
        self.gui.show_image("https://x/y.png", caption="cap", title="ttl")
        self.assertEqual(self.gui["image"], "https://x/y.png")
        self.assertEqual(self.gui["caption"], "cap")
        self.assertEqual(self.gui._pages, ["SYSTEM_ImageFrame"])

    def test_show_image_missing_file_logs_and_returns(self):
        # non-http, non-existent file → logs error and returns early
        self.gui.show_image("/no/such/file.png")
        self.assertNotEqual(self.gui._pages, ["SYSTEM_ImageFrame"])

    def test_show_animated_image_http(self):
        self.gui.show_animated_image("https://x/y.gif")
        self.assertEqual(self.gui["image"], "https://x/y.gif")
        self.assertEqual(self.gui._pages, ["SYSTEM_AnimatedImageFrame"])


class TestReleaseAndShutdown(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.gui = GUIInterface(skill_id="t.skill", bus=self.bus)

    def test_release_clears_and_emits_close(self):
        self.gui["k"] = "v"
        self.gui.release()
        types = _emitted_types(self.bus)
        self.assertIn("gui.clear.namespace", types)
        self.assertIn("mycroft.gui.screen.close", types)

    def test_shutdown_removes_handlers(self):
        cb = MagicMock()
        self.gui.register_handler("clicked", cb)
        self.gui.shutdown()
        # bus.remove should have been called at least for the registered handler
        self.assertTrue(self.bus.remove.called)


class TestGUISet(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.gui = GUIInterface(skill_id="t.skill", bus=self.bus)

    def test_gui_set_writes_each_key_into_session_data(self):
        msg = Message("t.skill.set", {"temp": 22, "city": "Lisbon"})
        self.gui.gui_set(msg)
        self.assertEqual(self.gui["temp"], 22)
        self.assertEqual(self.gui["city"], "Lisbon")

    def test_gui_set_invokes_callback(self):
        called = []
        self.gui.set_on_gui_changed(lambda: called.append(True))
        self.gui.gui_set(Message("set", {"k": 1}))
        self.assertEqual(called, [True])


if __name__ == "__main__":
    unittest.main()
