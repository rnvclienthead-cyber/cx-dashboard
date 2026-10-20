import unittest

from backend.return_scan import lookup_scan, normalize_scan_code


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class ScriptedDb:
    def __init__(self):
        self.handlers = []
        self.calls = []

    def on(self, predicate, rows):
        self.handlers.append((predicate, rows))
        return self

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        self.calls.append((sql, params))
        for pred, rows in self.handlers:
            if pred(sql, params):
                return FakeResult(rows)
        return FakeResult([])

    def rollback(self):
        pass

    def commit(self):
        pass


WB_CLAIM = {
    "srid": "12.abc.3.0",
    "claim_id": "99001",
    "supplier_article": "GL-1",
    "user_comment": "Сломана ножка",
    "status": "Одобрено",
    "claim_type": "1",
    "photos": "/static/wb_claims/a.jpg|https://cdn.example/orig.jpg",
    "nm_id": "123",
    "claim_date": None,
    "income_id": "555111",
    "cat_1": True,
    "cat_4": True,
    "name_ru": "Стол",
    "product_photo": "/static/p.jpg",
}


def _has(sql, needle):
    return needle.lower() in sql.lower()


class NormalizeScanCodeTests(unittest.TestCase):
    def test_plain_value(self):
        self.assertEqual(normalize_scan_code("  12.abc.3.0  "), "12.abc.3.0")

    def test_url_last_path(self):
        self.assertEqual(
            normalize_scan_code("https://www.wildberries.ru/lk/returns/12.abc.3.0"),
            "12.abc.3.0",
        )

    def test_url_query_srid(self):
        self.assertEqual(
            normalize_scan_code("https://seller.wildberries.ru/?srid=12.abc.3.0&x=1"),
            "12.abc.3.0",
        )

    def test_json_sticker(self):
        self.assertEqual(normalize_scan_code('{"shkId": 57232120242}'), "57232120242")

    def test_trbx_and_sticker_parts(self):
        from backend.return_scan import expand_scan_codes
        codes = expand_scan_codes("TRBX3445947623")
        self.assertIn("3445947623", codes)
        self.assertIn("TRBX3445947623", codes)
        parts = expand_scan_codes("3554806 4307")
        self.assertIn("35548064307", parts)
        pink = expand_scan_codes("*CEbUajOQ")
        self.assertIn("*CEbUajOQ", pink)
        self.assertIn("!CEbUajOQ", pink)
        sort_code = expand_scan_codes("$1:1:5220447565:50166242")
        self.assertIn("5220447565", sort_code)
        mp = expand_scan_codes("mp.c0c8a7c2917c44509a172a298c740a1d.r")
        self.assertIn("mp.c0c8a7c2917c44509a172a298c740a1d.r", mp)
        self.assertIn("c0c8a7c2917c44509a172a298c740a1d", mp)

    def test_wb_return_sticker_qr_code(self):
        db = ScriptedDb()
        db.on(lambda sql, p: "wb_goods_returns" in sql.lower() and p.get("code") == "*CEbUajOQ", [{
            "srid": "12.abc.3.0",
            "shk_id": 4307524959,
            "sticker_id": "35548064307",
            "sticker_barcode": "*CEbUajOQ",
            "barcode": None,
            "nm_id": 425421144,
            "vendor_code": "GL-1",
            "subject_name": "Стеллаж",
            "status": "Выдан продавцу",
            "reason": "Брак",
            "return_type": "Возврат",
            "dst_office_address": "Казань Техническая Улица 17",
            "completed_dt": None,
        }])
        db.on(
            lambda sql, p: "lower(c.srid) = lower(:code)" in sql.lower() and p.get("code") == "12.abc.3.0",
            [WB_CLAIM],
        )
        found = lookup_scan(db, "*CEbUajOQ")
        self.assertEqual(found["item"]["match_by"], "wb_return_sticker")
        self.assertEqual(found["item"]["supplier_article"], "GL-1")


