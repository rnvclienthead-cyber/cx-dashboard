import inspect
import unittest

import pandas as pd

from backend.ppm_defects import mark_ppm_defects, ppm_defect_sql, ym_not_plain_refusal_sql
from backend.routers.ppm import _load_ym_claims
from backend.routers.production import get_production_claims


def _row(**kwargs):
    base = {f"cat_{i}": False for i in range(1, 14)}
    base.update(kwargs)
    return base


class MarkPpmDefectsTests(unittest.TestCase):
    def test_july_not_fit_still_counts(self):
        df = pd.DataFrame([_row(created_dt="2026-07-15", cat_12=True, platform="wb")])
        self.assertTrue(bool(mark_ppm_defects(df).iloc[0]))

    def test_august_not_fit_does_not_count(self):
        df = pd.DataFrame([_row(created_dt="2026-08-01", cat_12=True, platform="wb")])
        self.assertFalse(bool(mark_ppm_defects(df).iloc[0]))

    def test_august_factory_tag_counts(self):
        df = pd.DataFrame([_row(created_dt="2026-08-02", cat_4=True, platform="wb")])
        self.assertTrue(bool(mark_ppm_defects(df).iloc[0]))

    def test_july_ozon_needs_factory_tag(self):
        df = pd.DataFrame([
            _row(created_dt="2026-07-20", platform="ozon"),
            _row(created_dt="2026-07-20", platform="ozon", cat_1=True),
        ])
        marked = mark_ppm_defects(df)
        self.assertFalse(bool(marked.iloc[0]))
        self.assertTrue(bool(marked.iloc[1]))

    def test_july_ym_not_fit_does_not_count(self):
        df = pd.DataFrame([_row(created_dt="2026-07-15", cat_12=True, platform="ym")])
        self.assertFalse(bool(mark_ppm_defects(df).iloc[0]))

    def test_july_ym_factory_tag_counts(self):
        df = pd.DataFrame([_row(created_dt="2026-07-15", cat_4=True, platform="ym")])
        self.assertTrue(bool(mark_ppm_defects(df).iloc[0]))

    def test_august_ozon_needs_factory_tag(self):
        df = pd.DataFrame([
            _row(created_dt="2026-08-10", platform="ozon"),
            _row(created_dt="2026-08-10", platform="ozon", cat_1=True),
        ])
        marked = mark_ppm_defects(df)
        self.assertFalse(bool(marked.iloc[0]))
        self.assertTrue(bool(marked.iloc[1]))

    def test_sql_switches_on_august(self):
        sql = ppm_defect_sql("created_dt")
        self.assertIn("2026-08-01", sql)
        self.assertIn("cat_1 OR cat_2 OR cat_4 OR cat_5 OR cat_7 OR cat_9", sql)
        self.assertIn("cat_12", sql)


class YmReshipRefusalTests(unittest.TestCase):
    def test_sql_keeps_rejected_only_with_reshipment(self):
        sql = ym_not_plain_refusal_sql("r")
        self.assertIn("REJECTED", sql)
        self.assertIn("CANCELLED", sql)
        self.assertIn("reshipment_requests", sql)
        self.assertIn("ym_chats", sql)
        self.assertIn("order_number", sql)

    def test_ppm_loader_uses_reshipment_exception(self):
        src = inspect.getsource(_load_ym_claims)
        self.assertIn("ym_not_plain_refusal_sql", src)
        self.assertNotIn("r.status NOT IN ('NOT_RETURNED', 'FAILED', 'CANCELLED', 'REJECTED')", src)

    def test_problem_map_uses_reshipment_exception(self):
        src = inspect.getsource(get_production_claims)
        self.assertIn("ym_not_plain_refusal_sql", src)
        self.assertIn('status_code == "CANCELLED"', src)


if __name__ == "__main__":
    unittest.main()
