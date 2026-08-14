import unittest

from backend import chat_faq
from backend.routers import chat_playbook


class ChatFaqTests(unittest.TestCase):
    def test_zapchasti_fallback_is_marketplace_specific(self):
        self.assertIn("Wildberries", chat_faq.faq_fallback("zapchasti", "wb"))
        self.assertIn("Ozon", chat_faq.faq_fallback("zapchasti", "ozon"))
        self.assertIn("Яндекс Маркете", chat_faq.faq_fallback("zapchasti", "ym"))
        self.assertNotIn("ozon.ru", chat_faq.faq_fallback("zapchasti", "wb").lower())

    def test_zapchasti_out_of_stock_fallback(self):
        wb = chat_faq.faq_fallback("zapchasti", "wb", branch="out_of_stock")
        oz = chat_faq.faq_fallback("zapchasti", "ozon", branch="out_of_stock")
        self.assertIn("нет в наличии", wb.lower())
        self.assertIn("нет в наличии", oz.lower())
        self.assertNotIn("ozon.ru", oz.lower())

    def test_krepezh_fallback_has_no_cross_marketplace_link(self):
        self.assertNotIn("Ozon", chat_faq.faq_fallback("krepezh", "wb"))
        self.assertNotIn("Wildberries", chat_faq.faq_fallback("krepezh", "ozon"))
        self.assertNotIn("Wildberries", chat_faq.faq_fallback("krepezh", "ym"))

    def test_wb_seed_zapchasti_has_no_ozon_link(self):
        bodies = [
            body for theme, branch, _bl, body, _s in chat_playbook.SEED_VARIANTS
            if theme == "zapchasti"
        ]
        self.assertTrue(bodies)
        for body in bodies:
            self.assertNotIn("ozon.ru", body.lower())
        in_stock = [
            body for theme, branch, _bl, body, _s in chat_playbook.SEED_VARIANTS
            if theme == "zapchasti" and branch == "in_stock"
        ]
        self.assertTrue(in_stock)
        self.assertIn("Wildberries", in_stock[0])

        oz_in = [
            body for theme, branch, _bl, body, _s in chat_playbook.SEED_OZON_VARIANTS
            if theme == "zapchasti" and branch == "in_stock"
        ]
        self.assertTrue(any("ozon.ru" in b.lower() for b in oz_in))
        oz_out = [
            body for theme, branch, _bl, body, _s in chat_playbook.SEED_OZON_VARIANTS
            if theme == "zapchasti" and branch == "out_of_stock"
        ]
        self.assertTrue(oz_out)
        self.assertNotIn("ozon.ru", oz_out[0].lower())


if __name__ == "__main__":
    unittest.main()
