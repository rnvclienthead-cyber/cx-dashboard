import unittest
from unittest.mock import MagicMock, patch

from backend import ozon_posting as posting


class SummarizeTests(unittest.TestCase):
    def test_api_card_has_sum_and_status(self):
        card = posting.summarize_api({
            "posting_number": "0173340967-0001-1",
            "order_number": "0173340967-0001",
            "status": "delivered",
            "products": [{
                "name": "Вешалка",
                "offer_id": "W-100",
                "sku": 1,
                "quantity": 2,
                "price": "1500",
            }],
            "analytics_data": {"city": "Казань", "warehouse": "Казань_РФЦ"},
        }, "FBO")
        self.assertTrue(card["found"])
        self.assertEqual(card["schema"], "FBO")
        self.assertEqual(card["status_label"], "Доставлен")
        self.assertEqual(card["total"], 3000.0)
        self.assertEqual(card["products"][0]["article"], "W-100")
        self.assertEqual(card["city"], "Казань")

    def test_cache_rows(self):
        card = posting.summarize_rows([{
            "posting_number": "1-0001-1",
            "status": "delivering",
            "sku_name": "Стол",
            "supplier_article": "T-1",
            "price": 990,
            "quantity": 1,
        }])
        self.assertEqual(card["total"], 990)
        self.assertEqual(card["status_label"], "В пути")

    def test_candidates(self):
        self.assertEqual(posting.candidates("0173340967-0001-1"), ["0173340967-0001-1"])
        self.assertEqual(posting.candidates("0173340967-0001")[-1], "0173340967-0001-1")


class LookupTests(unittest.TestCase):
    def test_live_prefers_api(self):
        db = MagicMock()
        api = {"found": True, "posting_number": "1-0001-1", "total": 10, "products": []}
        with patch.object(posting, "from_db", return_value={"found": True, "from_cache": True}), \
             patch.object(posting, "fetch_api", return_value=api):
            got = posting.lookup(db, "1-0001-1", live=True)
        self.assertEqual(got["total"], 10)

    def test_cache_when_api_empty(self):
        db = MagicMock()
        cached = {"found": True, "from_cache": True, "total": 5}
        with patch.object(posting, "from_db", return_value=cached), \
             patch.object(posting, "fetch_api", return_value=None):
            got = posting.lookup(db, "1-0001-1", live=True)
        self.assertEqual(got["total"], 5)


if __name__ == "__main__":
    unittest.main()
