import unittest

from backend import chat_faq


class ChatFaqTests(unittest.TestCase):
    def test_zapchasti_fallback_is_marketplace_specific(self):
        self.assertIn("Wildberries", chat_faq.faq_fallback("zapchasti", "wb"))
        self.assertIn("Ozon", chat_faq.faq_fallback("zapchasti", "ozon"))
        self.assertIn("Яндекс Маркете", chat_faq.faq_fallback("zapchasti", "ym"))

    def test_krepezh_fallback_has_no_cross_marketplace_link(self):
        self.assertNotIn("Ozon", chat_faq.faq_fallback("krepezh", "wb"))
        self.assertNotIn("Wildberries", chat_faq.faq_fallback("krepezh", "ozon"))
        self.assertNotIn("Wildberries", chat_faq.faq_fallback("krepezh", "ym"))


if __name__ == "__main__":
    unittest.main()
