from pathlib import Path
import unittest


class OzonBotTickSourceTests(unittest.TestCase):
    def test_does_not_mark_reviews_viewed_in_cabinet(self):
        src = Path(__file__).resolve().parents[2] / "ozon_bot_tick.py"
        text = src.read_text(encoding="utf-8")
        self.assertNotIn("/api/v2/review/detail", text)
        self.assertIn("review_info", text)
        self.assertNotIn("already processed", text)
        self.assertIn("не через /review/detail", text)
        self.assertNotIn("collect_and_upload_ratings", text)


if __name__ == "__main__":
    unittest.main()
