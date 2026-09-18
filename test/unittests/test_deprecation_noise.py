"""Importing ``ovos_bus_client.session`` builds a default ``Session`` at
module top-level, which resolves the default lang. That resolution must not
call ovos-config's deprecated ``get_default_lang()`` on ovos-config releases
where it is deprecated, or every process that imports this package emits a
DeprecationWarning it never asked for.

Runs in a fresh interpreter: module top-level code runs once per process, so
counting in-process would hide the very notice under test.
"""

import subprocess
import sys
import unittest


class TestSessionImportDeprecationNoise(unittest.TestCase):
    def test_session_import_emits_no_deprecation_notice(self):
        proc = subprocess.run(
            [sys.executable, "-c", "import ovos_bus_client.session"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"import failed:\n{proc.stderr}")
        self.assertNotIn("Deprecation version=", proc.stdout)
        self.assertNotIn("Deprecation version=", proc.stderr)


if __name__ == "__main__":
    unittest.main()
