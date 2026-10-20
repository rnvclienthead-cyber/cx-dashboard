import unittest
from unittest.mock import MagicMock, patch

import requests

import backend.salebot as salebot
from backend.salebot import format_delivery_status_message, format_eta_date, parse_eta_date


class SalebotSendRetryTests(unittest.TestCase):
    def _resp(self, code, text="{}"):
        r = MagicMock()
        r.status_code = code
        r.text = text
        r.headers = {}
        return r

    @patch("backend.salebot.SALEBOT_API_TOKEN", "tkn")
    @patch("backend.http_client.time.sleep", lambda *_: None)
    @patch("backend.http_client.requests.request")
    def test_transient_error_is_retried_then_succeeds(self, req):
        # Обрыв сети, потом 503, потом успех — через общий помощник доходит.
        req.side_effect = [requests.ConnectionError("network"), self._resp(503), self._resp(200)]
        self.assertTrue(salebot.send_message("c1", "hi"))
        self.assertEqual(req.call_count, 3)

    @patch("backend.salebot.SALEBOT_API_TOKEN", "tkn")
    @patch("backend.http_client.time.sleep", lambda *_: None)
    @patch("backend.http_client.requests.request")
    def test_blocked_client_is_not_retried(self, req):
        # Клиент заблокировал бота (403) — повторять бессмысленно, одна попытка.
        req.return_value = self._resp(403)
        self.assertFalse(salebot.send_message("c1", "hi"))
        self.assertEqual(req.call_count, 1)

ITEMS = (
    "Нижняя рамка 1шт Вертикальные 2шт Короткая трубка 1шт "
    "Длинная трубка 3шт Болты 18 штук Ножки 4шт Накладка 4 шт Ключ"
)
PVZ = "г. Московский, ул. Московская, 23г"


class DeliveryStatusMessageTests(unittest.TestCase):
    def test_in_transit_ozon_has_address_not_items(self):
        msg = format_delivery_status_message(
            "in_transit",
            track_number="07B457097482-4P1P",
            delivery_method="ozon",
            items_to_send=ITEMS,
            pvz_address=PVZ,
            pvz_name="Пункт Ozon",
        )
        self.assertNotIn("Что внутри", msg)
        self.assertNotIn("Нижняя рамка", msg)
        self.assertNotIn("Болты", msg)
        self.assertIn("Адрес", msg)
        self.assertIn(PVZ, msg)
        self.assertIn("07B457097482-4P1P", msg)
        self.assertIn("скоро прибудет", msg.lower())

    def test_in_transit_includes_eta_instead_of_soon(self):
        msg = format_delivery_status_message(
            "in_transit",
            track_number="07B457097482-4P1P",
            delivery_method="ozon",
            pvz_address=PVZ,
            eta_date="2026-09-18T14:00:00Z",
        )
        self.assertIn("Ориентировочно в пункте: 18 сентября", msg)
        self.assertNotIn("Скоро прибудет", msg)

    def test_shipped_cdek_has_address_not_items(self):
        msg = format_delivery_status_message(
            "shipped",
            track_number="10319240272",
            delivery_method="cdek",
            items_to_send=ITEMS,
            pvz_address=PVZ,
        )
        self.assertNotIn("Что внутри", msg)
        self.assertNotIn("Нижняя рамка", msg)
        self.assertIn(PVZ, msg)
        self.assertIn("10319240272", msg)

    def test_ready_for_pickup_still_has_address(self):
        msg = format_delivery_status_message(
            "ready_for_pickup",
            track_number="T-1",
            delivery_method="yandex",
            items_to_send=ITEMS,
            pvz_address=PVZ,
            pvz_name="ПВЗ Яндекс",
        )
        self.assertNotIn("Что внутри", msg)
        self.assertIn(PVZ, msg)
        self.assertIn("готова к выдаче", msg)
        self.assertIn("Яндекс Доставка", msg)
        self.assertNotIn("СДЭК", msg)

    def test_pickup_reminder_uses_actual_carrier(self):
        from backend.salebot import format_pickup_reminder
        yandex = format_pickup_reminder(
            delivery_method="yandex",
            pvz_address="Новосибирск Пригородная улица 30",
            sharing_url="https://dostavka.yandex.ru/route/#abc",
        )
        self.assertIn("Яндекс Доставка", yandex)
        self.assertNotIn("СДЭК", yandex)
        self.assertIn("Пригородная", yandex)
        ozon = format_pickup_reminder(
            delivery_method="ozon",
            pvz_address=PVZ,
            track_number="07B457097482-4P1P",
        )
        self.assertIn("Ozon Доставка", ozon)
        self.assertNotIn("СДЭК", ozon)
        cdek = format_pickup_reminder(
            delivery_method="cdek",
            pvz_address=PVZ,
            keep_until="20 сентября до 21:00 (МСК)",
        )
        self.assertIn("СДЭК", cdek)
        self.assertIn("20 сентября", cdek)


class EtaParseTests(unittest.TestCase):
    def test_ozon_delivery_at_utc(self):
        self.assertEqual(format_eta_date("2026-09-18T14:00:00Z"), "18 сентября")

    def test_cdek_date_only(self):
        self.assertEqual(format_eta_date("2026-09-17"), "17 сентября")

    def test_yandex_offset_without_colon(self):
        self.assertEqual(format_eta_date("2026-09-16T07:00:00+0000"), "16 сентября")
        self.assertIsNotNone(parse_eta_date(1789542000))

    def test_returned_does_not_offer_resend(self):
        msg = format_delivery_status_message("returned", delivery_method="yandex")
        self.assertIn("не была получена", msg)
        self.assertNotIn("повторно", msg)


class ApprovedMessageTests(unittest.TestCase):
    def test_ozon_does_not_promise_track(self):
        from backend.salebot import format_approved_message
        msg = format_approved_message("ozon")
        self.assertIn("одобрена", msg.lower())
        self.assertNotIn("трек", msg.lower())
        self.assertIn("Ozon", msg)
        self.assertIn("следующим сообщением", msg)

    def test_cdek_keeps_track(self):
        from backend.salebot import format_approved_message
        msg = format_approved_message("cdek")
        self.assertIn("трек-номер", msg)


if __name__ == "__main__":
    unittest.main()
