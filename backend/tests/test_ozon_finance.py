import unittest
from datetime import date
from unittest.mock import Mock, patch

from backend import ozon_finance


class AccrualParseTests(unittest.TestCase):
    def test_sums_container_fees_by_unit(self):
        totals = ozon_finance.accrual_unit_totals([
            {
                "unit_number": "EP692Y01E234-P88Y",
                "accrued_category": "CONTAINER_FEES",
                "total_amount": {"amount": "-10", "currency": "RUB"},
            },
            {
                "unit_number": "ep692y01e234-p88y",
                "accrued_category": "CONTAINER_FEES",
                "total_amount": {"amount": "-71.17", "currency": "RUB"},
            },
            {
                "unit_number": "84187030-0121",
                "accrued_category": "ITEM",
                "total_amount": {"amount": "-27.05", "currency": "RUB"},
            },
        ])
        self.assertEqual(totals["EP692Y01E234-P88Y"], 81.17)
        self.assertEqual(totals["84187030-0121"], 27.05)

    def test_skips_empty_unit(self):
        self.assertEqual(ozon_finance.accrual_unit_totals([{"total_amount": {"amount": "-10"}}]), {})


class ApplyTotalsTests(unittest.TestCase):
    def test_writes_posting_match_and_skips_same_amount(self):
        db = Mock()
        db.execute.return_value.mappings.return_value.all.return_value = [
            {
                "id": 680,
                "ozon_posting_number": "EP692Y01E234-P88Y",
                "ozon_order_number": "YB-1",
                "ozon_cost_actual": None,
            },
            {
                "id": 681,
                "ozon_posting_number": "OTHER",
                "ozon_order_number": "",
                "ozon_cost_actual": 10.0,
            },
        ]
        n = ozon_finance.apply_unit_totals(db, {"EP692Y01E234-P88Y": 81.17})
        self.assertEqual(n, 1)
        db.commit.assert_called_once()

    def test_skips_insurance_only_amount(self):
        db = Mock()
        db.execute.return_value.mappings.return_value.all.return_value = [
            {
                "id": 744,
                "ozon_posting_number": "428618279954-6E0E",
                "ozon_order_number": "",
                "ozon_cost_actual": None,
            },
        ]
        n = ozon_finance.apply_unit_totals(db, {"428618279954-6E0E": 10.0})
        self.assertEqual(n, 0)
        db.commit.assert_not_called()

    def test_matches_order_number_if_posting_missing_in_totals(self):
        db = Mock()
        db.execute.return_value.mappings.return_value.all.return_value = [
            {
                "id": 1,
                "ozon_posting_number": "",
                "ozon_order_number": "YB-39211Y972EY2-511Y",
                "ozon_cost_actual": None,
            },
        ]
        n = ozon_finance.apply_unit_totals(db, {"YB-39211Y972EY2-511Y": 40.0})
        self.assertEqual(n, 1)


class SyncWiringTests(unittest.TestCase):
    def test_skips_without_keys(self):
        with patch.object(ozon_finance.ozon_client, "client_id", return_value=""), \
             patch.object(ozon_finance.ozon_client, "api_key", return_value=""):
            stats = ozon_finance.sync_reshipment_ozon_costs(Mock())
        self.assertEqual(stats["updated"], 0)
        self.assertEqual(stats["reason"], "no_key")

    def test_updater_calls_finance_sync(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "main.py").read_text()
        self.assertIn("sync_reshipment_ozon_costs", src)

    def test_applies_on_fresh_session_after_fetch(self):
        db = Mock()
        db.execute.return_value.scalar.return_value = date(2026, 9, 1)
        fresh = Mock()
        with patch.object(ozon_finance.ozon_client, "client_id", return_value="1"), \
             patch.object(ozon_finance.ozon_client, "api_key", return_value="k"), \
             patch.object(ozon_finance, "fetch_unit_totals", return_value={"A": 10.0}) as fetch, \
             patch.object(ozon_finance, "apply_unit_totals", return_value=2) as apply, \
             patch.object(ozon_finance, "Session", return_value=fresh) as sess:
            stats = ozon_finance.sync_reshipment_ozon_costs(db)
        fetch.assert_called_once()
        apply.assert_called_once_with(fresh, {"A": 10.0})
        sess.assert_called_once()
        fresh.close.assert_called_once()
        self.assertEqual(stats["updated"], 2)
        db.rollback.assert_called()


if __name__ == "__main__":
    unittest.main()
