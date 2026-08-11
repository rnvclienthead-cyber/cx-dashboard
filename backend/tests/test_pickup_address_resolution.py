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


if __name__ == "__main__":
    unittest.main()
