import unittest
from unittest.mock import Mock, patch

from backend import ym_product_card


class YmProductCardTests(unittest.TestCase):
    def test_uses_only_exact_offer_and_selected_real_fields(self):
        data = {
            "result": {
                "offerMappings": [
                    {"offer": {"offerId": "other", "name": "Чужой товар"}},
                    {"offer": {
                        "offerId": "2-СБ-Black",
                        "name": "Сушилка",
                        "weightDimensions": {"length": 120, "width": 50, "height": 10, "weight": 4.2},
                        "params": [
                            {"name": "Цвет", "value": "чёрный"},
                            {"name": "Материал", "values": ["сталь", "пластик"]},
                            {"name": "Конструкция", "value": "складная"},
                            {"name": "Пустой", "value": ""},
                        ],
                    }},
                ]
            }
        }

        card = ym_product_card.parse_offer_card(data, "2-СБ-Black")

        self.assertEqual(card["status"], "ok")
        self.assertEqual(card["product"]["offer_id"], "2-СБ-Black")
        self.assertEqual(card["product"]["dimensions"]["weight"], 4.2)
        self.assertEqual(card["product"]["params"], [
            {"name": "Цвет", "value": "чёрный"},
            {"name": "Материал", "value": "сталь, пластик"},
            {"name": "Конструкция", "value": "складная"},
        ])
        self.assertNotIn("manuals", card["product"])

    def test_missing_exact_offer_is_not_substituted(self):
        card = ym_product_card.parse_offer_card({
            "result": {"offerMappings": [{"offer": {"offerId": "similar", "name": "Похожий"}}]}
        }, "wanted")
        self.assertEqual(card["status"], "not_found")
        self.assertNotIn("product", card)

    def test_unavailable_api_is_a_safe_state_and_is_not_cached(self):
        db = Mock()
        db.execute.return_value.mappings.return_value.first.return_value = None
        with patch.object(ym_product_card.ym_client, "post", side_effect=RuntimeError("offline")):
            card = ym_product_card.get_product_card(db, "2-СБ-Black")
        self.assertEqual(card["status"], "unavailable")
        db.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
