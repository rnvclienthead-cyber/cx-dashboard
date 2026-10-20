from datetime import datetime, timezone, timedelta
import unittest

from backend.routers.cx_control import classify_activity


class ClassifyActivityTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)

    def test_none_is_offline(self):
        status, sec = classify_activity(None, self.now)
        self.assertEqual(status, "offline")
        self.assertIsNone(sec)

    def test_fresh_click_is_online(self):
        seen = self.now - timedelta(seconds=3)
        status, sec = classify_activity(seen, self.now)
        self.assertEqual(status, "online")
        self.assertEqual(sec, 3)

    def test_idle_tab_after_half_minute_is_recent_not_online(self):
        seen = self.now - timedelta(seconds=40)
        status, sec = classify_activity(seen, self.now)
        self.assertEqual(status, "recent")
        self.assertEqual(sec, 40)

    def test_old_presence_is_offline(self):
        seen = self.now - timedelta(minutes=10)
        status, _sec = classify_activity(seen, self.now)
        self.assertEqual(status, "offline")


if __name__ == "__main__":
    unittest.main()
