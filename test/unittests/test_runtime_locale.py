"""``setup_locale()`` has to be visible to lang resolution.

``setup_locale()`` / ``set_default_lang()`` do not write to the config: they
set a module global in ``ovos_config.locale``. ``Configuration()`` never sees
it, so resolving the default lang from the config alone makes a runtime locale
switch invisible -- a message with no lang resolved ``en-US`` after
``setup_locale("it-it")``.

The two constraints are asserted together on purpose. #293 removed the
deprecated call for a real reason (a DeprecationWarning on every import) and
broke this one doing it; #282's note had warned that the config and the active
lang are not the same thing. A fix that satisfies either alone is not a fix.
"""

import subprocess
import sys
import unittest

from ovos_config.config import Configuration
from ovos_config.locale import setup_locale
import ovos_config.locale as locale_module

from ovos_bus_client.message import Message
from ovos_bus_client.util import get_message_lang


#: Module globals ``setup_locale()`` mutates. Named rather than discovered so a
#: new one is a visible test failure instead of silent leakage. Not every
#: supported ovos-config carries both: 2.3.8a2 and 3.0.0a1 drop ``_lang`` and
#: have ``setup_locale()`` write the configuration instead, so each is saved
#: only if it is there.
_LOCALE_GLOBALS = ("_lang", "_default_tz")


class TestRuntimeLocaleIsHonoured(unittest.TestCase):
    """Each test restores every piece of locale state ``setup_locale()`` moves.

    ``setup_locale()`` sets the language, the default timezone and loads
    lingua-franca resources. Restoring only the language left a timezone
    established by another test replaced with the configured one, so the order
    tests ran in could change their outcome.
    """

    def setUp(self):
        self._saved = {name: getattr(locale_module, name)
                       for name in _LOCALE_GLOBALS
                       if hasattr(locale_module, name)}
        # On the versions that have no ``_lang``, ``setup_locale()`` writes the
        # configuration, so that is the value to put back.
        self._saved_config_lang = Configuration().get("lang")

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(locale_module, name, value)
        if Configuration().get("lang") != self._saved_config_lang:
            Configuration()["lang"] = self._saved_config_lang

    def test_a_runtime_locale_switch_reaches_lang_resolution(self):
        setup_locale("it-it")
        self.assertEqual(get_message_lang(Message("t", data={})), "it-IT")

        setup_locale("pt-pt")
        self.assertEqual(
            get_message_lang(Message("t", data={})), "pt-PT",
            "a second switch was not picked up either")

    def test_the_config_is_the_fallback_when_no_locale_was_set_up(self):
        """With no active lang, the configured value is what should surface."""
        from ovos_bus_client.util import standardize_lang

        if hasattr(locale_module, "_lang"):
            locale_module._lang = None
        expected = standardize_lang(Configuration().get("lang", "en-us"))
        self.assertEqual(get_message_lang(Message("t", data={})), expected)

    def test_an_explicit_lang_on_the_message_still_wins(self):
        setup_locale("it-it")
        self.assertEqual(
            get_message_lang(Message("t", data={"lang": "de-de"})), "de-DE")

    def test_resolution_still_emits_no_deprecation_notice_on_import(self):
        """The constraint #293 added; it must survive this fix."""
        proc = subprocess.run(
            [sys.executable, "-c", "import ovos_bus_client.session"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"import failed:\n{proc.stderr}")
        self.assertNotIn("Deprecation version=", proc.stdout)
        self.assertNotIn("Deprecation version=", proc.stderr)


if __name__ == "__main__":
    unittest.main()
