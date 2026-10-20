import unittest

import pandas as pd

from backend.invoice_match import (
    claims_enriched_view_sql,
    claims_income_update_sql,
    clean_supply_id,
    init_claims_enriched_view,
    is_real_supply_id,
    logistics_upsert_sql,
    normalize_invoice_key,
    prefer_real_supply_rows,
)


class InvoiceMatchTests(unittest.TestCase):
    def test_invoice_key_ignores_hyphen_and_spaces(self):
        self.assertEqual(normalize_invoice_key("RNV26-P212"), "RNV26P212")
        self.assertEqual(
            normalize_invoice_key("RNV26P212"),
            normalize_invoice_key("RNV26-P212"),
        )
        self.assertEqual(normalize_invoice_key(" rnv26 p81 "), "RNV26P81")

    def test_zero_is_not_a_supply(self):
        self.assertFalse(is_real_supply_id(0))
        self.assertFalse(is_real_supply_id("0"))
        self.assertFalse(is_real_supply_id("0.0"))
        self.assertFalse(is_real_supply_id(""))
        self.assertFalse(is_real_supply_id(None))
        self.assertTrue(is_real_supply_id("41144033"))
        self.assertEqual(clean_supply_id(0), None)
        self.assertEqual(clean_supply_id("41144033"), "41144033")

    def test_prefer_real_supply_over_later_zero(self):
        df = pd.DataFrame([
            {"srid": "a", "income_id": "41144033", "last_change_date": "2026-08-01"},
            {"srid": "a", "income_id": 0, "last_change_date": "2026-09-01"},
            {"srid": "b", "income_id": "0", "last_change_date": "2026-09-02"},
        ])
        out = prefer_real_supply_rows(df)
        by_srid = {row["srid"]: row["income_id"] for _, row in out.iterrows()}
        self.assertEqual(str(by_srid["a"]), "41144033")
        self.assertEqual(str(by_srid["b"]), "0")

    def test_view_sql_normalizes_invoice_and_skips_fake_supply(self):
        sql = claims_enriched_view_sql()
        self.assertIn("regexp_replace", sql)
        self.assertIn("NOT IN ('', '0', '0.0'", sql)
        self.assertIn("HAVING COUNT(DISTINCT wb_factories.factory_name) = 1", sql)
        self.assertIn("HAVING COUNT(DISTINCT wb_factories.factory_name) > 1", sql)
        self.assertIn("cx_purged_claims", sql)

    def test_upsert_does_not_overwrite_real_income_with_zero(self):
        sql = logistics_upsert_sql()
        self.assertIn("THEN wb_logistics.income_id", sql)
        self.assertIn("IN ('', '0', '0.0'", sql)
        claim_sql = claims_income_update_sql()
        self.assertIn("THEN wb_claims.income_id", claim_sql)

    def test_parse_otgruzki_keeps_wb_and_ozon(self):
        from backend.invoice_match import parse_invoice_sheet_rows
        rows = parse_invoice_sheet_rows([
            {
                "Номер поставки": "39886747",
                "Артикул": "BO-Black-2",
                "Инвойс": "RNV26-P70",
                "МП": "ВБ",
            },
            {
                "Номер поставки": "41144033.0",
                "Артикул": "2-СБ-White",
                "Инвойс": "RNV26-P212",
                "МП": "ОЗОН",
            },
            {
                "Номер поставки": "1",
                "Артикул": "x",
                "Инвойс": "0",
                "МП": "ВБ",
            },
        ])
        self.assertEqual(
            rows,
            [
                {"sid": "39886747", "art": "BO-Black-2", "inv": "RNV26-P70", "mp": "wb"},
                {"sid": "41144033", "art": "2-СБ-White", "inv": "RNV26-P212", "mp": "ozon"},
            ],
        )

    def test_parse_skips_unclosed_invoice_and_unknown_mp(self):
        from backend.invoice_match import parse_invoice_sheet_rows
        rows = parse_invoice_sheet_rows([
            {
                "Номер поставки": "53226991",
                "Артикул": "BW-Black-1",
                "Инвойс": "Не закрытые инвойсы",
                "МП": "Офис Казань",
            },
            {
                "Номер поставки": "99",
                "Артикул": "X",
                "Инвойс": "RNV26-P1",
                "МП": "Магнит Маркет",
            },
        ])
        self.assertEqual(rows, [
            {"sid": "99", "art": "X", "inv": "RNV26-P1", "mp": "other"},
        ])

    def test_parse_prefers_factory_invoice_on_conflict(self):
        from backend.invoice_match import parse_invoice_sheet_rows
        rows = parse_invoice_sheet_rows(
            [
                {
                    "Номер поставки": "1",
                    "Артикул": "A",
                    "Инвойс": "P99OTHER",
                    "МП": "ВБ",
                },
                {
                    "Номер поставки": "1",
                    "Артикул": "A",
                    "Инвойс": "RNV26-P212",
                    "МП": "ВБ",
                },
            ],
            factory_keys={"RNV26P212"},
        )
        self.assertEqual(rows[0]["inv"], "RNV26-P212")

    def test_schema_startup_has_claims_view_helper(self):
        self.assertTrue(callable(init_claims_enriched_view))


if __name__ == "__main__":
    unittest.main()
