import unittest
from unittest.mock import patch

from backend import ozon_delivery


class NearestPvzTests(unittest.TestCase):
    def test_picks_closest_active_point(self):
        points = [
            {"id": 1, "lat": 55.83, "lng": 49.09, "active": True, "address": "far"},
            {"id": 2, "lat": 55.831, "lng": 49.091, "active": True, "address": "near"},
            {"id": 3, "lat": 55.831, "lng": 49.091, "active": False, "address": "closed"},
        ]
        best = ozon_delivery._nearest(points, 55.8311, 49.0911)
        self.assertEqual(best["id"], 2)
        self.assertLess(best["distance_km"], 1)

    def test_rejects_beyond_max_km(self):
        points = [{"id": 1, "lat": 55.83, "lng": 49.09, "active": True}]
        self.assertIsNone(ozon_delivery._nearest(points, 60.0, 30.0))


class ListPaginationTests(unittest.TestCase):
    def test_reads_past_old_20k_cap(self):
        pages = {i: [{"delivery_point_id": i * 100 + j} for j in range(100)] for i in range(201)}
        pages[201] = []

        def fake_post(path, body, timeout=25, idempotency_key=None):
            self.assertEqual(path, "/v1/delivery-point/list")
            cursor = (body.get("pagination") or {}).get("cursor")
            page = 0 if cursor is None else int(cursor)
            chunk = pages.get(page) or []
            nxt = page + 1 if chunk and page + 1 in pages and pages[page + 1] else None
            return 200, {"delivery_points": chunk, "next_cursor": nxt}

        ozon_delivery._points_cache = (0.0, [])
        with patch.object(ozon_delivery, "is_enabled", return_value=True), \
             patch.object(ozon_delivery, "_post", side_effect=fake_post):
            points = ozon_delivery.list_delivery_points(force=True)
        self.assertEqual(len(points), 20100)
        self.assertEqual(points[-1]["delivery_point_id"], 20099)


class PointInfoRetryTests(unittest.TestCase):
    def test_retries_batch_without_missing_ids(self):
        calls = []

        def fake_post(path, body, timeout=25, idempotency_key=None):
            ids = body.get("delivery_point_ids") or []
            calls.append(ids)
            if 140846 in ids:
                return 404, {"message": "Не найдены пункты выдачи: 140846."}
            return 200, {"delivery_points": [
                {
                    "delivery_point_id": i,
                    "full_address": f"addr {i}",
                    "coordinates": {"latitude": 59.6, "longitude": 60.5},
                    "is_active": True,
                    "shipment_method_ids": [1],
                }
                for i in ids
            ]}

        with patch.object(ozon_delivery, "_post", side_effect=fake_post):
            rows = ozon_delivery._point_info([1408406, 140846, 1408398])
        ids = {r["id"] for r in rows}
        self.assertEqual(ids, {1408406, 1408398})
        self.assertNotIn(140846, ids)
        self.assertGreaterEqual(len(calls), 2)


class ExpandNearbyTests(unittest.TestCase):
    def test_adds_neighbor_id_missing_from_list(self):
        listed = [{
            "id": 1408398,
            "lat": 59.59338,
            "lng": 60.520314,
            "active": True,
            "address": "Орджоникидзе 53а",
            "shipment_method_ids": [9],
        }]
        extra = [{
            "id": 1408406,
            "lat": 59.603052,
            "lng": 60.531347,
            "active": True,
            "address": "Циолковского 27",
            "shipment_method_ids": [],
        }]
        with patch.object(ozon_delivery, "_point_info", return_value=extra):
            merged = ozon_delivery._expand_points_near(listed, 59.6031158, 60.5313791)
        dest = ozon_delivery._nearest(merged, 59.6031158, 60.5313791)
        self.assertEqual(dest["id"], 1408406)
        self.assertEqual(dest["shipment_method_ids"], [9])


