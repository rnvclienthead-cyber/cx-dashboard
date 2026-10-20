import unittest

from backend.reshipment_costs import delivery_amounts


class DeliveryAmountsTests(unittest.TestCase):
    def test_cdek_uses_actual_when_present(self):
        a = delivery_amounts("cdek", cdek_cost=451.4, cdek_cost_actual=480.0)
        self.assertEqual(a["plan"], 451.4)
        self.assertEqual(a["fact"], 480.0)
        self.assertEqual(a["main"], 480.0)
        self.assertEqual(a["diff"], 28.6)

    def test_cdek_falls_back_to_plan(self):
        a = delivery_amounts("cdek", cdek_cost=451.4, cdek_cost_actual=None)
        self.assertEqual(a["main"], 451.4)
        self.assertIsNone(a["fact"])
        self.assertIsNone(a["diff"])

    def test_yandex_offer_is_fact(self):
        a = delivery_amounts("yandex", yandex_cost=383.11, shipping_cost=278.16)
        self.assertEqual(a["plan"], 383.11)
        self.assertEqual(a["fact"], 278.16)
        self.assertEqual(a["main"], 278.16)
        self.assertEqual(a["diff"], -104.95)

    def test_ozon_uses_quote_as_plan(self):
        a = delivery_amounts("ozon", ozon_cost=210.0, shipping_cost=210.0)
        self.assertEqual(a["plan"], 210.0)
        self.assertEqual(a["fact"], 210.0)
        self.assertEqual(a["main"], 210.0)
        self.assertEqual(a["diff"], 0.0)

    def test_ozon_falls_back_to_quote(self):
        a = delivery_amounts("ozon", ozon_cost=210.0, shipping_cost=None)
        self.assertEqual(a["plan"], 210.0)
        self.assertEqual(a["main"], 210.0)
        self.assertIsNone(a["fact"])

    def test_ozon_accrual_overrides_quote(self):
        a = delivery_amounts(
            "ozon", ozon_cost=104.0, shipping_cost=104.0, ozon_cost_actual=81.17,
        )
        self.assertEqual(a["plan"], 104.0)
        self.assertEqual(a["fact"], 81.17)
        self.assertEqual(a["main"], 81.17)
        self.assertEqual(a["diff"], -22.83)


class OzonPhoneTests(unittest.TestCase):
    def test_normalize_phone(self):
        from backend.ozon_delivery import normalize_phone
        self.assertEqual(normalize_phone("8 (903) 123-45-67"), "+79031234567")
        self.assertEqual(normalize_phone("79031234567"), "+79031234567")
        self.assertEqual(normalize_phone("9031234567"), "+79031234567")
        self.assertIsNone(normalize_phone("123"))


if __name__ == "__main__":
    unittest.main()
