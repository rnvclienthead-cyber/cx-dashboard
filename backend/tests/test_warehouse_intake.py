import inspect
import unittest
from datetime import datetime, timezone

from backend import warehouse_intake
from backend.routers import returns, roles
from backend.warehouse_intake import make_dedupe_key, period_span_days, record_intake


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class IntakeDb:
    def __init__(self):
        self.rows = []
        self.commits = 0
        self.ids = 0

    def _digits(self, value):
        return "".join(ch for ch in str(value or "") if ch.isdigit())

    def _match_find(self, row, params):
        code = str(params.get("code") or "")
        norm = str(params.get("norm") or "")
        barcode = str(params.get("barcode") or "")
        srid = str(params.get("srid") or "")
        key = params.get("k")
        if key and row["dedupe_key"] == key:
            return True
        if code and row["scan_code"] == code:
            return True
        if norm and (
            row["scan_code"] == norm
            or self._digits(row["scan_code"]) == norm
            or row["barcode"] == norm
            or self._digits(row["barcode"]) == norm
        ):
            return True
        if barcode and (row["barcode"] == barcode or row["scan_code"] == barcode):
            return True
        if srid and str(row.get("srid") or "").lower() == srid.lower():
            return True
        return False

    def execute(self, statement, params=None):
        sql = str(statement).lower()
        params = params or {}
        if "create table" in sql or "create index" in sql:
            return FakeResult([])
        if "from wb_assortment" in sql:
            art = str(params.get("art") or "")
            if art.upper() == "GL-1":
                return FakeResult([{
                    "supplier_article": "GL-1",
                    "name_ru": "Стеллаж",
                    "category_1": "Стеллажи",
                    "subject": "Стеллаж",
                }])
            return FakeResult([])
        if "insert into warehouse_intake" in sql:
            if any(r["dedupe_key"] == params["dedupe_key"] for r in self.rows):
                return FakeResult([])
            self.ids += 1
            row = {
                "id": self.ids,
                "dedupe_key": params["dedupe_key"],
                "scan_code": params["scan_code"],
                "barcode": params.get("barcode") or "",
                "marketplace": params.get("marketplace") or "",
                "supplier_article": params.get("supplier_article") or "",
                "product_name": params.get("product_name") or "",
                "nm_id": params.get("nm_id") or "",
                "srid": params.get("srid") or "",
                "claim_id": params.get("claim_id"),
                "status": params.get("status") or "",
                "has_claim": params.get("has_claim"),
                "match_by": params.get("match_by") or "",
                "category": params.get("category") or "",
                "categories": params.get("categories"),
                "product_photo": params.get("product_photo") or "",
                "payload": params.get("payload"),
                "found": params.get("found"),
                "received_at": datetime(2026, 9, 16, 8, 57, tzinfo=timezone.utc),
                "last_scan_at": datetime(2026, 9, 16, 8, 57, tzinfo=timezone.utc),
                "scan_count": 1,
            }
            self.rows.append(row)
            return FakeResult([row])
        if sql.strip().startswith("update warehouse_intake"):
            for row in self.rows:
                if row["id"] == params.get("id"):
                    row["scan_count"] = int(row["scan_count"]) + 1
                    row["last_scan_at"] = datetime(2026, 9, 16, 9, 10, tzinfo=timezone.utc)
                    if params.get("found"):
                        if params.get("supplier_article"):
                            row["supplier_article"] = params["supplier_article"]
                        if params.get("product_name"):
                            row["product_name"] = params["product_name"]
                        if params.get("barcode"):
                            row["barcode"] = row["barcode"] or params["barcode"]
                        if params.get("srid"):
                            row["srid"] = row["srid"] or params["srid"]
                        row["found"] = True
                    return FakeResult([row])
            return FakeResult([])
        if "from warehouse_intake" in sql:
            matched = [r for r in self.rows if self._match_find(r, params)]
            if matched:
                return FakeResult(matched)
            if not any(params.get(k) for k in ("code", "norm", "barcode", "srid", "k")):
                return FakeResult(list(self.rows))
            return FakeResult([])
        return FakeResult([])

    def rollback(self):
        pass

    def commit(self):
        self.commits += 1


class DedupeKeyTests(unittest.TestCase):
    def test_same_sticker_digits_share_key(self):
        self.assertEqual(
            make_dedupe_key("5690311 8348", {"marketplace": "wb", "barcode": "56903118348"}),
            "unit|56903118348",
        )
        self.assertEqual(make_dedupe_key("56903118348", None), "unit|56903118348")

    def test_short_unknown_stays_raw(self):
        self.assertEqual(make_dedupe_key("111", None), "raw|111")


