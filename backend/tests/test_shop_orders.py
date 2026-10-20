"""Заказы витрины: нормализация, посылки, доступ."""
import unittest

from backend import shop_orders
from backend.access import PUBLIC, MODULES, classify_path, decide_access


class NormalizeIngestTests(unittest.TestCase):
    def test_order_from_admin_card_keeps_customer_and_sku(self):
        data = shop_orders.normalize_ingest({
            "cscart_order_id": 1,
            "status": "O",
            "paid": False,
            "payment_name": "Тестовый заказ — без оплаты",
            "shipping_method": "Тестовая доставка — 0 руб",
            "total": 2199,
            "customer": {
                "name": "RNV digital",
                "email": "rnv.digital.dep@gmail.com",
                "phone": "+79099991191",
            },
            "shipping_address": {
                "city": "Казань",
                "address": "пр. Фатыха Амирхана, д. 21",
                "zip": "420126",
                "state": "Татарстан",
                "country": "Россия",
            },
            "shop_barcode": "FF45CR991",
            "items": [{
                "product_id": 12,
                "product_code": "КОД-VV-0007",
                "name": "Вешалка 2 рейла",
                "amount": 1,
                "price": 2199,
            }],
        })
        self.assertEqual(data["cscart_order_id"], 1)
        self.assertTrue(data["paid"])
        self.assertEqual(data["customer_phone"], "+79099991191")
        self.assertEqual(data["shop_barcode"], "FF45CR991")
        self.assertEqual(data["items"][0]["product_code"], "КОД-VV-0007")
        self.assertEqual(data["items"][0]["length_cm"], 40)

    def test_two_units_become_two_parcels(self):
        items = shop_orders.explode_items([
            {"name": "Вешалка", "amount": 2, "weight_g": 1500, "length_cm": 80, "width_cm": 10, "height_cm": 10},
        ])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["qty"], 1)
        self.assertEqual(items[1]["line_no"], 2)
        self.assertEqual(items[0]["weight_g"], 1500)

    def test_delivery_pvz_is_kept(self):
        data = shop_orders.normalize_ingest({
            "order_id": 8,
            "delivery": {
                "carrier": "ozon",
                "pvz_code": "12345",
                "pvz_address": "Казань, Тульская 40",
                "cost": 0,
            },
            "items": [{"name": "Полка", "amount": 1}],
        })
        self.assertEqual(data["cscart_order_id"], 8)
        self.assertEqual(data["carrier"], "ozon")
        self.assertEqual(data["pvz_code"], "12345")
        self.assertEqual(data["shipping_cost"], 0.0)

    def test_cancelled_cscart_status(self):
        data = shop_orders.normalize_ingest({"cscart_order_id": 3, "cscart_status": "I", "items": []})
        self.assertEqual(data["cscart_status"], "I")


class AccessTests(unittest.TestCase):
    def test_ingest_is_public_token_inside(self):
        self.assertEqual(classify_path("/api/v1/shop/orders/ingest")[0], PUBLIC)

    def test_list_needs_shop_orders_module(self):
        kind, needed = classify_path("/api/v1/shop/orders")
        self.assertEqual(kind, MODULES)
        self.assertEqual(needed, frozenset({"shop_orders"}))
        self.assertEqual(decide_access("/api/v1/shop/orders", "cs_manager", {"reshipment"}), 403)
        self.assertIsNone(decide_access("/api/v1/shop/orders", "warehouse_manager", {"shop_orders"}))
        self.assertIsNone(decide_access("/api/v1/shop/orders", "admin", set()))

    def test_delivery_quote_stays_public(self):
        self.assertEqual(classify_path("/api/v1/shop/delivery/quote")[0], PUBLIC)


class CarrierReqIdTests(unittest.TestCase):
    def test_shop_ids_do_not_overlap_reshipment(self):
        self.assertEqual(shop_orders._carrier_req_id(1), 900001)


class LockedReplaceTests(unittest.TestCase):
    def test_packing_is_locked(self):
        self.assertIn("packing", shop_orders.LOCKED_STATUSES)
        self.assertNotIn("new", shop_orders.LOCKED_STATUSES)