class CityRankTests(unittest.TestCase):
    def test_same_city_beats_closer_other_settlement(self):
        points = [
            {
                "id": 1, "lat": 44.87, "lng": 40.18, "active": True,
                "address": "Россия, Адыгея, Гиагинский Район, Новый, Советский переулок, 3",
            },
            {
                "id": 2, "lat": 44.86, "lng": 40.07, "active": True,
                "address": "Россия, Адыгея, станица Гиагинская, Советская улица, 26",
            },
            {
                "id": 3, "lat": 44.859, "lng": 40.078, "active": True,
                "address": "Россия, Адыгея, станица Гиагинская, улица Ленина, 82",
            },
        ]
        ranked = ozon_delivery._rank_local_points(points, 44.872625, 40.064053, city="Гиагинская")
        self.assertIn(ranked[0]["id"], (2, 3))
        self.assertTrue(all(p["id"] != 1 for p in ranked[:2]))
        self.assertLess(ranked[0]["distance_km"], 3)

    def test_without_city_picks_geographic_nearest(self):
        points = [
            {"id": 1, "lat": 55.83, "lng": 49.09, "active": True, "address": "far"},
            {"id": 2, "lat": 55.831, "lng": 49.091, "active": True, "address": "near"},
        ]
        ranked = ozon_delivery._rank_local_points(points, 55.8311, 49.0911, city="")
        self.assertEqual(ranked[0]["id"], 2)

    def test_city_key_strips_stanitsa(self):
        self.assertEqual(ozon_delivery._city_key("ст-ца Гиагинская"), "гиагинская")
        self.assertTrue(ozon_delivery._address_has_city(
            "Россия, Адыгея, станица Гиагинская, Советская улица, 26", "гиагинская"
        ))
        self.assertFalse(ozon_delivery._address_has_city(
            "Россия, Адыгея, Гиагинский Район, Новый, Советский переулок, 3", "гиагинская"
        ))


class ExpandFromMapTests(unittest.TestCase):
    def test_adds_closer_map_point_missing_from_list(self):
        listed = [{
            "id": 2630864,
            "lat": 55.74071,
            "lng": 37.684518,
            "active": True,
            "address": "Россия, Москва, Новорогожская улица, 8",
            "shipment_method_ids": [9],
        }]
        mmap = [
            {"id": 218464, "lat": 55.74178, "lng": 37.682896},
            {"id": 2630864, "lat": 55.74071, "lng": 37.684518},
        ]
        extra = [{
            "id": 218464,
            "lat": 55.74178,
            "lng": 37.682896,
            "active": True,
            "address": "Россия, Москва, Новорогожская улица, 15",
            "shipment_method_ids": [],
        }]
        with patch.object(ozon_delivery, "_seller_map_points", return_value=mmap), \
             patch.object(ozon_delivery, "_point_info", return_value=extra):
            merged = ozon_delivery._expand_from_map(listed, 55.741166, 37.682278)
        dest = ozon_delivery._nearest(merged, 55.741166, 37.682278)
        self.assertEqual(dest["id"], 218464)
        self.assertEqual(dest["shipment_method_ids"], [9])

    def test_giaginskaya_map_beats_other_settlement(self):
        listed = [{
            "id": 520635,
            "lat": 44.79,
            "lng": 40.13,
            "active": True,
            "address": "Россия, Адыгея, Гиагинский Район, Новый, Советский переулок, 3",
            "shipment_method_ids": [9],
        }]
        extra = [{
            "id": 465330,
            "lat": 44.871158,
            "lng": 40.060267,
            "active": True,
            "address": "Россия, Республика Адыгея, станица Гиагинская, Красная улица, 337",
            "shipment_method_ids": [],
        }]
        mmap = [{"id": 465330, "lat": 44.871158, "lng": 40.060267}]
        with patch.object(ozon_delivery, "_seller_map_points", return_value=mmap), \
             patch.object(ozon_delivery, "_point_info", return_value=extra):
            merged = ozon_delivery._expand_from_map(listed, 44.872625, 40.064053)
        ranked = ozon_delivery._rank_local_points(merged, 44.872625, 40.064053, city="Гиагинская")
        self.assertEqual(ranked[0]["id"], 465330)
        self.assertLess(ranked[0]["distance_km"], 1)


