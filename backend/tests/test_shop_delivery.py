import os
import unittest
from unittest.mock import patch

from backend import cdek, shop_delivery
from backend.access import classify_path, PUBLIC


class CdekPackagesTests(unittest.TestCase):
    def test_light_parcel_keeps_env_box(self):
        pkg = cdek._packages(100)[0]
        self.assertEqual(pkg["weight"], 100)
        self.assertEqual(pkg["length"], cdek.PACKAGE_LENGTH)

    def test_shop_weight_uses_furniture_box(self):
        pkg = cdek._packages(2000)[0]
        self.assertEqual(pkg["weight"], 2000)
        self.assertEqual(pkg["length"], 40)
        self.assertEqual(pkg["height"], 20)


class ShopPackagesTests(unittest.TestCase):
    def test_parse_packages_sums_two_items(self):
        pkgs = shop_delivery.parse_packages([
            {"weight_g": 1500, "length_cm": 80, "width_cm": 10, "height_cm": 10},
            {"weight_g": 900, "length_cm": 40, "width_cm": 30, "height_cm": 20},
        ])
        self.assertEqual(shop_delivery.packages_weight_g(pkgs, 2000), 2400)
        outer = shop_delivery.packages_outer_mm(pkgs, 2000)
        self.assertEqual(outer["length_mm"], 800)
        self.assertEqual(outer["width_mm"], 300)
        self.assertEqual(outer["height_mm"], 300)

    def test_parse_packages_json_and_fallback_box(self):
        pkgs = shop_delivery.parse_packages('[{"weight_g": 0, "length_cm": 0, "width_cm": 0, "height_cm": 0}]')
        self.assertEqual(pkgs[0]["weight_g"], 2000)
        self.assertEqual(pkgs[0]["length_cm"], 40)


