import unittest

from backend.wb_goods_return import parse_report, _windows


class ParseGoodsReturnTests(unittest.TestCase):
    def test_maps_sticker_and_srid(self):
        rows = parse_report({
            "report": [{
                "srid": "12.abc.3.0",
                "stickerId": "35548064307",
                "shkId": 4307524959,
                "status": "Выдан продавцу",
                "reason": "Брак",
                "dstOfficeAddress": "Казань Техническая Улица 17",
                "nmId": 1,
                "vendorCode": "Shelf-white-5/43",
            }]
        })
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sticker_id"], "35548064307")
        self.assertEqual(rows[0]["shk_id"], 4307524959)
        self.assertEqual(rows[0]["srid"], "12.abc.3.0")
        self.assertEqual(rows[0]["vendor_code"], "Shelf-white-5/43")

    def test_skips_rows_without_srid(self):
        self.assertEqual(parse_report({"report": [{"stickerId": "1"}]}), [])

    def test_windows_cover_range(self):
        windows = _windows(40)
        self.assertGreaterEqual(len(windows), 2)
        self.assertEqual(windows[0][0] <= windows[-1][1], True)

    def test_parse_stickers_barcode(self):
        from backend.wb_goods_return import parse_stickers
        rows = parse_stickers({
            "stickers": [{
                "orderId": 5381508279,
                "partA": "5636242",
                "partB": "1292",
                "barcode": "*DR92TCyF",
            }]
        })
        self.assertEqual(rows[0]["sticker_barcode"], "*DR92TCyF")
        self.assertEqual(rows[0]["sticker_id"], "56362421292")
        self.assertEqual(rows[0]["order_id"], 5381508279)


if __name__ == "__main__":
    unittest.main()
