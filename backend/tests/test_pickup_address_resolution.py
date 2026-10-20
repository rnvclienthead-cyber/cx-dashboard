import os
import unittest
from unittest.mock import Mock, patch

from backend import pickup_resolver


class PickupAddressResolutionTests(unittest.TestCase):
    def test_dadata_normalizes_address_on_backend(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "suggestions": [{
                "value": "г Новосибирск, ул Каменская",
                "data": {
                    "city": "Новосибирск",
                    "street": "Каменская",
                    "geo_lat": "55.0411",
                    "geo_lon": "82.9344",
                    "granularity": "street",
                },
            }],
        }

        with patch.dict(os.environ, {"DADATA_API_KEY": "server-secret"}, clear=False):
            with patch("requests.post", return_value=response) as post:
                resolved = pickup_resolver.resolve_address("Новосибирск Каменская")

        self.assertEqual(resolved["city"], "Новосибирск")
        self.assertEqual(resolved["address"], "г Новосибирск, ул Каменская")
        self.assertEqual(resolved["lat"], 55.0411)
        self.assertEqual(resolved["lon"], 82.9344)
        self.assertFalse(resolved["address_imprecise"])
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Token server-secret")
        self.assertEqual(post.call_args.kwargs["json"]["count"], 5)
        self.assertEqual(
            post.call_args.kwargs["json"]["locations"],
            [{"country_iso_code": "RU"}],
        )

    def test_skips_region_without_city(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "suggestions": [
                {"value": "Иркутская обл", "data": {"region": "Иркутская", "geo_lat": None}},
                {
                    "value": "г Иркутск",
                    "data": {
                        "city": "Иркутск",
                        "geo_lat": "52.28",
                        "geo_lon": "104.28",
                    },
                },
            ],
        }
        with patch.dict(os.environ, {"DADATA_API_KEY": "server-secret"}, clear=False):
            with patch("requests.post", return_value=response):
                resolved = pickup_resolver.resolve_address("Иркутская")
        self.assertEqual(resolved["city"], "Иркутск")
        self.assertEqual(resolved["address"], "г Иркутск")

    def test_suggest_addresses_marks_selectable(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "suggestions": [
                {"value": "Иркутская обл", "data": {"region": "Иркутская"}},
                {
                    "value": "г Иркутск",
                    "data": {"city": "Иркутск", "geo_lat": "52.28", "geo_lon": "104.28"},
                },
            ],
        }
        with patch.dict(os.environ, {"DADATA_API_KEY": "server-secret"}, clear=False):
            with patch("requests.post", return_value=response):
                rows = pickup_resolver.suggest_addresses("Иркутск")
        self.assertEqual(rows[0]["selectable"], False)
        self.assertEqual(rows[1]["selectable"], False)
        self.assertEqual(rows[1]["city"], "Иркутск")
        self.assertTrue(rows[1]["address_imprecise"])

    def test_foreign_address_hint_blocks_kazakhstan(self):
        self.assertTrue(pickup_resolver.is_foreign_address_hint("Караганда Поселок Молодежный"))
        self.assertTrue(pickup_resolver.is_foreign_address_hint("Алматы", "ул. Абая"))
        self.assertFalse(pickup_resolver.is_foreign_address_hint("Москва, Тверская 7"))
        self.assertFalse(pickup_resolver.is_foreign_address_hint("Казань, Четаева 14"))

    def test_city_only_counts_as_missing_street(self):
        self.assertTrue(pickup_resolver.address_missing_street(
            {"address_imprecise": True, "city": "Уссурийск"},
            address="Приморский край, г Уссурийск",
            city="Уссурийск",
        ))
        self.assertFalse(pickup_resolver.address_missing_street(
            {"address_imprecise": False, "city": "Москва"},
            address="Москва, Тверская улица, 7",
            city="Москва",
        ))

    def test_suggest_skips_foreign_query_without_api(self):
        with patch.dict(os.environ, {"DADATA_API_KEY": "server-secret"}, clear=False):
            with patch("requests.post") as post:
                rows = pickup_resolver.suggest_addresses("Караганда")
        self.assertEqual(rows, [])
        post.assert_not_called()

    def test_resolve_rejects_foreign_without_api(self):
        with patch.dict(os.environ, {"DADATA_API_KEY": "server-secret"}, clear=False):
            with patch("requests.post") as post:
                with patch("requests.get") as get:
                    self.assertIsNone(pickup_resolver.resolve_address("Караганда Казахстан"))
        post.assert_not_called()
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