class ShopDeliveryQuoteTests(unittest.TestCase):
    def test_quote_path_is_public_token_checked_inside(self):
        self.assertEqual(classify_path("/api/v1/shop/delivery/quote")[0], PUBLIC)

    def test_token_rejects_empty_and_mismatch(self):
        with patch.object(shop_delivery, "SHOP_TOKEN", "secret-token"):
            self.assertFalse(shop_delivery.token_ok(""))
            self.assertFalse(shop_delivery.token_ok("other"))
            self.assertTrue(shop_delivery.token_ok("secret-token"))

    def test_public_row_requires_cost(self):
        self.assertIsNone(shop_delivery.public_row("cdek", None))
        self.assertIsNone(shop_delivery.public_row("cdek", {"pvz_code": "KZN1"}))
        row = shop_delivery.public_row("cdek", {
            "cost": 390,
            "pvz_code": "KZN1",
            "pvz_address": "Казань, Тульская 40",
            "pvz_name": "ПВЗ",
        })
        self.assertEqual(row["carrier"], "cdek")
        self.assertEqual(row["cost"], 390.0)
        self.assertEqual(row["pvz_code"], "KZN1")

    def test_quote_both_passes_weight_and_geocodes(self):
        with patch.object(shop_delivery, "resolve_address", return_value={
            "city": "Казань",
            "address": "Казань, Кремлёвская 1",
            "lat": 55.79,
            "lon": 49.12,
        }):
            with patch.object(shop_delivery, "get_both_candidates", return_value={
                "cdek": {"cost": 410, "pvz_code": "KZN59"},
                "yandex": {"cost": 380, "pvz_code": "yd-1"},
            }) as both:
                out = shop_delivery.quote_both("казань", "кремлевская 1", weight_g=2500)
        self.assertEqual(out["cdek"]["cost"], 410)
        both.assert_called_once()
        kwargs = both.call_args.kwargs
        self.assertEqual(kwargs["weight_g"], 2500)
        self.assertEqual(kwargs["city"], "Казань")
        self.assertEqual(kwargs["lat"], 55.79)

    def test_list_points_asks_for_street(self):
        with patch.object(shop_delivery, "resolve_address", return_value={
            "city": "Казань",
            "address": "Казань",
            "lat": 55.79,
            "lon": 49.12,
            "address_imprecise": True,
        }):
            out = shop_delivery.list_points("cdek", "Казань")
        self.assertEqual(out["status"], "need_street")

    def test_list_points_returns_sorted_cdek(self):
        with patch.object(shop_delivery, "resolve_address", return_value={
            "city": "Казань",
            "address": "Казань, Тульская 40",
            "lat": 55.79,
            "lon": 49.12,
            "address_imprecise": False,
        }):
            with patch.object(shop_delivery, "list_cdek_points", return_value=[{
                "pvz_code": "KZN1",
                "pvz_name": "ПВЗ",
                "pvz_address": "Тульская 1",
                "lat": 55.80,
                "lng": 49.13,
                "distance_km": 1.2,
                "city_code": 424,
                "work_time": "10-20",
            }]) as listed:
                with patch.object(shop_delivery.cdek, "calculate_cost_by_city_code", return_value=317):
                    out = shop_delivery.list_points("cdek", "Казань", "Тульская 40")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["points"][0]["code"], "KZN1")
        self.assertEqual(out["points"][0]["cost"], 317.0)
        self.assertEqual(out["points"][0]["distance_km"], 1.2)
        listed.assert_called_once()

    def test_postamats_are_skipped(self):
        self.assertTrue(shop_delivery.is_postamat({"pvz_name": "Ozon Box", "point_type": ""}))
        self.assertTrue(shop_delivery.is_postamat({"point_type": "POSTAMAT"}))
        self.assertTrue(shop_delivery.is_postamat({"pvz_name": "Постамат на Ленина"}))
        self.assertTrue(shop_delivery.is_postamat({"pvz_name": "Халва", "point_type": "pvz"}))
        self.assertFalse(shop_delivery.is_postamat({"pvz_name": "ПВЗ Ozon", "pvz_address": "Ленина 1"}))
        kept = shop_delivery.without_postamats([
            {"pvz_code": "1", "pvz_name": "Постамат", "pvz_address": "A"},
            {"pvz_code": "2", "pvz_name": "ПВЗ", "pvz_address": "B"},
        ], limit=20)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["pvz_code"], "2")

    def test_list_points_ozon_asks_for_account(self):
        with patch.object(shop_delivery, "resolve_address", return_value={
            "city": "Казань",
            "address": "Казань, Тульская 40",
            "lat": 55.79,
            "lon": 49.12,
            "address_imprecise": False,
        }):
            with patch.object(shop_delivery.ozon_delivery, "is_enabled", return_value=True):
                with patch.object(shop_delivery.ozon_delivery, "check_client", return_value={
                    "eligible": False,
                    "reason": "На этот номер нет аккаунта Ozon",
                }):
                    out = shop_delivery.list_points("ozon", "Казань", "Тульская 40", phone="+79999999999")
        self.assertEqual(out["status"], "need_ozon_account")
        self.assertIn("профиля Ozon", out["message"])

    def test_list_points_ozon_returns_nearby(self):
        with patch.object(shop_delivery, "resolve_address", return_value={
            "city": "Казань",
            "address": "Казань, Тульская 40",
            "lat": 55.79,
            "lon": 49.12,
            "address_imprecise": False,
        }):
            with patch.object(shop_delivery.ozon_delivery, "is_enabled", return_value=True):
                with patch.object(shop_delivery.ozon_delivery, "check_client", return_value={
                    "eligible": True,
                    "phone": "+79991234567",
                }):
                    with patch.object(shop_delivery, "list_ozon_points", return_value=[{
                        "pvz_code": "1001",
                        "id": 1001,
                        "pvz_name": "Ozon ПВЗ",
                        "pvz_address": "Казань, Ленина 1",
                        "lat": 55.80,
                        "lng": 49.13,
                        "distance_km": 1.1,
                        "shipment_method_ids": [5],
                    }]) as listed:
                        with patch.object(shop_delivery.ozon_delivery, "quote_to_point", return_value={"cost": 249.0}):
                            out = shop_delivery.list_points(
                                "ozon", "Казань", "Тульская 40", phone="+79991234567",
                            )
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["points"][0]["code"], "1001")
        self.assertEqual(out["points"][0]["cost"], 249.0)
        listed.assert_called_once()

    def test_quote_point_uses_selected_pvz(self):
        with patch.object(shop_delivery, "list_points", return_value={
            "status": "ok",
            "points": [{
                "code": "yd-9",
                "name": "ПВЗ",
                "address": "Ленина 1",
                "lat": 55.8,
                "lng": 49.1,
                "distance_km": 0.4,
                "cost": 158,
            }],
        }):
            with patch.object(shop_delivery.yandex_ndd, "calculate_price", return_value=162.0):
                row = shop_delivery.quote_point("yandex", "yd-9", "Казань", "Ленина 1")
        self.assertEqual(row["pvz_code"], "yd-9")
        self.assertEqual(row["cost"], 162.0)

    def test_list_points_passes_product_packages_to_cdek(self):
        pkgs = [{"weight_g": 1800, "length_cm": 70, "width_cm": 12, "height_cm": 8}]
        with patch.object(shop_delivery, "resolve_address", return_value={
            "city": "Казань",
            "address": "Казань, Тульская 40",
            "lat": 55.79,
            "lon": 49.12,
            "address_imprecise": False,
        }):
            with patch.object(shop_delivery, "list_cdek_points", return_value=[{
                "pvz_code": "KZN1",
                "pvz_name": "ПВЗ",
                "pvz_address": "Казань",
                "lat": 55.8,
                "lng": 49.1,
                "city_code": 270,
                "distance_km": 0.4,
            }]):
                with patch.object(shop_delivery.cdek, "calculate_cost_by_city_code", return_value=410) as calc:
                    shop_delivery.list_points("cdek", "Казань", "Тульская 40", packages=pkgs)
        self.assertEqual(calc.call_count, 1)
        self.assertEqual(calc.call_args.kwargs.get("packages"), shop_delivery.parse_packages(pkgs))

    def test_cdek_sums_separate_item_shipments(self):
        pkgs = [
            {"weight_g": 1800, "length_cm": 70, "width_cm": 12, "height_cm": 8},
            {"weight_g": 900, "length_cm": 40, "width_cm": 30, "height_cm": 20},
        ]
        with patch.object(shop_delivery, "resolve_address", return_value={
            "city": "Казань",
            "address": "Казань, Тульская 40",
            "lat": 55.79,
            "lon": 49.12,
            "address_imprecise": False,
        }):
            with patch.object(shop_delivery, "list_cdek_points", return_value=[{
                "pvz_code": "KZN1",
                "pvz_name": "ПВЗ",
                "pvz_address": "Казань",
                "lat": 55.8,
                "lng": 49.1,
                "city_code": 270,
                "distance_km": 0.4,
            }]):
                with patch.object(
                    shop_delivery.cdek, "calculate_cost_by_city_code", side_effect=[180, 120],
                ) as calc:
                    out = shop_delivery.list_points("cdek", "Казань", "Тульская 40", packages=pkgs)
        self.assertEqual(calc.call_count, 2)
        self.assertEqual(out["points"][0]["cost"], 300.0)
        self.assertEqual(len(calc.call_args_list[0].kwargs.get("packages") or []), 1)
