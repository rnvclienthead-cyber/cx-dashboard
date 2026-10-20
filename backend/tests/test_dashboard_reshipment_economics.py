import unittest

from backend.routers.dashboard import (
    RESHIPMENT_DELIVERY_COST_SQL,
    _parse_reshipment_economics,
    _WB_SAVED_SQL,
    _YM_SAVED_SQL,
)


class ReshipmentEconomicsTests(unittest.TestCase):
    def test_mixed_actual_return_scenario_recovers_stock_cog(self):
        result = _parse_reshipment_economics(
            # n, stays, returns, ppvz, retail, WB costs, all COG, detail delivery,
            # COG left with clients, COG returned to stock, reverse logistics
            (3, 1, 2, 3000, 4200, 300, 1500, 300, 400, 1100, 700),
            avg_return_delivery=372,
        )

        self.assertEqual(result["net_v3"], 1200)
        self.assertEqual(result["counterfactual_return_loss"], 1400)
        self.assertEqual(result["real_benefit"], 2600)
        self.assertEqual(result["saved_sales_amount"], 4200)
        self.assertEqual(result["return_to_stock_cog"], 1100)
        self.assertEqual(result["avg_return_delivery"], 350)
        self.assertEqual(result["ret_logistics"], 1116)
        self.assertEqual(result["loss_if_return_v1"], 1800)
        self.assertEqual(result["loss_if_return_v2"], 2916)
        self.assertEqual(result["benefit_vs_v1"], 3000)
        self.assertEqual(result["benefit_vs_v2"], 4116)
        self.assertEqual(result["avg_benefit_vs_v1"], 1000)
        self.assertEqual(result["avg_benefit_vs_v2"], 1372)

    def test_all_stay_with_client_does_not_add_reverse_logistics(self):
        result = _parse_reshipment_economics(
            (2, 2, 0, 1800, 2400, 200, 900, 200, 900, 0, 0),
            avg_return_delivery=372,
        )

        self.assertEqual(result["counterfactual_return_loss"], 1100)
        self.assertEqual(result["real_benefit"], 1800)
        self.assertEqual(result["avg_return_delivery"], 372)
        self.assertEqual(result["ret_logistics"], 744)
        self.assertEqual(result["loss_if_return_v1"], 1100)
        self.assertEqual(result["loss_if_return_v2"], 1844)
        self.assertEqual(result["benefit_vs_v1"], 1800)
        self.assertEqual(result["benefit_vs_v2"], 2544)

    def test_empty_aggregate_is_not_rendered(self):
        self.assertIsNone(
            _parse_reshipment_economics(
                (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
                avg_return_delivery=372,
            )
        )


class DeliveryCostSqlTests(unittest.TestCase):
    def test_ozon_uses_own_price_not_cdek_candidate(self):
        sql = RESHIPMENT_DELIVERY_COST_SQL
        self.assertIn("delivery_method = 'ozon'", sql)
        self.assertIn("ozon_cost", sql)
        self.assertIn("ozon_cost_actual", sql)
        self.assertIn("yandex_cost", sql)
        self.assertIn("cdek_cost_actual", sql)

    def test_funnel_counts_ozon_separately(self):
        from backend.routers.dashboard import get_executive_summary
        import inspect
        src = inspect.getsource(get_executive_summary)
        self.assertIn("approved_ozon", src)
        self.assertIn("delivery_method = 'ozon'", src)
        self.assertLess(src.find("delivery_method = 'ozon'"), src.find("delivery_method = 'cdek'"))


class RefusalSavingsSqlTests(unittest.TestCase):
    def test_wb_saved_sql_counts_refusals_not_approvals(self):
        sql = str(_WB_SAVED_SQL)
        self.assertIn("отказ", sql)
        self.assertIn("отклонено", sql)
        self.assertNotIn("'одобрено'", sql)

    def test_wb_saved_sql_splits_reshipment_from_other_refusals(self):
        sql = str(_WB_SAVED_SQL)
        self.assertIn("reshipment_requests", sql)
        self.assertIn("доотправк", sql)
        self.assertIn("reship_revenue", sql)
        self.assertIn("other_revenue", sql)

    def test_ym_saved_sql_counts_rejected_only(self):
        sql = str(_YM_SAVED_SQL)
        self.assertIn("REJECTED", sql)
        self.assertIn("отказ", sql)

    def test_ym_saved_sql_splits_reshipment_by_order(self):
        sql = str(_YM_SAVED_SQL)
        self.assertIn("reshipment_requests", sql)
        self.assertIn("order_number", sql)
        self.assertIn("reship_revenue", sql)


if __name__ == "__main__":
    unittest.main()
