"""Coverage tests for ovos_bus_client.apis.gui — the template-based GUIInterface.

The classic free-form page / widget API was removed (OVOS-GUI-1): applications
now declare *what* to display by naming a SYSTEM_* template (PageTemplates) and
supplying its data; the wire protocol is ``gui.value.set`` + ``gui.page.show``.
These tests assert that observable contract.
"""
from unittest import TestCase
from unittest.mock import MagicMock, patch

from ovos_bus_client.apis.gui import GUIInterface, PageTemplates, _GUIDict


def _emitted(bus):
    return [c.args[0] for c in bus.emit.call_args_list]


def _types(bus):
    return [m.msg_type for m in _emitted(bus)]


def _first(bus, msg_type):
    return next(m for m in _emitted(bus) if m.msg_type == msg_type)


class TestPageTemplates(TestCase):
    def test_values_are_system_prefixed(self):
        for t in PageTemplates:
            self.assertTrue(t.value.startswith("SYSTEM_"), t.value)

    def test_known_templates_present(self):
        names = {t.name for t in PageTemplates}
        for expected in ("TEXT", "IMAGE", "FACE", "LOADING", "STATUS", "HTML", "URL"):
            self.assertIn(expected, names)


class TestConstructionAndProps(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.gui = GUIInterface(skill_id="t.skill", bus=self.bus)

    def test_skill_id_property_and_setter(self):
        self.assertEqual(self.gui.skill_id, "t.skill")
        self.gui.skill_id = "new.skill"
        self.assertEqual(self.gui.skill_id, "new.skill")

    def test_bus_property_and_set_bus(self):
        new_bus = MagicMock()
        self.gui.set_bus(new_bus)
        self.assertIs(self.gui.bus, new_bus)

    def test_build_message_type_prepends_skill_id(self):
        self.assertEqual(self.gui._build_message_type("clicked"), "t.skill.clicked")

    def test_build_message_type_idempotent_when_prefixed(self):
        self.assertEqual(self.gui._build_message_type("t.skill.clicked"),
                         "t.skill.clicked")

    def test_pages_initial_empty(self):
        self.assertEqual(self.gui.pages, [])
        self.assertIsNone(self.gui.page)

    def test_gui_disabled_default_false(self):
        self.assertFalse(self.gui.gui_disabled)

    def test_connected_false_without_bus(self):
        gui = GUIInterface(skill_id="t.skill")
        self.assertFalse(gui.connected)


class TestHandlers(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.gui = GUIInterface(skill_id="t.skill", bus=self.bus)

    def test_register_handler_prefixes_event(self):
        self.gui.register_handler("clicked", lambda m: None)
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


class TestDictAccess(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.gui = GUIInterface(skill_id="t.skill", bus=self.bus)

    def test_setitem_and_getitem(self):
        self.gui["temperature"] = 22
        self.assertEqual(self.gui["temperature"], 22)

    def test_get_with_default(self):
        self.gui["k"] = "v"
        self.assertEqual(self.gui.get("k"), "v")
        self.assertIsNone(self.gui.get("missing"))
        self.assertEqual(self.gui.get("missing", "default"), "default")

    def test_contains(self):
        self.gui["k"] = "v"
        self.assertIn("k", self.gui)
        self.assertNotIn("missing", self.gui)

    def test_dict_value_wrapped(self):
        self.gui["meta"] = {"a": 1}
        self.assertIsInstance(self.gui["meta"], _GUIDict)


class TestClearAndEvents(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.gui = GUIInterface(skill_id="t.skill", bus=self.bus)

    def test_clear_resets_pages_and_emits_namespace(self):
        self.gui.show_text("hi")
        self.gui.clear()
        self.assertEqual(self.gui.pages, [])
        self.assertIn("gui.clear.namespace", _types(self.bus))

    def test_send_event_payload(self):
        self.gui.send_event("clicked", {"target": "btn"})
        msg = _first(self.bus, "gui.event.send")
        self.assertEqual(msg.data["event_name"], "clicked")
        self.assertEqual(msg.data["params"], {"target": "btn"})

    def test_send_event_default_params(self):
        self.gui.send_event("idle")
        self.assertEqual(_first(self.bus, "gui.event.send").data["params"], {})

    def test_release_clears_pages(self):
        self.gui.show_text("hi")
        self.gui.release()
        self.assertEqual(self.gui.pages, [])


class TestShowTemplates(TestCase):
    """Each show_* helper names exactly one SYSTEM_* template and emits the
    gui.value.set + gui.page.show pair (OVOS-GUI-1 §3, §4)."""

    def setUp(self):
        self.bus = MagicMock()
        self.gui = GUIInterface(skill_id="t.skill", bus=self.bus)

    def _assert_template(self, template):
        self.assertEqual(_types(self.bus), ["gui.value.set", "gui.page.show"])
        self.assertEqual(self.gui._pages, [template])
        self.assertEqual(self.gui.page, template)
        show = _first(self.bus, "gui.page.show")
        self.assertEqual(show.data["page_names"], [template])
        self.assertEqual(show.data["__from"], "t.skill")

    def test_show_text(self):
        self.gui.show_text("hello", title="T")
        self._assert_template(PageTemplates.TEXT)
        vs = _first(self.bus, "gui.value.set")
        self.assertEqual(vs.data["text"], "hello")
        self.assertEqual(vs.data["title"], "T")

    def test_show_image(self):
        self.gui.show_image("http://x/i.png", caption="c")
        self._assert_template(PageTemplates.IMAGE)

    def test_show_animated_image(self):
        self.gui.show_animated_image("http://x/a.gif")
        self._assert_template(PageTemplates.ANIMATED_IMAGE)

    def test_show_html(self):
        self.gui.show_html("<p>x</p>")
        self._assert_template(PageTemplates.HTML)

    def test_show_url(self):
        self.gui.show_url("http://x")
        self._assert_template(PageTemplates.URL)

    def test_show_face(self):
        self.gui.show_face()
        self._assert_template(PageTemplates.FACE)

    def test_show_loading_animation(self):
        self.gui.show_loading_animation("loading")
        self._assert_template(PageTemplates.LOADING)

    def test_show_status_animation(self):
        self.gui.show_status_animation("done", success=True)
        self._assert_template(PageTemplates.STATUS)

    def test_overrides_passed_through(self):
        self.gui.show_text("hi", override_idle=30, override_animations=True)
        show = _first(self.bus, "gui.page.show")
        self.assertEqual(show.data["__idle"], 30)
        self.assertTrue(show.data["__animations"])


class TestTemplateData(TestCase):
    """Each show_* helper supplies its template's normative session-data keys
    on gui.value.set (OVOS-GUI-1 §3.3)."""

    def setUp(self):
        self.bus = MagicMock()
        self.gui = GUIInterface(skill_id="t.skill", bus=self.bus)

    def _value_data(self):
        return _first(self.bus, "gui.value.set").data

    def test_image_data(self):
        self.gui.show_image("http://x/i.png", caption="c", title="T")
        d = self._value_data()
        self.assertEqual(d["image"], "http://x/i.png")
        self.assertEqual(d["caption"], "c")
        self.assertEqual(d["title"], "T")

    def test_html_data(self):
        self.gui.show_html("<p>h</p>", resource_url="http://r")
        d = self._value_data()
        self.assertEqual(d["html"], "<p>h</p>")
        self.assertEqual(d["resourceLocation"], "http://r")

    def test_url_data(self):
        self.gui.show_url("http://u")
        self.assertEqual(self._value_data()["url"], "http://u")

    def test_status_data(self):
        self.gui.show_status_animation("s", success=False)
        d = self._value_data()
        self.assertEqual(d["label"], "s")
        self.assertIn("status", d)

    def test_loading_data(self):
        self.gui.show_loading_animation("L")
        self.assertEqual(self._value_data()["label"], "L")


class TestGuiDisabled(TestCase):
    def test_disabled_suppresses_emissions(self):
        with patch("ovos_bus_client.apis.gui.Configuration",
                   return_value={"gui": {"disable_gui": True}}):
            bus = MagicMock()
            gui = GUIInterface(skill_id="t.skill", bus=bus)
            self.assertTrue(gui.gui_disabled)
            gui.show_text("x")
            self.assertEqual(_types(bus), [])


class TestPrivatePageHelpers(TestCase):
    """The private template-page primitives the public show_* methods build on."""

    def setUp(self):
        self.bus = MagicMock()
        self.gui = GUIInterface(skill_id="t.skill", bus=self.bus)

    def test_show_pages_multiple_sets_index(self):
        self.gui._show_pages([PageTemplates.TEXT, PageTemplates.IMAGE], index=1)
        self.assertEqual(self.gui._pages,
                         [PageTemplates.TEXT, PageTemplates.IMAGE])
        self.assertEqual(self.gui.current_page_idx, 1)
        self.assertIn("gui.page.show", _types(self.bus))

    def test_show_page_single(self):
        self.gui._show_page(PageTemplates.STATUS)
        self.assertEqual(self.gui._pages, [PageTemplates.STATUS])

    def test_remove_pages_emits_delete(self):
        self.gui.show_text("x")
        self.bus.reset_mock()
        self.gui._remove_pages([PageTemplates.TEXT])
        self.assertIn("gui.page.delete", _types(self.bus))

    def test_remove_page_emits_delete(self):
        self.gui.show_text("x")
        self.bus.reset_mock()
        self.gui._remove_page(PageTemplates.TEXT)
        self.assertIn("gui.page.delete", _types(self.bus))

    def test_remove_all_pages_emits_delete_all(self):
        self.gui.show_text("x")
        self.bus.reset_mock()
        self.gui._remove_all_pages()
        self.assertIn("gui.page.delete.all", _types(self.bus))

    def test_sync_data_emits_value_set(self):
        self.gui.show_text("x")
        self.bus.reset_mock()
        self.gui._sync_data()
        self.assertIn("gui.value.set", _types(self.bus))

    def test_show_pages_without_bus_raises(self):
        gui = GUIInterface(skill_id="t.skill")
        with self.assertRaises(RuntimeError):
            gui._show_pages([PageTemplates.TEXT])


class TestGuiSetAndDict(TestCase):
    def setUp(self):
        self.bus = MagicMock()
        self.gui = GUIInterface(skill_id="t.skill", bus=self.bus)

    def test_gui_set_stores_data_and_fires_callback(self):
        from ovos_bus_client.message import Message
        fired = []
        self.gui.set_on_gui_changed(lambda: fired.append(True))
        self.gui.gui_set(Message("t.skill.set", {"temp": 22, "unit": "C"}))
        self.assertEqual(self.gui["temp"], 22)
        self.assertEqual(self.gui["unit"], "C")
        self.assertTrue(fired)

    def test_dict_value_is_wrapped(self):
        self.gui["outer"] = {"inner": 1}
        self.assertIsInstance(self.gui["outer"], _GUIDict)

    def test_page_property_reflects_current_index(self):
        self.gui._pages = [PageTemplates.TEXT, PageTemplates.IMAGE]
        self.gui.current_page_idx = 1
        self.assertEqual(self.gui.page, PageTemplates.IMAGE)

    def test_page_property_none_when_index_out_of_range(self):
        self.gui._pages = [PageTemplates.TEXT]
        self.gui.current_page_idx = 9
        self.assertIsNone(self.gui.page)


class TestShutdown(TestCase):
    def test_shutdown_no_error(self):
        bus = MagicMock()
        gui = GUIInterface(skill_id="t.skill", bus=bus)
        gui.show_text("hi")
        gui.shutdown()  # must not raise

    def test_release_emits_and_clears(self):
        bus = MagicMock()
        gui = GUIInterface(skill_id="t.skill", bus=bus)
        gui.show_text("hi")
        gui.release()
        self.assertEqual(gui.pages, [])


if __name__ == "__main__":
    import unittest
    unittest.main()