class RecordIntakeTests(unittest.TestCase):
    def setUp(self):
        warehouse_intake._table_ready = True

    def test_first_scan_saves_article_and_time(self):
        db = IntakeDb()
        out = record_intake(db, "56903118348", {
            "marketplace": "wb",
            "barcode": "56903118348",
            "supplier_article": "GL-1",
            "srid": "12.abc.3.0",
            "nm_id": "123",
            "has_claim": True,
            "categories": ["Брак"],
        })
        self.assertFalse(out["duplicate"])
        self.assertEqual(out["supplier_article"], "GL-1")
        self.assertEqual(out["product_name"], "Стеллаж")
        self.assertEqual(out["category"], "Стеллажи")
        self.assertTrue(out["received_at"])
        self.assertEqual(db.commits, 1)

    def test_second_scan_does_not_create_another_row(self):
        db = IntakeDb()
        item = {"marketplace": "wb", "barcode": "56903118348", "supplier_article": "GL-1"}
        first = record_intake(db, "56903118348", item)
        second = record_intake(db, "56903118348", item)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(len(db.rows), 1)
        self.assertEqual(second["scan_count"], 2)
        self.assertEqual(second["id"], first["id"])

    def test_miss_then_found_is_still_one_intake(self):
        db = IntakeDb()
        miss = record_intake(db, "56903118348", None)
        found = record_intake(db, "56903118348", {
            "marketplace": "wb",
            "barcode": "56903118348",
            "supplier_article": "GL-1",
            "srid": "12.abc.3.0",
        })
        self.assertFalse(miss["duplicate"])
        self.assertTrue(found["duplicate"])
        self.assertEqual(len(db.rows), 1)
        self.assertEqual(found["id"], miss["id"])
        self.assertEqual(found["supplier_article"], "GL-1")
        self.assertTrue(found["found"])

    def test_two_different_stickers_are_two_intakes(self):
        db = IntakeDb()
        a = record_intake(db, "56903118348", {"marketplace": "wb", "barcode": "56903118348", "supplier_article": "GL-1"})
        b = record_intake(db, "39841818852", {"marketplace": "wb", "barcode": "39841818852", "supplier_article": "GL-1"})
        self.assertFalse(a["duplicate"])
        self.assertFalse(b["duplicate"])
        self.assertEqual(len(db.rows), 2)

    def test_unknown_sticker_is_still_logged(self):
        db = IntakeDb()
        out = record_intake(db, "00011122233", None)
        self.assertTrue(out["scan_code"])
        self.assertFalse(out["found"])
        self.assertEqual(out["supplier_article"], "")

    def test_qr_is_not_recorded(self):
        db = IntakeDb()
        self.assertIsNone(record_intake(db, "*ВУ9ПЕРрЙ", None))
        self.assertIsNone(record_intake(db, "*CUbB6ORp", {"supplier_article": "GL-1"}))
        self.assertEqual(len(db.rows), 0)


class RouterWiringTests(unittest.TestCase):
    def test_lookup_writes_intake(self):
        src = inspect.getsource(returns.lookup_return)
        self.assertIn("record_intake", src)
        self.assertIn("is_sticker_qr", src)
        self.assertIn("is_linear_sticker", src)
        self.assertIn('kind") == "claim"', src)
        self.assertLess(src.find("is_sticker_qr"), src.find("record_intake"))

    def test_intake_list_endpoint_exists(self):
        src = inspect.getsource(returns)
        self.assertIn('"/intake"', src)
        self.assertIn("list_today", src)
        self.assertLess(src.find("/intake"), src.find("/{srid}"))
        self.assertIn("days", inspect.getsource(returns.warehouse_intake_today))

    def test_intake_module_is_admin_only(self):
        keys = [m["key"] for m in roles.MODULES]
        self.assertIn("warehouse_intake", keys)
        self.assertLess(keys.index("reshipment"), keys.index("warehouse_intake"))
        self.assertNotIn("warehouse_intake", roles.ASSIGNABLE_MODULE_KEYS)
        self.assertNotIn("warehouse_intake", roles._WAREHOUSE_DEFAULT_MODULES)
        self.assertNotIn("warehouse_intake", roles._CS_DEFAULT_MODULES)


class PeriodSpanTests(unittest.TestCase):
    def test_clamps_days(self):
        self.assertEqual(period_span_days(7), 7)
        self.assertEqual(period_span_days(0), 1)
        self.assertEqual(period_span_days(99), 31)


if __name__ == "__main__":
    unittest.main()