class StatusMappingTests(unittest.TestCase):
    def test_packed_and_later_go_to_cscart(self):
        self.assertEqual(shop_orders.cscart_status_for("new"), "placed")
        self.assertEqual(shop_orders.cscart_status_for("paid"), "paid")
        self.assertEqual(shop_orders.cscart_status_for("packed"), "packed")
        self.assertEqual(shop_orders.cscart_status_for("shipped"), "shipped")
        self.assertEqual(shop_orders.cscart_status_for("in_transit"), "in_transit")
        self.assertEqual(shop_orders.cscart_status_for("ready_for_pickup"), "ready_for_pickup")
        self.assertEqual(shop_orders.cscart_status_for("delivered"), "delivered")
        self.assertEqual(shop_orders.cscart_status_for("returned"), "returned")
        self.assertEqual(shop_orders.cscart_status_for("cancelled_seller"), "cancelled_seller")
        self.assertEqual(shop_orders.cscart_status_for("cancelled_buyer"), "cancelled_buyer")
        self.assertEqual(shop_orders.cscart_status_for("incomplete"), "incomplete")
        self.assertEqual(shop_orders.cscart_status_for("packing"), "")

    def test_site_title_sets_cx_status(self):
        self.assertEqual(shop_orders.shop_status_from_payload({"status_title": "Оплачен"}), "paid")
        self.assertEqual(shop_orders.shop_status_from_payload({"status_title": "Возвращен"}), "returned")
        self.assertEqual(shop_orders.shop_status_from_payload({"status_title": "Отменен продавцом"}), "cancelled_seller")
        self.assertEqual(shop_orders.shop_status_from_payload({"status_title": "Отменён покупателем"}), "cancelled_buyer")
        self.assertEqual(shop_orders.shop_status_from_payload({"status_title": "Незавершенный заказ"}), "incomplete")
        self.assertEqual(shop_orders.shop_status_from_payload({"status_title": "Заказ оформлен"}), "new")
        self.assertEqual(shop_orders.shop_status_from_payload({"status_kind": "in_transit"}), "in_transit")

    def test_old_letter_does_not_rewind_warehouse(self):
        packed = shop_orders.resolve_shop_status("packed", {"cscart_status": "P"})
        self.assertEqual(packed, "packed")
        paid = shop_orders.resolve_shop_status("new", {"cscart_status": "P"})
        self.assertEqual(paid, "paid")
        explicit = shop_orders.resolve_shop_status("packed", {
            "cscart_status": "P",
            "status_title": "Оплачен",
            "status_kind": "paid",
        })
        self.assertEqual(explicit, "paid")
        held = shop_orders.resolve_shop_status("in_transit", {"cscart_status": "I"})
        self.assertEqual(held, "in_transit")

    def test_push_stores_cscart_letter_not_long_word(self):
        stored = shop_orders._cscart_stored_status("in_transit", {
            "status": "ok",
            "data": {"ok": True, "status": "T", "kind": "in_transit"},
        })
        self.assertEqual(stored, "T")
        self.assertLessEqual(len(stored), 8)
        fallback = shop_orders._cscart_stored_status("in_transit", {"status": "ok"})
        self.assertEqual(fallback, "in_transit")
        self.assertLessEqual(len(fallback), 32)

    def test_sync_ozon_does_not_stop_on_one_item(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "shop_orders.py").read_text()
        fn = src.split("def sync_open_ozon(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("db.rollback()", fn)
        self.assertIn("continue", fn)
        self.assertIn("VARCHAR(32)", src)

    def test_rollup_follows_the_slowest_parcel(self):
        self.assertEqual(shop_orders.rollup_from_items(["packed", "shipped"]), "packed")
        self.assertEqual(shop_orders.rollup_from_items(["new", "packed"]), "packing")
        self.assertEqual(shop_orders.rollup_from_items(["shipped", "in_transit"]), "shipped")
        self.assertEqual(shop_orders.rollup_from_items(["delivered", "delivered"]), "delivered")
        self.assertEqual(shop_orders.rollup_from_items(["returned", "returned"]), "returned")

    def test_cdek_codes_match_reshipment(self):
        self.assertEqual(shop_orders.CDEK_STATUS_MAP["RECEIVED_AT_SHIPMENT_WAREHOUSE"], "shipped")
        self.assertEqual(shop_orders.CDEK_STATUS_MAP["ACCEPTED_AT_PICK_UP_POINT"], "ready_for_pickup")
        self.assertEqual(shop_orders.CDEK_STATUS_MAP["DELIVERED"], "delivered")
        self.assertEqual(shop_orders.CDEK_STATUS_MAP["TAKEN_BY_COURIER"], "in_transit")
        self.assertEqual(shop_orders.CDEK_STATUS_MAP["RETURNED_TO_SENDER"], "returned")

    def test_manual_status_aligns_parcels_and_is_admin_only(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "shop_orders.py").read_text()
        fn = src.split("def set_status(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("_align_items_to_status", fn)
        self.assertNotIn("ELSE 'shipped'", fn)
        router = (Path(__file__).resolve().parents[1] / "routers" / "shop_orders.py").read_text()
        gate = router.split("def set_status(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('role") != "admin"', gate)
        self.assertIn("shop_order_status_log", src)
        self.assertIn("def status_history", src)

    def test_shop_shipment_uses_same_carriers(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "shop_orders.py").read_text()
        fn = src.split("def create_item_shipment(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('carrier == "ozon"', fn)
        self.assertIn('carrier == "cdek"', fn)
        self.assertIn('carrier == "yandex"', fn)
        self.assertIn("Заказ сайта", fn)

    def test_label_rows_need_track(self):
        rows = shop_orders.label_rows_for_order({
            "carrier": "cdek",
            "customer_name": "Анна",
            "shipping_city": "Казань",
            "items": [
                {"id": 1, "track_number": "123", "has_label": True, "status": "packed"},
                {"id": 2, "track_number": "", "has_label": False, "status": "new"},
            ],
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cdek_number"], "123")
        self.assertEqual(rows[0]["delivery_method"], "cdek")


class TrackingUrlTests(unittest.TestCase):
    def test_cdek_track_page(self):
        url = shop_orders.tracking_url_for("cdek", {"track_number": "1234567890"})
        self.assertEqual(url, "https://www.cdek.ru/track.html?order_id=1234567890")

    def test_yandex_prefers_sharing_url(self):
        url = shop_orders.tracking_url_for("yandex", {
            "track_number": "abc-udp",
            "yandex_sharing_url": "https://dostavka.yandex.ru/route/#xyz",
        })
        self.assertEqual(url, "https://dostavka.yandex.ru/route/#xyz")

    def test_ozon_uses_posting_or_order(self):
        url = shop_orders.tracking_url_for("ozon", {
            "ozon_posting_number": "1234-001",
            "track_number": "",
        })
        self.assertIn("1234-001", url)
        self.assertTrue(url.startswith("https://www.ozon.ru/"))

    def test_push_payload_has_parcels_and_distinct_status(self):
        body = shop_orders.cscart_push_payload({
            "cscart_order_id": 11,
            "status": "in_transit",
            "carrier": "ozon",
            "pvz_name": "Ozon",
            "pvz_address": "Казань, Четева 20",
            "items": [{
                "name": "Вешалка",
                "product_code": "VV-0008",
                "status": "in_transit",
                "status_label": "В пути",
                "track_number": "OZ-11",
                "ozon_posting_number": "OZ-11",
            }],
        })
        self.assertEqual(body["status"], "in_transit")
        self.assertEqual(body["order_id"], 11)
        self.assertEqual(len(body["parcels"]), 1)
        self.assertEqual(body["parcels"][0]["track_number"], "OZ-11")
        self.assertIn("OZ-11", body["parcels"][0]["tracking_url"])

    def test_push_payload_keeps_tracks_before_packed(self):
        body = shop_orders.cscart_push_payload({
            "cscart_order_id": 15,
            "status": "new",
            "carrier": "ozon",
            "items": [{
                "name": "Вешалка",
                "status": "new",
                "track_number": "EB-1",
                "ozon_posting_number": "P-1",
            }],
        })
        self.assertEqual(body["status"], "placed")
        self.assertEqual(body["parcels"][0]["track_number"], "EB-1")

    def test_ozon_parcel_uses_shop_order_details(self):
        order = {
            "cscart_order_id": 15,
            "shop_barcode": "FF45CR991",
            "customer_name": "Рустам Мур",
            "shipping_city": "Альметьевск",
            "total": 2199,
        }
        item = {
            "name": "Вешалка для ванной",
            "product_code": "VV-BX-244",
            "price": 2199,
            "weight_g": 1500,
            "length_cm": 40,
            "width_cm": 30,
            "height_cm": 20,
        }
        text = shop_orders.ozon_parcel_description(order, item)
        self.assertIn("Заказ сайта #15", text)
        self.assertIn("Рустам Мур", text)
        self.assertIn("VV-BX-244", text)
        self.assertEqual(shop_orders.ozon_parcel_declared(order, item), 2199)
        self.assertEqual(shop_orders.ozon_parcel_dims(item)["weight_g"], 1500)
        self.assertEqual(shop_orders.ozon_parcel_dims(item)["length_mm"], 400)

    def test_ozon_create_uses_existing_dropoff_method(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] / "shop_orders.py").read_text()
        fn = src.split("def create_item_shipment(", 1)[1].split("\ndef mark_item_shipped", 1)[0]
        pack = src.split("def pack_item(", 1)[1].split("\ndef _carrier_req_id", 1)[0]
        self.assertIn("dropoff_barcode_info", fn)
        self.assertIn("shipment_method_id", fn)
        self.assertIn("declared_cost", fn)
        self.assertIn("ozon_parcel_description", fn)
        self.assertNotIn("ELSE 'packed'", fn)
        self.assertNotIn("create_item_shipment", pack)


if __name__ == "__main__":
    unittest.main()