class QuoteFallbackTests(unittest.TestCase):
    def test_skips_failed_quote_and_takes_next(self):
        points = [
            {"id": 10, "lat": 55.741, "lng": 37.682, "active": True, "address": "Москва, Новорогожская 15", "shipment_method_ids": [1]},
            {"id": 20, "lat": 55.745, "lng": 37.683, "active": True, "address": "Москва, Рабочая 13", "shipment_method_ids": [1]},
        ]

        def fake_quote(point, phone, lat, lon, **kwargs):
            if int(point["id"]) == 10:
                return None
            return {"cost": 100, "days": 2, "shipment_method_id": 1, "cutoff_at": "x"}

        with patch.object(ozon_delivery, "is_enabled", return_value=True), \
             patch.object(ozon_delivery, "check_client", return_value={"eligible": True, "phone": "+7999"}), \
             patch.object(ozon_delivery, "_enriched_points", return_value=points), \
             patch.object(ozon_delivery, "_expand_around_ranked", side_effect=lambda pts, ranked, lat, lon: pts), \
             patch.object(ozon_delivery, "quote_to_point", side_effect=fake_quote), \
             patch.object(ozon_delivery, "from_dropoff_point", return_value={"id": 1, "address": "Казань"}):
            row = ozon_delivery.get_candidate(55.741166, 37.682278, "+7999", city="Москва")
        self.assertTrue(row["available"])
        self.assertEqual(row["pvz_code"], "20")

    def test_does_not_offer_other_town_when_city_has_pvz(self):
        points = [
            {
                "id": 10, "lat": 50.6286, "lng": 38.7002, "active": True,
                "address": "Россия, Белгородская, Алексеевка, Республиканская 71а",
                "shipment_method_ids": [1],
            },
            {
                "id": 20, "lat": 50.75, "lng": 38.90, "active": True,
                "address": "Россия, Воронежская Область, Нижний Ольшан, Почтовая 30",
                "shipment_method_ids": [1],
            },
        ]

        def fake_quote(point, phone, lat, lon, **kwargs):
            if int(point["id"]) == 20:
                return {"cost": 99, "days": 8, "shipment_method_id": 1, "cutoff_at": "x"}
            return None

        with patch.object(ozon_delivery, "is_enabled", return_value=True), \
             patch.object(ozon_delivery, "check_client", return_value={"eligible": True, "phone": "+7999"}), \
             patch.object(ozon_delivery, "_enriched_points", return_value=points), \
             patch.object(ozon_delivery, "_expand_around_ranked", side_effect=lambda pts, ranked, lat, lon: pts), \
             patch.object(ozon_delivery, "quote_to_point", side_effect=fake_quote), \
             patch.object(ozon_delivery, "from_dropoff_point", return_value={"id": 1, "address": "Казань"}):
            row = ozon_delivery.get_candidate(50.628595, 38.700242, "+7999", city="Алексеевка")
        self.assertFalse(row["available"])
        self.assertEqual(row["pvz_code"], "10")


class StatusMapTests(unittest.TestCase):
    def test_acceptance_at_dropoff_is_shipped(self):
        self.assertEqual(ozon_delivery.STATUS_MAP["accepted"], "shipped")

    def test_ready_for_pickup_and_delivered(self):
        self.assertEqual(ozon_delivery.STATUS_MAP["ready_for_pickup"], "ready_for_pickup")
        self.assertEqual(ozon_delivery.STATUS_MAP["delivered"], "delivered")

    def test_on_way_is_in_transit(self):
        self.assertEqual(ozon_delivery.map_posting_status("on_way"), "in_transit")
        self.assertEqual(ozon_delivery.map_posting_status("delivering"), "in_transit")

    def test_in_delivery_point_is_ready_not_in_transit(self):
        self.assertEqual(ozon_delivery.map_posting_status("in_delivery_point"), "ready_for_pickup")

    def test_waiting_dropoff_stays_unmapped(self):
        self.assertIsNone(ozon_delivery.map_posting_status("ready_for_shipping"))
        self.assertIsNone(ozon_delivery.map_posting_status("created"))
        self.assertIsNone(ozon_delivery.map_posting_status("forming"))

    def test_created_needs_approve(self):
        self.assertTrue(ozon_delivery.needs_posting_approve("created"))
        self.assertFalse(ozon_delivery.needs_posting_approve("ready_for_shipping"))
        self.assertFalse(ozon_delivery.needs_posting_approve("on_way"))


