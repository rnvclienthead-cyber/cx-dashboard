import unittest

from backend.wb_return_logistics import LAST_N, sku_return_log_cte, fetch_avg_return_delivery


class SkuReturnLogCteTests(unittest.TestCase):
    def test_cte_uses_last_n_and_finance_logistics_rows(self):
        sql = sku_return_log_cte()
        self.assertIn(f"rn <= {LAST_N}", sql)
        self.assertIn("doc_type_name = ''", sql)
        self.assertIn("mp.%.r", sql)
        self.assertNotIn("WITH ", sql)

    def test_empty_article_skips_sku_query(self):
        class FakeDb:
            def execute(self, statement, params=None):
                sql = str(statement)
                self.sql = sql
                self.params = params

                class Result:
                    def mappings(self_inner):
                        return self_inner

                    def first(self_inner):
                        return {"avg_delivery": 429.4}

                return Result()

        db = FakeDb()
        result = fetch_avg_return_delivery(db, None)
        self.assertEqual(result["source"], "fallback")
        self.assertEqual(result["n"], 0)
        self.assertEqual(result["value"], 429.4)
        self.assertIsNone(db.params)


if __name__ == "__main__":
    unittest.main()
