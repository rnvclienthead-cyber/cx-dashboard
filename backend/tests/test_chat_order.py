import unittest

from backend.chat_order import resolve_chat_order, _ozon_status_label, _ym_status_label, _tone


class _Result:
    def __init__(self, row):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


class _Conn:
    def __init__(self, rows):
        self.rows = list(rows)
        self.sqls = []
        self.params = []

    def execute(self, sql, params=None):
        self.sqls.append(" ".join(str(sql).split()))
        self.params.append(params or {})
        row = self.rows.pop(0) if self.rows else None
        return _Result(row)


class LabelTests(unittest.TestCase):
    def test_ozon_and_ym_status(self):
        self.assertEqual(_ozon_status_label("delivering"), "В пути")
        self.assertEqual(_ozon_status_label("delivered"), "Доставлен")
        self.assertEqual(_ym_status_label("PICKUP"), "В пункте выдачи")
        self.assertEqual(_ym_status_label("DELIVERY"), "В доставке")

    def test_tone(self):
        self.assertEqual(_tone("cancelled", "Отменён"), "bad")
        self.assertEqual(_tone("delivered", "Доставлен"), "ok")
        self.assertEqual(_tone("delivering", "В пути"), "info")
        self.assertEqual(_tone("RETURN", "Возврат"), "warn")


class ResolveChatOrderTests(unittest.TestCase):
    def test_wb_logistics_by_srid_only(self):
        conn = _Conn([
            {
                "srid": "eB5.iaaa.0.0",
                "dt": "2026-09-12",
                "supplier_article": "BS-Black-2",
                "nm_id": 1,
                "finish_price": 2490,
                "warehouse_name": "Коледино",
                "doc_type": "SALE",
                "is_cancel": False,
            },
            None,
        ])
        out = resolve_chat_order(conn, "wb", "1:abc", rid="eB5.iaaa.0.0")
        self.assertEqual(out["status_label"], "Продажа")
        self.assertEqual(out["article"], "BS-Black-2")
        self.assertEqual(out["warehouse"], "Коледино")
        self.assertEqual(out["price"], 2490)
        self.assertEqual(out["date_label"], "12 сентября")
        self.assertIn("wb_logistics", conn.sqls[0])
        self.assertIn("srid = :r", conn.sqls[0])
        self.assertEqual(conn.params[0]["r"], "eB5.iaaa.0.0")
        joined = " ".join(conn.sqls).lower()
        self.assertNotIn("trim(nm_id", joined)
        self.assertNotIn("seq scan", joined)

    def test_wb_cancel(self):
        conn = _Conn([
            {
                "srid": "x.0.0",
                "dt": None,
                "supplier_article": "A",
                "nm_id": 1,
                "finish_price": None,
                "warehouse_name": "",
                "doc_type": "SALE",
                "is_cancel": True,
            },
            None,
        ])
        out = resolve_chat_order(conn, "wb", "1:x", rid="x.0.0")
        self.assertEqual(out["status_label"], "Отменён")
        self.assertEqual(out["tone"], "bad")

    def test_wb_dbw_status_wins(self):
        conn = _Conn([
            {
                "srid": "dbw.0.0",
                "dt": "2026-09-01",
                "supplier_article": "DBW-1",
                "nm_id": 2,
                "finish_price": 100,
                "warehouse_name": "DBW",
                "doc_type": "SALE",
                "is_cancel": False,
            },
            {
                "supplier_status": "complete",
                "wb_status": "waiting",
                "ui_status": "В доставке",
                "article": "DBW-1",
            },
        ])
        out = resolve_chat_order(conn, "wb", "1:x", rid="dbw.0.0")
        self.assertEqual(out["status_label"], "В доставке")
        self.assertEqual(out["tone"], "info")

    def test_ozon_delivering(self):
        conn = _Conn([
            {
                "posting_number": "123-1",
                "order_number": "PE-1",
                "status": "delivering",
                "supplier_article": "OZ-1",
                "sku_name": "полка",
                "created_at": "2026-09-10",
                "price": 990,
            },
        ])
        out = resolve_chat_order(conn, "ozon", "chat-1", order_n="PE-1")
        self.assertEqual(out["status_label"], "В пути")
        self.assertEqual(out["order_id"], "PE-1")
        self.assertEqual(out["posting_number"], "123-1")
        self.assertEqual(out["price"], 990)
        self.assertIn("ozon_orders", conn.sqls[0])

    def test_ym_pickup(self):
        conn = _Conn([
            {
                "order_id": 551122,
                "status": "PICKUP",
                "supplier_article": "YM-1",
                "sku_name": "стол",
                "created_at": "2026-08-30",
                "price": 1200,
                "buyer_total": 1500,
                "region_name": "Казань",
            },
        ])
        out = resolve_chat_order(conn, "ym", "c1", order_n="551122")
        self.assertEqual(out["status_label"], "В пункте выдачи")
        self.assertEqual(out["price"], 1500)
        self.assertEqual(out["region"], "Казань")
        self.assertIn("ym_orders", conn.sqls[0])
        self.assertIn("order_id::text = :oid", conn.sqls[0])

    def test_missing_is_none(self):
        conn = _Conn([None, None])
        self.assertIsNone(resolve_chat_order(conn, "wb", "1:x", rid="missing"))
        self.assertIsNone(resolve_chat_order(_Conn([None]), "ozon", "c", order_n="nope"))


if __name__ == "__main__":
    unittest.main()