class DeclaredValueTests(unittest.TestCase):
    def test_declared_is_100(self):
        self.assertEqual(ozon_delivery.DECLARED_RUB, "100")

    def test_cheap_shop_order_is_floored_to_100(self):
        self.assertEqual(ozon_delivery._declared_amount(10), "100")
        self.assertEqual(ozon_delivery._declared_amount(1), "100")
        self.assertEqual(ozon_delivery._declared_amount(None), "100")

    def test_real_item_price_is_kept(self):
        self.assertEqual(ozon_delivery._declared_amount(2199), "2199")


class ErrTextTests(unittest.TestCase):
    def test_nested_checkout_error(self):
        text = ozon_delivery._err_text({
            "results": [{"error": {"code": "delivery_calculation_error", "message": "Ошибка предрасчета стоимости доставки"}}],
        })
        self.assertIn("предрасчета", text)


class PhoneNormalizeTests(unittest.TestCase):
    def test_russian_variants(self):
        self.assertEqual(ozon_delivery.normalize_phone("8 (903) 123-45-67"), "+79031234567")
        self.assertIsNone(ozon_delivery.normalize_phone("123"))


class LabelBarcodeTests(unittest.TestCase):
    def test_prefers_posting_over_order_number(self):
        self.assertEqual(
            ozon_delivery.resolve_label_barcode(
                req_id=404,
                order_number="EB-B10750B97E39-9YBP",
                posting_number="BP4P7P42Y131-11PP",
            ),
            "BP4P7P42Y131-11PP",
        )

    def test_looks_up_posting_when_missing(self):
        found = {"posting_number": "E123P552145P-0YBB", "order_number": "EE-5047BB925214-2B0P"}
        with patch.object(ozon_delivery, "find_posting", return_value=found):
            self.assertEqual(
                ozon_delivery.resolve_label_barcode(
                    req_id=401,
                    order_number="EE-5047BB925214-2B0P",
                    posting_number="",
                ),
                "E123P552145P-0YBB",
            )

    def test_falls_back_to_order_number(self):
        with patch.object(ozon_delivery, "find_posting", return_value=None):
            self.assertEqual(
                ozon_delivery.resolve_label_barcode(
                    req_id=1, order_number="EB-XXX", posting_number=""
                ),
                "EB-XXX",
            )


class FindPostingTests(unittest.TestCase):
    def test_matches_external_id(self):
        rows = [
            {"posting_number": "P1", "order_number": "O1", "order_external_id": "cx-10"},
            {"posting_number": "P2", "order_number": "O2", "order_external_id": "cx-401"},
        ]
        with patch.object(ozon_delivery, "list_postings", return_value=rows), \
             patch.object(ozon_delivery, "_post", return_value=(200, {"postings": []})):
            found = ozon_delivery.find_posting(req_id=401)
        self.assertEqual(found["posting_number"], "P2")

    def test_prefers_live_recreate_over_canceled_original(self):
        rows = [
            {"posting_number": "OLD", "order_number": "O1", "order_external_id": "cx-425",
             "status": "canceled", "created_at": "2026-09-11T07:05:51Z"},
            {"posting_number": "NEW", "order_number": "O2", "order_external_id": "cx-425-PPP677",
             "status": "ready_for_shipping", "created_at": "2026-09-24T09:00:00Z"},
        ]
        with patch.object(ozon_delivery, "list_postings", return_value=rows), \
             patch.object(ozon_delivery, "_post", return_value=(200, {"postings": [
                 {"posting_number": "OLD", "status": "canceled"}
             ]})):
            found = ozon_delivery.find_posting(req_id=425, posting_number="OLD")
        self.assertEqual(found["posting_number"], "NEW")