class LookupScanTests(unittest.TestCase):
    def test_finds_wb_claim_by_srid(self):
        db = ScriptedDb().on(
            lambda sql, p: "lower(c.srid) = lower(:code)" in sql.lower() and p.get("code") == "12.abc.3.0",
            [WB_CLAIM],
        )
        found = lookup_scan(db, "https://wb.ru/x/12.abc.3.0")
        self.assertEqual(found["kind"], "claim")
        self.assertEqual(found["item"]["match_by"], "srid")
        self.assertEqual(found["item"]["supplier_article"], "GL-1")
        self.assertIn("Некомплект: Фурнитура", found["item"]["categories"])
        self.assertIn("Производственный дефект", found["item"]["categories"])
        self.assertEqual(found["item"]["photos"], ["https://cdn.example/orig.jpg"])

    def test_numeric_sticker_beats_batch(self):
        db = ScriptedDb()
        db.on(lambda sql, p: _has(sql, "from wb_orders") and p.get("shk") == 57232120242, [
            {"srid": "12.abc.3.0", "supplier_article": "GL-1", "shk_id": 57232120242},
        ])
        db.on(lambda sql, p: "lower(c.srid) = lower(:code)" in sql.lower() and p.get("code") == "12.abc.3.0", [WB_CLAIM])
        found = lookup_scan(db, "57232120242")
        self.assertEqual(found["kind"], "claim")
        self.assertEqual(found["item"]["match_by"], "shk")
        self.assertEqual(found["item"]["barcode"], "57232120242")

    def test_income_id_batch(self):
        second = dict(WB_CLAIM, srid="99.other.1")
        db = ScriptedDb().on(
            lambda sql, p: _has(sql, "c.income_id") and str(p.get("income_id")) == "555111",
            [WB_CLAIM, second],
        )
        found = lookup_scan(db, "555111")
        self.assertEqual(found["kind"], "batch")
        self.assertEqual(found["count"], 2)
        self.assertEqual(found["income_id"], "555111")

    def test_ozon_barcode(self):
        db = ScriptedDb().on(
            lambda sql, p: "c.barcode =" in sql.lower() and p.get("code") == "OZ-99",
            [{
                "return_id": 77,
                "posting_number": "111-2",
                "barcode": "OZ-99",
                "supplier_article": "GL-2",
                "product_name": "Стул",
                "return_reason": "Брак",
                "status_ru": "Получен продавцом",
                "status_sys": "ReceivedBySeller",
                "photos": "",
                "buyer_comment": "Царапина",
                "place_name": "Склад",
                "target_place_name": "",
                "return_date": None,
                "ozon_sku": "5",
                "return_type": "ClientReturn",
                "schema_code": "Fbo",
                "product_photo": "",
            }],
        )
        found = lookup_scan(db, "OZ-99")
        self.assertEqual(found["item"]["marketplace"], "ozon")
        self.assertEqual(found["item"]["match_by"], "ozon_barcode")
        self.assertEqual(found["item"]["user_comment"], "Царапина")
        self.assertEqual(found["item"]["categories"], ["Брак"])

    def test_unknown_code_is_none(self):
        found = lookup_scan(ScriptedDb(), "no-such-code")
        self.assertIsNone(found)

    def test_wb_return_sticker_links_claim(self):
        db = ScriptedDb()
        db.on(lambda sql, p: "wb_goods_returns" in sql.lower() and p.get("code") == "35548064307", [{
            "srid": "12.abc.3.0",
            "shk_id": 4307524959,
            "sticker_id": "35548064307",
            "barcode": None,
            "nm_id": 425421144,
            "vendor_code": "GL-1",
            "subject_name": "Стеллаж",
            "status": "Выдан продавцу",
            "reason": "Брак",
            "return_type": "Возврат",
            "dst_office_address": "Казань Техническая Улица 17",
            "completed_dt": None,
        }])
        db.on(
            lambda sql, p: "lower(c.srid) = lower(:code)" in sql.lower() and p.get("code") == "12.abc.3.0",
            [WB_CLAIM],
        )
        found = lookup_scan(db, "35548064307")
        self.assertEqual(found["item"]["match_by"], "wb_return_sticker")
        self.assertEqual(found["item"]["supplier_article"], "GL-1")
        self.assertEqual(found["item"]["place_name"], "Казань Техническая Улица 17")

    def test_mp_srid_from_goods_return_without_claim(self):
        db = ScriptedDb()
        db.on(lambda sql, p: "wb_goods_returns" in sql.lower() and p.get("code") == "mp.c0c8a7c2917c44509a172a298c740a1d.r", [{
            "srid": "mp.c0c8a7c2917c44509a172a298c740a1d.r",
            "shk_id": 56765955699,
            "sticker_id": "54709840533",
            "sticker_barcode": "*DTeDvnOC",
            "barcode": None,
            "nm_id": 425421144,
            "vendor_code": None,
            "subject_name": None,
            "status": "Выдано",
            "reason": None,
            "return_type": None,
            "dst_office_address": "Казань Техническая 17",
            "completed_dt": None,
        }])
        db.on(lambda sql, p: "wb_logistics" in sql.lower(), [{
            "supplier_article": "Shelf-white-5/43",
            "name_ru": "Полка",
            "photo_url": "/static/p.jpg",
        }])
        found = lookup_scan(db, "mp.c0c8a7c2917c44509a172a298c740a1d.r")
        self.assertEqual(found["item"]["match_by"], "wb_mp_srid")
        self.assertEqual(found["item"]["srid"], "mp.c0c8a7c2917c44509a172a298c740a1d.r")
        self.assertEqual(found["item"]["supplier_article"], "Shelf-white-5/43")
        self.assertIsNone(found["item"]["claim_id"])
        self.assertFalse(found["item"]["has_claim"])
        self.assertIn("это нормально", found["item"]["no_claim_title"].lower())
        self.assertEqual(found["item"]["user_comment"], "")

    def test_cancel_return_is_not_a_missing_claim(self):
        db = ScriptedDb()
        db.on(lambda sql, p: "wb_goods_returns" in sql.lower() and p.get("code") == "39841818852", [{
            "srid": "1b671ab38d874ad891c59752e202c030",
            "shk_id": 56344140070,
            "sticker_id": "39841818852",
            "sticker_barcode": None,
            "barcode": None,
            "nm_id": 425429002,
            "vendor_code": "BB-Black",
            "subject_name": "Вешалка",
            "status": "Выдано",
            "reason": None,
            "return_type": "Отмена",
            "dst_office_address": "Казань Техническая Улица 17",
            "completed_dt": None,
        }])
        found = lookup_scan(db, "39841818852")
        self.assertFalse(found["item"]["has_claim"])
        self.assertIn("отмена", found["item"]["no_claim_title"].lower())
        self.assertIn("Причины дефекта здесь не будет", found["item"]["no_claim_text"])

    def test_goods_sticker_bridges_claim_via_order_shk(self):
        """Наклейка = поставка на склад; претензия сидит в заказе с тем же shk."""
        goods_srid = "1aa480747bbc446295d0996cd80e6b87"
        order_srid = "eAO.ib7258e6d45a1eb0061fea080bf914401.0.0"
        claim = dict(WB_CLAIM, srid=order_srid, supplier_article="WH-Basics-Black")
        db = ScriptedDb()
        db.on(lambda sql, p: "wb_goods_returns" in sql.lower() and p.get("code") == "56903118348", [{
            "srid": goods_srid,
            "shk_id": 56903118348,
            "sticker_id": "56903118348",
            "sticker_barcode": None,
            "barcode": "7930158480832",
            "nm_id": 1261022883,
            "vendor_code": None,
            "subject_name": "Вешалка",
            "status": "Выдано",
            "reason": None,
            "return_type": None,
            "dst_office_address": "Казань Техническая Улица 17",
            "completed_dt": None,
        }])
        db.on(lambda sql, p: _has(sql, "from wb_orders") and p.get("shk") == 56903118348, [
            {"srid": order_srid, "supplier_article": "WH-Basics-Black", "shk_id": 56903118348},
        ])
        db.on(
            lambda sql, p: "lower(c.srid) = lower(:code)" in sql.lower() and p.get("code") == order_srid,
            [claim],
        )
        found = lookup_scan(db, "5690311 8348")
        self.assertEqual(found["kind"], "claim")
        self.assertEqual(found["item"]["claim_id"], "99001")
        self.assertEqual(found["item"]["srid"], order_srid)
        self.assertEqual(found["item"]["supplier_article"], "WH-Basics-Black")
        self.assertEqual(found["item"]["match_by"], "shk")

    def test_goods_hex_srid_bridges_claim_via_shk(self):
        goods_srid = "1aa480747bbc446295d0996cd80e6b87"
        order_srid = "eAO.ib7258e6d45a1eb0061fea080bf914401.0.0"
        claim = dict(WB_CLAIM, srid=order_srid)
        db = ScriptedDb()
        db.on(lambda sql, p: "wb_goods_returns" in sql.lower() and p.get("code") == goods_srid, [{
            "srid": goods_srid,
            "shk_id": 56903118348,
            "sticker_id": "56903118348",
            "sticker_barcode": None,
            "barcode": None,
            "nm_id": 1261022883,
            "vendor_code": "WH-Basics-Black",
            "subject_name": "Вешалка",
            "status": "Выдано",
            "reason": None,
            "return_type": None,
            "dst_office_address": "Казань Техническая Улица 17",
            "completed_dt": None,
        }])
        db.on(lambda sql, p: _has(sql, "from wb_orders") and p.get("shk") == 56903118348, [
            {"srid": order_srid, "supplier_article": "WH-Basics-Black", "shk_id": 56903118348},
        ])
        db.on(
            lambda sql, p: "lower(c.srid) = lower(:code)" in sql.lower() and p.get("code") == order_srid,
            [claim],
        )
        found = lookup_scan(db, goods_srid)
        self.assertEqual(found["item"]["claim_id"], "99001")
        self.assertEqual(found["item"]["srid"], order_srid)


if __name__ == "__main__":
    unittest.main()
