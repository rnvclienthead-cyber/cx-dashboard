import unittest
from datetime import date

from backend.marketplace_daily_sheet import (
    aggregate_ozon_accruals,
    aggregate_ozon_realization,
    aggregate_wb_orders,
    explain,
    payout_orders,
    payout_sales,
    profit,
    show_scheme,
    wb_scheme,
    missing_header_tail,
    yellow_ranges,
)


class SheetMathTests(unittest.TestCase):
    def test_order_payout_matches_sample_row(self):
        pay = payout_orders(21862, -7798, 0, 0, -55)
        self.assertEqual(pay, 14009)
        self.assertEqual(profit(pay, -6826), 7183)

    def test_sales_payout_matches_sample_row(self):
        pay = payout_sales(203250, -98855, 3328, -9032, 0, -2401, 0, -2098, 0, 0)
        self.assertEqual(pay, 94192)
        self.assertEqual(profit(pay, -35460), 58732)

    def test_wb_scheme(self):
        self.assertEqual(wb_scheme("Склад продавца"), "FBS")
        self.assertEqual(wb_scheme("Склад WB"), "FBO")
        self.assertEqual(wb_scheme(""), "")

    def test_wb_orders_skip_cancels_in_sum(self):
        rows = aggregate_wb_orders([
            {"date": "2026-09-17T10:00:00", "supplierArticle": "ШО-White", "nmId": 1,
             "warehouseType": "Склад WB", "isCancel": False, "priceWithDisc": 1000,
             "finishedPrice": 800, "brand": "Vidovito", "subject": "Вешалки", "srid": "a"},
            {"date": "2026-09-17T11:00:00", "supplierArticle": "ШО-White", "nmId": 1,
             "warehouseType": "Склад WB", "isCancel": True, "priceWithDisc": 1000,
             "finishedPrice": 800, "brand": "Vidovito", "subject": "Вешалки", "srid": "b"},
        ], date(2026, 8, 1), date(2026, 9, 23))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["qty"], 1)
        self.assertEqual(rows[0]["cancelled"], 1)
        self.assertEqual(rows[0]["rub"], 1000)
        self.assertEqual(rows[0]["scheme"], "FBO")

    def test_ozon_realization_nets_returns(self):
        rows = aggregate_ozon_realization(date(2026, 9, 16), [
            {"item": {"offer_id": "BO-Black-2", "name": "Вешалка", "sku": 11},
             "seller_price_per_instance": 2570,
             "delivery_commission": {"quantity": 2, "standard_fee": 100, "compensation": 0},
             "return_commission": None},
            {"item": {"offer_id": "BO-Black-2", "name": "Вешалка", "sku": 11},
             "seller_price_per_instance": 2570,
             "delivery_commission": None,
             "return_commission": {"quantity": 1, "standard_fee": 40, "compensation": 0}},
        ])
        self.assertEqual(rows[0]["sale_qty"], 2)
        self.assertEqual(rows[0]["return_qty"], 1)
        self.assertEqual(rows[0]["sale_rub"], 5140)
        self.assertEqual(rows[0]["return_rub"], 2570)
        self.assertEqual(rows[0]["comm_sales"], 100)
        self.assertEqual(rows[0]["comm_returns"], 40)

    def test_ozon_accrual_splits_sale_return_and_logistics(self):
        rows = aggregate_ozon_accruals(date(2026, 8, 1), [
            {"unit_number": "A-1", "posting": {"delivery_schema": "Fbo", "products": [{
                "sku": 11, "quantity": 1,
                "commission": {"sale_amount": {"amount": "2490"}, "sale_commission": {"amount": "-1170"}},
                "delivery": {"services": [{"type_id": 32, "accrued": {"amount": "-217"}}]},
            }]}},
            {"unit_number": "A-2", "posting": {"delivery_schema": "Fbo", "products": [{
                "sku": 11, "quantity": 1,
                "commission": {"sale_amount": {"amount": "-1580"}, "sale_commission": {"amount": "805"}},
                "delivery": {"services": [{"type_id": 59, "accrued": {"amount": "-40"}}]},
            }]}},
        ], {"A-1": ("BO-Black-2", "Вешалка"), "A-2": ("BO-Black-2", "Вешалка")})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sale_qty"], 1)
        self.assertEqual(rows[0]["return_qty"], 1)
        self.assertEqual(rows[0]["sale_rub"], 2490)
        self.assertEqual(rows[0]["comm_sales"], 1170)
        self.assertEqual(rows[0]["comm_returns"], 805)
        self.assertEqual(rows[0]["log_sales"], -217)
        self.assertEqual(rows[0]["log_returns"], -40)
        self.assertEqual(rows[0]["scheme"], "FBO")

    def test_scheme_matches_sample_case(self):
        self.assertEqual(show_scheme("FBO", orders=True), "fbo")
        self.assertEqual(show_scheme("fbs", orders=False), "FBS")

    def test_yellow_note_names_the_reason(self):
        cols, why, how = explain(["ads", "cogs"], kind="order")
        self.assertIn(14, cols)
        self.assertIn(17, cols)
        self.assertIn(16, cols)
        self.assertIn("Реклама", why)
        self.assertIn("реклам", how.casefold())

    def test_yellow_ranges_merge(self):
        flags = [{0}, {0}, set(), {2}]
        self.assertEqual(yellow_ranges(flags, 3), ["A2:A3", "C5"])

    def test_missing_profit_header_is_restored(self):
        expected = ["К перечислению, руб.", "Себестоимость, руб.", "Прибыль, руб."]
        self.assertEqual(missing_header_tail(expected[:2], expected), ["Прибыль, руб."])
        self.assertEqual(missing_header_tail(expected, expected), [])
        self.assertIsNone(missing_header_tail(["Другое"], expected))


if __name__ == "__main__":
    unittest.main()