class DropoffBarcodeTests(unittest.TestCase):
    def test_uses_shipment_method_id(self):
        src = {"id": 1224090, "name": "Казань", "address": "Казань, улица Глазунова, 10"}
        with patch.object(ozon_delivery, "SHIPMENT_METHOD_ID", "1020005029438500"), \
             patch.object(ozon_delivery, "from_dropoff_point", return_value=src), \
             patch.dict("os.environ", {"OZON_DELIVERY_DROPOFF_BARCODE": ""}, clear=False):
            info = ozon_delivery.dropoff_barcode_info()
        self.assertEqual(info["barcode"], "1020005029438500")
        self.assertEqual(info["from_pvz_id"], "1224090")
        self.assertIn("Глазунова", info["address"])

    def test_falls_back_to_default_method(self):
        with patch.object(ozon_delivery, "SHIPMENT_METHOD_ID", ""), \
             patch.object(ozon_delivery, "_shipment_method_id", return_value=None), \
             patch.object(ozon_delivery, "from_dropoff_point", return_value=None), \
             patch.dict("os.environ", {"OZON_DELIVERY_DROPOFF_BARCODE": ""}, clear=False):
            info = ozon_delivery.dropoff_barcode_info()
        self.assertEqual(info["barcode"], ozon_delivery.DEFAULT_DROPOFF_BARCODE)


class FetchLabelPdfTests(unittest.TestCase):
    def test_returns_pdf_bytes(self):
        class Resp:
            content = b"%PDF-1.4 official-qr"

            def json(self):
                raise ValueError("not json")

        with patch.object(ozon_delivery, "_token_fresh", return_value="t"), \
             patch.object(ozon_delivery._session, "post", return_value=Resp()):
            raw = ozon_delivery.fetch_label_pdf("P-1")
        self.assertTrue(raw.startswith(b"%PDF"))
        self.assertIn(b"official-qr", raw)


