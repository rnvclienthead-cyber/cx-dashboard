import unittest
from unittest.mock import patch

from backend import yandex_ndd


class YandexNddQuoteTests(unittest.TestCase):
    def test_quote_and_create_use_tulskaya_not_warehouse(self):
        self.assertEqual(
            yandex_ndd._source_pvz_id(yandex_ndd.WAREHOUSE_PVZ_ID),
            yandex_ndd.TULSKAYA_PVZ_ID,
        )
        with patch.object(yandex_ndd, "FROM_PVZ_ADDRESS", "Казань, Техническая улица, 17Б"):
            self.assertEqual(
                yandex_ndd._quote_source_address("Казань, Техническая улица, 17Б"),
                "Казань, Тульская 40",
            )
        payload = yandex_ndd._quote_source_payload()
        self.assertEqual(payload["platform_station_id"], yandex_ndd.TULSKAYA_PVZ_ID)

    def test_calculate_price_sends_tulskaya_station(self):
        with patch.object(yandex_ndd, "FROM_PVZ_ID", yandex_ndd.WAREHOUSE_PVZ_ID):
            with patch.object(
                yandex_ndd, "_post", return_value=(200, {"pricing_total": "337.97 RUB"})
            ) as post:
                cost = yandex_ndd.calculate_price("dest-pvz")
        self.assertEqual(cost, 337.97)
        body = post.call_args[0][1]
        self.assertEqual(body["source"]["platform_station_id"], yandex_ndd.TULSKAYA_PVZ_ID)

    def test_create_order_drops_off_at_tulskaya(self):
        create_resp = (200, {"offers": [{"offer_id": "off-1", "offer_details": {"pricing_total": "300 RUB"}}]})
        confirm_resp = (200, {"request_id": "req-1"})
        with patch.object(yandex_ndd, "FROM_PVZ_ID", yandex_ndd.WAREHOUSE_PVZ_ID):
            with patch.object(yandex_ndd, "_post", side_effect=[create_resp, confirm_resp]) as post:
                result = yandex_ndd.create_order(
                    req_id=410,
                    customer_name="Клиент",
                    customer_phone="79990000000",
                    destination_pvz_id="dest-pvz",
                    source_pvz_id=yandex_ndd.WAREHOUSE_PVZ_ID,
                )
        self.assertEqual(result["request_id"], "req-1")
        body = post.call_args_list[0][0][1]
        self.assertEqual(
            body["source"]["platform_station"]["platform_id"],
            yandex_ndd.TULSKAYA_PVZ_ID,
        )
        self.assertNotIn("interval_utc", body["source"])


if __name__ == "__main__":
    unittest.main()
