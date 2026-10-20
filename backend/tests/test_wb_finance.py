import unittest
from unittest.mock import patch, Mock

from backend import wb_client, wb_finance


class WbFinanceTests(unittest.TestCase):
    def test_maps_v1_camelcase_and_money_strings(self):
        row = wb_finance.normalize_row({
            "rrdId": 123,
            "reportId": 307401554,
            "dateFrom": "2026-03-16",
            "dateTo": "2026-03-22",
            "rrDate": "2026-03-21",
            "docTypeName": "Продажа",
            "vendorCode": "MAB123",
            "nmId": 169887124,
            "sku": "2030000000000",
            "srid": "abc.srid",
            "quantity": 1,
            "retailPrice": "1249",
            "retailAmount": "367",
            "forPay": "376.99",
            "ppvzSalesCommission": "23.74",
            "deliveryService": "255.10",
            "paidStorage": "12.5",
            "penalty": "0",
            "deduction": "1",
            "paidAcceptance": "8",
            "rebillLogisticCost": "1.349",
            "acquiringFee": "14.89",
            "sellerPromo": "0",
            "additionalPayment": "0",
            "commissionPercent": "24",
            "uuidPromocode": "  CODE  ",
            "salePricePromocodeDiscountPrc": "3",
            "shkId": 35548064307,
            "stickerId": "35548064307",
            "trbxId": "TRBX3445947623",
            "giId": 36566148,
        })
        self.assertEqual(row["rrd_id"], 123)
        self.assertEqual(row["realizationreport_id"], 307401554)
        self.assertEqual(row["sa_name"], "MAB123")
        self.assertEqual(row["barcode"], "2030000000000")
        self.assertEqual(row["ppvz_for_pay"], 376.99)
        self.assertEqual(row["delivery_rub"], 255.10)
        self.assertEqual(row["storage_fee"], 12.5)
        self.assertEqual(row["acceptance"], 8.0)
        self.assertEqual(row["uuid_promocode"], "CODE")
        self.assertEqual(row["shk_id"], 35548064307)
        self.assertEqual(row["sticker_id"], "35548064307")
        self.assertEqual(row["trbx_id"], "TRBX3445947623")
        self.assertEqual(row["gi_id"], 36566148)

    def test_maps_legacy_snake_case(self):
        row = wb_finance.normalize_row({
            "rrd_id": 9,
            "sa_name": "OLD",
            "ppvz_for_pay": 10,
            "delivery_rub": 4,
        })
        self.assertEqual(row["rrd_id"], 9)
        self.assertEqual(row["sa_name"], "OLD")
        self.assertEqual(row["ppvz_for_pay"], 10.0)
        self.assertEqual(row["delivery_rub"], 4.0)

    def test_skips_row_without_rrd_id(self):
        self.assertIsNone(wb_finance.normalize_row({"vendorCode": "X"}))

    def test_parse_rows_handles_204_and_list(self):
        self.assertEqual(wb_finance.parse_rows(None, 204), [])
        rows = wb_finance.parse_rows([{"rrdId": 1, "forPay": "1.5"}], 200)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ppvz_for_pay"], 1.5)

    def test_request_body_uses_rrd_id_cursor(self):
        body = wb_finance.request_body("2026-04-01", "2026-09-04", rrd_id=50, limit=10000)
        self.assertEqual(body["rrdId"], 50)
        self.assertEqual(body["period"], "weekly")
        self.assertNotIn("rrdid", body)


class WbAnswerFeedbackTests(unittest.TestCase):
    def test_posts_to_feedbacks_answer(self):
        ok = Mock(status_code=200)
        ok.raise_for_status = Mock()
        with patch("backend.wb_client.http_client.request", return_value=ok) as req:
            wb_client.answer_feedback("abc", "  Спасибо за отзыв!  ")
        # http_client.request(method, url, ...): method=args[0], url=args[1]
        self.assertEqual(req.call_args.args[0], "POST")
        self.assertTrue(req.call_args.args[1].endswith("/api/v1/feedbacks/answer"))
        self.assertEqual(req.call_args.kwargs["json"], {"id": "abc", "text": "Спасибо за отзыв!"})

    def test_patches_when_already_answered(self):
        conflict = Mock(status_code=409)
        ok = Mock(status_code=200)
        ok.raise_for_status = Mock()
        with patch("backend.wb_client.http_client.request", side_effect=[conflict, ok]) as req:
            wb_client.answer_feedback("abc", "Обновлённый текст")
        # Второй вызов — PATCH на тот же адрес.
        self.assertEqual(req.call_args.args[0], "PATCH")
        self.assertTrue(req.call_args.args[1].endswith("/api/v1/feedbacks/answer"))

    def test_rejects_short_text(self):
        with self.assertRaises(ValueError):
            wb_client.answer_feedback("abc", " ")


if __name__ == "__main__":
    unittest.main()
