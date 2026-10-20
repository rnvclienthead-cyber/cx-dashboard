import unittest
from datetime import date

from backend.routers.reshipment import _skip_reject_stock_template
from backend.wb_mini_sync import format_rejection_promo_message


class SkipRejectStockTemplateTests(unittest.TestCase):
    def test_promo_skips_even_without_claim(self):
        self.assertTrue(_skip_reject_stock_template("CQ4GYW7H", {"ok": False, "skipped": "no_claim"}))
        self.assertTrue(_skip_reject_stock_template("CQ4GYW7H", None))

    def test_existing_claim_skips_without_promo(self):
        self.assertTrue(_skip_reject_stock_template(None, {"ok": True, "claim_id": "abc"}))
        self.assertTrue(_skip_reject_stock_template(None, {"ok": False, "claim_id": "abc"}))

    def test_no_promo_no_claim_sends_template(self):
        self.assertFalse(_skip_reject_stock_template(None, None))
        self.assertFalse(_skip_reject_stock_template("", {"ok": False, "skipped": "no_claim"}))


class RejectionPromoMessageTests(unittest.TestCase):
    def test_wb_includes_no_stock_approved_return_and_discount(self):
        msg = format_rejection_promo_message(
            "CQ4GYW7H", date(2027, 1, 30), has_review=False, marketplace="wb",
        )
        self.assertIn("отправить деталь не получится", msg)
        self.assertIn("одобрили ваш возврат", msg)
        self.assertIn("Wildberries", msg)
        self.assertIn("CQ4GYW7H", msg)
        self.assertIn("скидку 15%", msg)
        self.assertIn("до 30 января", msg)
        self.assertIn("Александр из команды Видовито", msg)
        self.assertIn("Будем рады снова видеть", msg)

    def test_wb_with_review_asks_to_update(self):
        msg = format_rejection_promo_message("CODE", has_review=True, marketplace="wb")
        self.assertIn("обновить отзыв", msg)
        self.assertNotIn("Будем рады снова видеть", msg)

    def test_ym_asks_to_return_in_market_lk(self):
        msg = format_rejection_promo_message("CQ4GYW7H", marketplace="ym")
        self.assertIn("отправить деталь не получится", msg)
        self.assertIn("Яндекс Маркета", msg)
        self.assertIn("оформите возврат", msg.lower())
        self.assertNotIn("одобрили ваш возврат", msg)
        self.assertIn("Применить его можно при оформлении заказа на Wildberries", msg)


if __name__ == "__main__":
    unittest.main()
