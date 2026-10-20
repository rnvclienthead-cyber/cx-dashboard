import os
import unittest
from unittest.mock import patch, Mock

from fastapi import HTTPException

from backend.routers import cx_responses, reshipment


class WbPhotoPublicTests(unittest.TestCase):
    def test_wb_photo_is_on_public_router(self):
        public_paths = [getattr(r, "path", "") for r in cx_responses.public_router.routes]
        auth_paths = [getattr(r, "path", "") for r in cx_responses.router.routes]
        self.assertTrue(any(p.endswith("/wb-photo/{nm_id}") for p in public_paths))
        self.assertFalse(any(p.endswith("/wb-photo/{nm_id}") for p in auth_paths))


class WbCdnProbeTests(unittest.TestCase):
    def setUp(self):
        cx_responses._WB_VOL_HOST_CACHE.clear()

    def test_probe_uses_neighbor_host_when_guess_404s(self):
        def fake_get(url, timeout=6, stream=True):
            resp = Mock()
            resp.status_code = 200 if "basket-15." in url else 404
            resp.close = Mock()
            return resp

        with patch.object(cx_responses.requests, "get", side_effect=fake_get):
            url = cx_responses._probe_wb_cdn_photo(237307787)
        self.assertIn("basket-15.", url)
        self.assertEqual(cx_responses._WB_VOL_HOST_CACHE[2373], 15)

    def test_probe_finds_host_above_30(self):
        def fake_get(url, timeout=6, stream=True):
            resp = Mock()
            resp.status_code = 200 if "basket-44." in url else 404
            resp.close = Mock()
            return resp

        with patch.object(cx_responses.requests, "get", side_effect=fake_get):
            url = cx_responses._probe_wb_cdn_photo(1260803740)
        self.assertIn("basket-44.", url)
        self.assertEqual(cx_responses._WB_VOL_HOST_CACHE[12608], 44)


class MapsConfigTests(unittest.TestCase):
    def test_prefers_maps_js_key(self):
        env = {
            "YANDEX_MAPS_JS_KEY": "maps-key",
            "VITE_YANDEX_MAPS_KEY": "vite-key",
            "YANDEX_API_KEY": "llm-key",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(reshipment._maps_js_key(), "maps-key")

    def test_falls_back_to_yandex_api_key(self):
        with patch.dict(
            os.environ,
            {"YANDEX_MAPS_JS_KEY": "", "VITE_YANDEX_MAPS_KEY": "", "YANDEX_API_KEY": "llm-key"},
            clear=False,
        ):
            self.assertEqual(reshipment._maps_js_key(), "llm-key")

    def test_maps_config_returns_apikey(self):
        with patch.object(reshipment, "_maps_js_key", return_value="abc"):
            self.assertEqual(reshipment.get_maps_config(), {"apikey": "abc"})

    def test_maps_config_errors_without_key(self):
        with patch.object(reshipment, "_maps_js_key", return_value=""):
            with self.assertRaises(HTTPException) as caught:
                reshipment.get_maps_config()
            self.assertEqual(caught.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
