import unittest

from backend.routers.dashboard import _parse_reshipment_economics


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


if __name__ == "__main__":
    unittest.main()
