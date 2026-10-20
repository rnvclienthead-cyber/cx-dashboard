import unittest

from backend import product_fasteners, ym_product_card
from unittest.mock import Mock, patch


class ProductFastenersTests(unittest.TestCase):
    def test_table_sizes_are_readable_for_staff(self):
        self.assertEqual(
            product_fasteners.fasteners_for("2-СБ-Black"),
            [{"code": "VINT-M6x30-SILV", "label": "М6×30, серебристый"}],
        )
        self.assertEqual(
            [item["label"] for item in product_fasteners.fasteners_for("BW-Black-1")],
            ["М6×20, серебристый", "М6×35, серебристый"],
        )
        self.assertEqual(
            [item["label"] for item in product_fasteners.fasteners_for("BW-White-1")],
            ["М6×20, серебристый", "М6×40, серебристый"],
        )
        self.assertEqual(
            product_fasteners.fasteners_for("BO-White -1")[0]["label"],
            "М6×35, серебристый",
        )
        self.assertEqual(
            product_fasteners.fasteners_for("Shelf-black-5/63")[0]["label"],
            "28×10,3 мм, М6, чёрный",
        )
        self.assertEqual(
            product_fasteners.fasteners_for("Flower-stand-white-5")[0]["label"],
            "М6×25, чёрный",
        )

    def test_old_2sb_batch_uses_same_screw(self):
        self.assertEqual(
            product_fasteners.lookup_codes("2-СБ-Grey Старый заказ"),
            ("VINT-M6x30-SILV",),
        )

    def test_unknown_article_has_no_screws(self):
        self.assertEqual(product_fasteners.fasteners_for("UNKNOWN-SKU"), [])


class YmProductCardFastenerAttachTests(unittest.TestCase):
    def test_unavailable_api_still_returns_warehouse_screws(self):
        db = Mock()
        db.execute.return_value.mappings.return_value.first.return_value = None
        with patch.object(ym_product_card.ym_client, "post", side_effect=RuntimeError("offline")):
            card = ym_product_card.get_product_card(db, "2-СБ-Black")
        self.assertEqual(card["status"], "unavailable")
        self.assertEqual(card["fasteners"][0]["label"], "М6×30, серебристый")
        db.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
