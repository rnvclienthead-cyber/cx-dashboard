import unittest

from backend.rating_digest import (
    CRITICAL_BELOW,
    format_critical,
    has_critical,
    is_critical,
    pick_critical,
)


class RatingDigestTests(unittest.TestCase):
    def test_threshold_skips_flagship_449(self):
        self.assertFalse(is_critical(4.49, 2000))
        self.assertFalse(is_critical(4.40, 100))
        self.assertTrue(is_critical(4.39, 100))
        self.assertTrue(is_critical(4.20, 6))

    def test_low_rating_shows_with_few_reviews(self):
        self.assertFalse(is_critical(0, 100))
        self.assertTrue(is_critical(1.75, 4))
        self.assertTrue(is_critical(1.8, 1))
        self.assertFalse(is_critical(4.20, 4))
        self.assertTrue(is_critical(4.20, 5))

    def test_pick_sorts_and_caps(self):
        rows = [
            {"sku": "B", "rating": 4.10, "reviews": 20},
            {"sku": "A", "rating": 3.90, "reviews": 50},
            {"sku": "skip", "rating": 4.50, "reviews": 9},
            {"sku": "C", "rating": 4.10, "reviews": 80},
        ]
        picked = pick_critical(rows, limit=2)
        self.assertEqual([p["sku"] for p in picked], ["A", "C"])

    def test_format_empty(self):
        text = format_critical({"WB": [], "ЯМ": [], "Ozon": []})
        self.assertIn("Критические товары по рейтингу", text)
        self.assertIn("Нет.", text)
        self.assertFalse(has_critical({"WB": [], "ЯМ": []}))

    def test_format_lists_marketplaces(self):
        text = format_critical({
            "WB": [{"sku": "Dryer-X-White", "rating": 4.36, "reviews": 107}],
            "ЯМ": [],
            "Ozon": [{"sku": "BS-White-1", "rating": 4.21, "reviews": 12}],
        })
        self.assertIn("WB:", text)
        self.assertIn("Dryer-X-White — 4.36 (107 оценок)", text)
        self.assertIn("ЯМ: нет", text)
        self.assertIn("Ozon:", text)
        self.assertIn(f"ниже {CRITICAL_BELOW:g}", text)
        self.assertTrue(has_critical({
            "WB": [{"sku": "X", "rating": 4.0, "reviews": 10}],
        }))


if __name__ == "__main__":
    unittest.main()
