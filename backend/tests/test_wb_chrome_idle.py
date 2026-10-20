import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class WbChromeIdleTests(unittest.TestCase):
    def test_start_script_parks_on_blank(self):
        src = (ROOT / "scripts/wb_chrome_start.sh").read_text()
        self.assertIn("about:blank", src)
        self.assertNotIn("suppliers-returns", src)
        self.assertNotIn("under-consideration", src)
        self.assertIn("hide-crash-restore-bubble", src)
        self.assertIn("/tmp/.X11-unix/X", src)

    def test_bot_parks_after_run(self):
        src = (ROOT / "scripts/wb_bot_open_chat.py").read_text()
        self.assertIn("def _park", src)
        self.assertIn("_park(ctx)", src)
        self.assertIn("finally:", src)
