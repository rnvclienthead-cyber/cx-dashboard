import unittest

from backend import chat_faq
from backend.routers import chat_playbook


class ChatFaqTests(unittest.TestCase):
    def test_zapchasti_fallback_is_marketplace_specific(self):
        self.assertIn("Wildberries", chat_faq.faq_fallback("zapchasti", "wb"))
        self.assertIn("Ozon", chat_faq.faq_fallback("zapchasti", "ozon"))
        self.assertIn("Яндекс Маркете", chat_faq.faq_fallback("zapchasti", "ym"))
        self.assertNotIn("ozon.ru", chat_faq.faq_fallback("zapchasti", "wb").lower())

    def test_krepezh_fallback_has_no_cross_marketplace_link(self):
        self.assertNotIn("Ozon", chat_faq.faq_fallback("krepezh", "wb"))
        self.assertNotIn("Wildberries", chat_faq.faq_fallback("krepezh", "ozon"))
        self.assertNotIn("Wildberries", chat_faq.faq_fallback("krepezh", "ym"))

    def test_wb_seed_zapchasti_has_no_ozon_link(self):
        bodies = [
            body for theme, _bk, _bl, body, _s in chat_playbook.SEED_VARIANTS
            if theme == "zapchasti"
        ]
        self.assertTrue(bodies)
        for body in bodies:
            self.assertNotIn("ozon.ru", body.lower())
            self.assertIn("Wildberries", body)


if __name__ == "__main__":
    unittest.main()