class ApprovePostingTests(unittest.TestCase):
    def test_approve_sends_posting_number(self):
        calls = []

        def fake_post(path, body, timeout=25, idempotency_key=None):
            calls.append((path, body))
            return 200, {}

        with patch.object(ozon_delivery, "_post", side_effect=fake_post):
            result = ozon_delivery.approve_posting("BP4P7P42Y131-11PP")
        self.assertTrue(result["ok"])
        self.assertEqual(calls[0][0], "/v1/posting/approve")
        self.assertEqual(calls[0][1], {"posting_number": "BP4P7P42Y131-11PP"})

    def test_create_order_confirms_posting(self):
        def fake_post(path, body, timeout=25, idempotency_key=None):
            if path == "/v1/order/create":
                return 200, {"order_number": "EB-1", "postings": [{"posting_number": "P-1"}]}
            return 200, {}

        with patch.object(ozon_delivery, "is_enabled", return_value=True), \
             patch.object(ozon_delivery, "check_client") as check, \
             patch.object(ozon_delivery, "_post", side_effect=fake_post), \
             patch.object(ozon_delivery, "approve_posting", return_value={"ok": True}) as approve, \
             patch.object(ozon_delivery, "ensure_ready_for_shipping", return_value="ready_for_shipping"):
            result = ozon_delivery.create_order(
                req_id=1,
                customer_name="Тест",
                customer_phone="+79031234567",
                destination_pvz_id="218464",
                shipment_method_id=9,
            )
        self.assertEqual(result["posting_number"], "P-1")
        approve.assert_called_once_with("P-1")
        check.assert_not_called()

    def test_create_order_uses_stable_external_id(self):
        captured = {}

        def fake_post(path, body, timeout=25, idempotency_key=None):
            if path == "/v1/order/create":
                captured["body"] = body
                captured["idempotency"] = idempotency_key
                return 200, {"order_number": "EB-1", "postings": [{"posting_number": "P-1"}]}
            if path == "/v1/posting/info":
                return 200, {"postings": [{"status": "created"}]}
            return 200, {}

        with patch.object(ozon_delivery, "is_enabled", return_value=True), \
             patch.object(ozon_delivery, "_post", side_effect=fake_post), \
             patch.object(ozon_delivery, "approve_posting", return_value={"ok": True}), \
             patch.object(ozon_delivery, "ensure_ready_for_shipping", return_value="ready_for_shipping"):
            ozon_delivery.create_order(
                req_id=425,
                customer_name="Тест",
                customer_phone="+79031234567",
                destination_pvz_id="218464",
                shipment_method_id=9,
            )
        self.assertEqual(captured["body"]["order_external_id"], "cx-425")
        self.assertEqual(captured["body"]["postings"][0]["posting_external_id"], "cx-425")
        self.assertEqual(captured["body"]["postings"][0]["declared_value"]["amount"], "100")

    def test_recreate_uses_new_external_id(self):
        captured = {}

        def fake_post(path, body, timeout=25, idempotency_key=None):
            if path == "/v1/order/create":
                captured["body"] = body
                captured["idempotency"] = idempotency_key
                return 200, {"order_number": "EB-2", "postings": [{"posting_number": "P-2"}]}
            if path == "/v1/posting/info":
                return 200, {"postings": [{"status": "created"}]}
            return 200, {}

        with patch.object(ozon_delivery, "is_enabled", return_value=True), \
             patch.object(ozon_delivery, "_post", side_effect=fake_post), \
             patch.object(ozon_delivery, "approve_posting", return_value={"ok": True}), \
             patch.object(ozon_delivery, "ensure_ready_for_shipping", return_value="ready_for_shipping"):
            result = ozon_delivery.create_order(
                req_id=425,
                customer_name="Тест",
                customer_phone="+79031234567",
                destination_pvz_id="218464",
                shipment_method_id=9,
                attempt_key="PP-P677Y557B820-P75P",
            )
        self.assertEqual(captured["body"]["order_external_id"], "cx-425-PPP677Y557B820P75P")
        self.assertNotEqual(captured["idempotency"], str(__import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL, "cx-ozon-425")))
        self.assertEqual(result["order_number"], "EB-2")

    def test_create_order_rejects_canceled_posting(self):
        def fake_post(path, body, timeout=25, idempotency_key=None):
            if path == "/v1/order/create":
                return 200, {"order_number": "OLD", "postings": [{"posting_number": "OLD-P"}]}
            if path == "/v1/posting/info":
                return 200, {"postings": [{"status": "canceled"}]}
            return 200, {}

        with patch.object(ozon_delivery, "is_enabled", return_value=True), \
             patch.object(ozon_delivery, "_post", side_effect=fake_post), \
             patch.object(ozon_delivery, "approve_posting") as approve:
            with self.assertRaisesRegex(RuntimeError, "отменённый"):
                ozon_delivery.create_order(
                    req_id=425,
                    customer_name="Тест",
                    customer_phone="+79031234567",
                    destination_pvz_id="218464",
                    shipment_method_id=9,
                )
        approve.assert_not_called()

    def test_create_order_uses_dropoff_method_when_dest_has_none(self):
        captured = {}

        def fake_post(path, body, timeout=25, idempotency_key=None):
            if path == "/v1/order/create":
                captured["method"] = body["postings"][0]["shipment_method_id"]
                return 200, {"order_number": "EB-3", "postings": [{"posting_number": "P-3"}]}
            return 200, {}

        with patch.object(ozon_delivery, "is_enabled", return_value=True), \
             patch.object(ozon_delivery, "SHIPMENT_METHOD_ID", ""), \
             patch.object(ozon_delivery, "from_dropoff_point", return_value={"id": 1224090, "shipment_method_ids": [1020005029438500]}), \
             patch.object(ozon_delivery, "_post", side_effect=fake_post), \
             patch.object(ozon_delivery, "approve_posting", return_value={"ok": True}), \
             patch.object(ozon_delivery, "ensure_ready_for_shipping", return_value="ready_for_shipping"):
            ozon_delivery.create_order(
                req_id=11,
                customer_name="Тест",
                customer_phone="+79031234567",
                destination_pvz_id="218464",
            )
        self.assertEqual(captured["method"], 1020005029438500)

    def test_create_order_falls_back_to_glazunova_dropoff_barcode(self):
        captured = {}

        def fake_post(path, body, timeout=25, idempotency_key=None):
            if path == "/v1/order/create":
                captured["method"] = body["postings"][0]["shipment_method_id"]
                return 200, {"order_number": "EB-4", "postings": [{"posting_number": "P-4"}]}
            return 200, {}

        with patch.object(ozon_delivery, "is_enabled", return_value=True), \
             patch.object(ozon_delivery, "SHIPMENT_METHOD_ID", ""), \
             patch.object(ozon_delivery, "from_dropoff_point", return_value={"id": 1224090, "shipment_method_ids": []}), \
             patch.object(ozon_delivery, "_post", side_effect=fake_post), \
             patch.object(ozon_delivery, "approve_posting", return_value={"ok": True}), \
             patch.object(ozon_delivery, "ensure_ready_for_shipping", return_value="ready_for_shipping"):
            ozon_delivery.create_order(
                req_id=12,
                customer_name="Тест",
                customer_phone="+79031234567",
                destination_pvz_id="218464",
            )
        self.assertEqual(captured["method"], int(ozon_delivery.DEFAULT_DROPOFF_BARCODE))

    def test_create_order_uses_declared_cost_and_description(self):
        captured = {}

        def fake_post(path, body, timeout=25, idempotency_key=None):
            if path == "/v1/order/create":
                captured["body"] = body
                return 200, {"order_number": "EB-5", "postings": [{"posting_number": "P-5"}]}
            return 200, {}

        with patch.object(ozon_delivery, "is_enabled", return_value=True), \
             patch.object(ozon_delivery, "_post", side_effect=fake_post), \
             patch.object(ozon_delivery, "approve_posting", return_value={"ok": True}), \
             patch.object(ozon_delivery, "ensure_ready_for_shipping", return_value="ready_for_shipping"):
            ozon_delivery.create_order(
                req_id=15,
                customer_name="Рустам Мур",
                customer_phone="+79031234567",
                destination_pvz_id="218464",
                shipment_method_id=9,
                items_description="Заказ сайта #15, Рустам Мур, VV-BX-244 Вешалка для ванной",
                declared_cost=2199,
                weight_g=1500,
                length_mm=400,
                width_mm=300,
                height_mm=200,
            )
        posting = captured["body"]["postings"][0]
        self.assertEqual(posting["declared_value"]["amount"], "2199")
        self.assertIn("Заказ сайта #15", posting["description"])
        self.assertEqual(captured["body"]["recipient"]["full_name"], "Рустам Мур")
        self.assertEqual(posting["dimensions"]["weight_g"], 1500)
        self.assertEqual(posting["dimensions"]["length_mm"], 400)

    def test_create_order_floors_cheap_declared_value(self):
        captured = {}

        def fake_post(path, body, timeout=25, idempotency_key=None):
            if path == "/v1/order/create":
                captured["amount"] = body["postings"][0]["declared_value"]["amount"]
                return 200, {"order_number": "EB-6", "postings": [{"posting_number": "P-6"}]}
            return 200, {}

        with patch.object(ozon_delivery, "is_enabled", return_value=True), \
             patch.object(ozon_delivery, "_post", side_effect=fake_post), \
             patch.object(ozon_delivery, "approve_posting", return_value={"ok": True}), \
             patch.object(ozon_delivery, "ensure_ready_for_shipping", return_value="ready_for_shipping"):
            ozon_delivery.create_order(
                req_id=16,
                customer_name="Тест",
                customer_phone="+79031234567",
                destination_pvz_id="470418",
                shipment_method_id=9,
                declared_cost=10,
            )
        self.assertEqual(captured["amount"], "100")


    def test_create_order_recovers_when_create_times_out(self):
        with patch.object(ozon_delivery, "is_enabled", return_value=True), \
             patch.object(ozon_delivery, "_post", side_effect=TimeoutError("read timeout")), \
             patch.object(ozon_delivery, "find_posting", return_value={
                 "order_number": "YE-TIMEOUT",
                 "posting_number": "P-TIMEOUT",
                 "status": "created",
             }), \
             patch.object(ozon_delivery, "approve_posting", return_value={"ok": True}):
            result = ozon_delivery.create_order(
                req_id=603,
                customer_name="Тест",
                customer_phone="+79031234567",
                destination_pvz_id="340817",
                shipment_method_id=9,
            )
        self.assertEqual(result["order_number"], "YE-TIMEOUT")
        self.assertEqual(result["posting_number"], "P-TIMEOUT")


class RecreateShipmentSourceTests(unittest.TestCase):
    def test_ozon_recreate_passes_old_track_as_attempt_key(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[2] / "backend/routers/reshipment.py").read_text()
        recreate = src.split("def recreate_shipment(", 1)[1].split("\n@router.", 1)[0]
        self.assertIn("attempt_key=old_track", recreate)
        self.assertIn("new_track == old_track", recreate)


if __name__ == "__main__":
    unittest.main()
