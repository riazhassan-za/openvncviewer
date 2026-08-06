"""Guards the packaging entry point.

PyInstaller runs its entry script as top-level ``__main__`` with no package
context. Pointing it straight at ``openvncviewer/__main__.py`` therefore builds
an executable that dies instantly on ``from . import __version__`` — and only a
full build would reveal it. Running the entry script as a subprocess reproduces
that context cheaply.
"""

import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENTRY = ROOT / "packaging" / "entry.py"


class PackagingEntryPointTest(unittest.TestCase):
    def test_entry_script_runs_without_a_package_context(self):
        self.assertTrue(ENTRY.is_file(), f"missing entry script: {ENTRY}")
        # --version exits inside argparse, before any GUI is created.
        result = subprocess.run([sys.executable, str(ENTRY), "--version"],
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0,
                         f"entry script failed:\n{result.stderr}")
        self.assertIn("openvncviewer", result.stdout.lower())

    def test_spec_uses_the_entry_script(self):
        spec = (ROOT / "packaging" / "openvncviewer.spec").read_text()
        self.assertIn('"entry.py"', spec,
                      "the spec must build from packaging/entry.py")


if __name__ == "__main__":
    unittest.main()
