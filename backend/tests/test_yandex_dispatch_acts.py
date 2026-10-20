import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend.routers import reshipment


class YandexDispatchActsTests(unittest.TestCase):
    def test_render_act_html_matches_handover_template(self):
        payload = [{
            "line_no": 1,
            "request_id": 212,
            "customer_track_number": "reship-212-deadbeef",
            "track_number": "YANDEX-212",
            "customer_name": "Иван Иванов",
            "customer_phone": "+79990000000",
            "city": "Казань",
            "pickup_address": "ул. Ленина, 1",
            "pickup_name": "ПВЗ",
            "items_to_send": "центральное крепление - 2 шт.",
            "shipping_cost": 390.41,
        }]

        html = reshipment._render_yandex_dispatch_act_html(
            act_number="YA-20260812-001",
            created_at=datetime.datetime(2026, 8, 12, 12, 0),
            payload=payload,
            total_shipping_cost=390.41,
            created_by="warehouse",
        )

        self.assertIn("Акт приема-передачи Отправления от 12.08.2026", html)
        self.assertIn('Заказчик:</strong> ООО "РНВ"', html)
        self.assertIn("reship-212-deadbeef", html)
        self.assertIn("Отправления принял (Сортировочный центр):", html)

    def test_get_stamp_data_uri_reads_image_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stamp_path = Path(tmpdir) / "stamp.png"
            stamp_path.write_bytes(b"png-binary")
            with unittest.mock.patch.object(reshipment, "_ACT_STAMP_PATH", str(stamp_path)):
                data_uri = reshipment._get_yandex_act_stamp_data_uri()

        self.assertTrue(data_uri.startswith("data:image/png;base64,"))

    def test_serialize_uses_yandex_operator_request_id(self):
        rows = [{
            "id": 212,
            "updated_at": datetime.datetime(2026, 8, 12, 12, 0),
            "customer_name": "Иван",
            "customer_phone": "+79990000000",
            "address_city": "Казань",
            "client_pvz_city": "Казань",
            "client_pvz_address": "",
            "yandex_pvz_name": "ПВЗ",
            "yandex_pvz_address": "ул. Ленина, 1",
            "items_to_send": "крепление",
            "track_number": "YANDEX-212",
            "shipping_cost": 390.41,
            "chat_token": "abc",
            "confirmation_token": None,
        }]
        with patch("backend.yandex_ndd.get_operator_request_id", return_value="reship-212-realid12"):
            payload = reshipment._serialize_yandex_dispatch_rows(rows)
        self.assertEqual(payload[0]["customer_track_number"], "reship-212-realid12")

    def test_next_yandex_act_number_increments_daily_sequence(self):
        db = Mock()
        execute_result = Mock()
        execute_result.scalar.return_value = "YA-20260812-004"
        db.execute.return_value = execute_result

        fake_today = datetime.date(2026, 8, 12)
        with unittest.mock.patch("backend.routers.reshipment.datetime") as fake_datetime:
            fake_datetime.date.today.return_value = fake_today
            fake_datetime.date.side_effect = datetime.date
            number = reshipment._next_yandex_act_number(db)

        self.assertEqual(number, "YA-20260812-005")


if __name__ == "__main__":
    unittest.main()
