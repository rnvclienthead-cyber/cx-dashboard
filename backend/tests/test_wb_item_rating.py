import unittest
from datetime import date

from backend.wb_item_rating import (
    extract_items,
    keep_raw_columns,
    request_body,
    wb_ratings_date_is_stale,
)


class WbItemRatingTests(unittest.TestCase):
    def test_extracts_v2_items(self):
        payload = {"data": {"items": [{"nmId": 1, "vendorCode": "A"}]}}
        self.assertEqual(extract_items(payload)[0]["nmId"], 1)

    def test_extracts_legacy_v1_cards(self):
        payload = {"data": {"cards": [{"nmId": 2}]}}
        self.assertEqual(extract_items(payload)[0]["nmId"], 2)

    def test_empty_payload(self):
        self.assertEqual(extract_items({}), [])
        self.assertEqual(extract_items(None), [])

    def test_request_uses_v2_flag(self):
        body = request_body("2026-08-10", "2026-08-10", [169887124])
        self.assertIn("isNotIncludeNmsWithoutSales", body)
        self.assertNotIn("isNotIncludeNMsWithoutSales", body)

    def test_drops_unknown_columns(self):
        self.assertEqual(keep_raw_columns(["nmId", "isShadowed", "sys_date"]), ["nmId", "sys_date"])

    def test_stale_after_two_days(self):
        today = date(2026, 8, 20)
        self.assertTrue(wb_ratings_date_is_stale("2026-08-09", today=today))
        self.assertFalse(wb_ratings_date_is_stale("2026-08-19", today=today))
        self.assertTrue(wb_ratings_date_is_stale(None, today=today))


if __name__ == "__main__":
    unittest.main()
