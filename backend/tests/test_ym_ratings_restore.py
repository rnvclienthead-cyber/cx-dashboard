import unittest
from datetime import date

from backend.ym_ratings_restore import add_stars, daterange, rating_from_stars


class RestoreYmRatingsTests(unittest.TestCase):
    def test_add_stars(self):
        self.assertEqual(add_stars(10, 2, 1, 0, 0, 3, 1, 0, 0, 1), (13, 3, 1, 0, 1))

    def test_rating(self):
        self.assertEqual(rating_from_stars(10, 0, 0, 0, 0), 5.0)
        self.assertEqual(rating_from_stars(2, 2, 0, 0, 0), 4.5)
        self.assertIsNone(rating_from_stars(0, 0, 0, 0, 0))

    def test_daterange(self):
        days = list(daterange(date(2026, 8, 7), date(2026, 8, 9)))
        self.assertEqual(days, [date(2026, 8, 7), date(2026, 8, 8), date(2026, 8, 9)])


if __name__ == "__main__":
    unittest.main()
