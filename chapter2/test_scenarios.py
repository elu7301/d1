"""Проверки экономических тождеств и правил движения товара в памяти."""

from datetime import date, timedelta
import math
import unittest

from chapter2.scenarios import simulate_scenario, validate_parameters


def forecast(days=14, products=("A", "B"), demand=10, price=100):
    """Небольшие явные контрольные значения для проверки расчётных правил."""
    return [{"date": (date(2026, 1, 1) + timedelta(days=index)).isoformat(),
             "sku": sku, "prediction": demand, "price": price}
            for index in range(days) for sku in products]


class ScenarioTests(unittest.TestCase):
    def test_daily_and_aggregate_stock_mass_balance(self):
        result = simulate_scenario(forecast(28), {})
        for row in result["daily"]:
            self.assertAlmostEqual(row["opening_stock"] + row["received"] - row["sales"], row["closing_stock"])
            self.assertAlmostEqual(row["sales"] + row["lost_sales"], row["demand"])
            for key in ["demand", "sales", "lost_sales", "opening_stock", "received", "ordered", "closing_stock", "pipeline"]:
                self.assertGreaterEqual(row[key], 0)
        summary = result["summary"]
        self.assertAlmostEqual(summary["stock_balance"], 0)
        self.assertAlmostEqual(summary["ordered_units"] - summary["received_units"], summary["pipeline_units"])

    def test_equal_opening_inventory_across_scenarios(self):
        data = forecast()
        base = simulate_scenario(data, {})["summary"]
        alternative = simulate_scenario(data, {"demand_change_pct": 100, "price_change_pct": -20,
                                               "promo_uplift_pct": 50})["summary"]
        self.assertEqual(base["initial_stock_units"], alternative["initial_stock_units"])
        self.assertEqual(base["initial_stock_value"], alternative["initial_stock_value"])
        self.assertGreater(alternative["total_demand"], base["total_demand"])

    def test_profit_deducts_only_sold_inventory_once(self):
        summary = simulate_scenario(forecast(1, ("A",)), {
            "initial_stock_days": 1, "target_stock_days": 2,
            "unit_cost_ratio": 0.6, "holding_cost_rate_daily": 0,
            "order_fixed_cost": 0, "promo_budget": 0,
        })["summary"]
        self.assertAlmostEqual(summary["revenue"], 1000)
        self.assertAlmostEqual(summary["cost_of_goods_sold"], 600)
        self.assertAlmostEqual(summary["profit"], 400)
        self.assertAlmostEqual(summary["procurement_spend"], 600)
        self.assertAlmostEqual(summary["pipeline_value"], 600)
        self.assertAlmostEqual(summary["initial_stock_value"] + summary["procurement_spend"],
                               summary["cost_of_goods_sold"] + summary["ending_stock_value"] + summary["pipeline_value"])

    def test_delayed_arrivals_remain_in_pipeline(self):
        data = forecast(7, ("A",))
        base = simulate_scenario(data, {"initial_stock_days": 0, "lead_time_days": 1})["summary"]
        delayed = simulate_scenario(data, {"initial_stock_days": 0, "lead_time_days": 1,
                                          "supply_delay_days": 10})["summary"]
        self.assertGreater(base["sales_units"], delayed["sales_units"])
        self.assertEqual(delayed["received_units"], 0)
        self.assertEqual(delayed["sales_units"], 0)
        self.assertGreater(delayed["pipeline_units"], 0)
        self.assertEqual(delayed["stockout_days"], 7)

    def test_existing_pipeline_prevents_duplicate_replenishment(self):
        daily = simulate_scenario(forecast(5, ("A",)), {
            "initial_stock_days": 0, "lead_time_days": 4,
            "target_stock_days": 10, "review_period_days": 1,
        })["daily"]
        self.assertEqual([row["ordered"] for row in daily], [100, 0, 0, 0, 0])
        self.assertEqual([row["received"] for row in daily], [0, 0, 0, 0, 100])
        self.assertEqual(daily[-1]["sales"], 10)

    def test_inventory_value_reconciles_with_cost_of_sold_goods(self):
        data = forecast(28)
        for index, row in enumerate(data):
            row["prediction"] += index % 4
            row["price"] += index % 3
        summary = simulate_scenario(data, {"price_change_pct": -10,
                                            "promo_uplift_pct": 20})["summary"]
        self.assertAlmostEqual(summary["initial_stock_value"] + summary["procurement_spend"],
                               summary["cost_of_goods_sold"] + summary["ending_stock_value"]
                               + summary["pipeline_value"], places=7)

    def test_price_effect_and_cost_basis_are_explicit(self):
        summary = simulate_scenario(forecast(1, ("A",)), {
            "price_change_pct": 100, "elasticity": 1,
            "initial_stock_days": 10, "unit_cost_ratio": 0.6,
            "holding_cost_rate_daily": 0, "order_fixed_cost": 0,
        })["summary"]
        self.assertAlmostEqual(summary["total_demand"], 5)
        self.assertAlmostEqual(summary["revenue"], 1000)
        self.assertAlmostEqual(summary["cost_of_goods_sold"], 300)

    def test_promo_budget_is_for_whole_scenario(self):
        summary = simulate_scenario(forecast(7), {"promo_budget": 140})["summary"]
        self.assertAlmostEqual(summary["promo_cost"], 140)
        self.assertAlmostEqual(summary["profit"], summary["revenue"] - summary["cost_of_goods_sold"]
                               - summary["holding_cost"] - summary["ordering_cost"] - 140)

    def test_zero_demand_does_not_create_orders(self):
        summary = simulate_scenario(forecast(demand=0), {})["summary"]
        self.assertEqual(summary["total_demand"], 0)
        self.assertEqual(summary["orders_count"], 0)
        self.assertEqual(summary["service_level"], 1)
        self.assertEqual(summary["profit"], 0)

    def test_invalid_parameters_are_rejected(self):
        for params in [{"elasticity": math.nan}, {"price_change_pct": -100},
                       {"lead_time_days": 1.5}, {"unit_cost_ratio": math.inf},
                       {"name": " "}, {"promo_budget": True}, {"typo": 3}]:
            with self.subTest(params=params), self.assertRaises(ValueError):
                validate_parameters(params)

    def test_invalid_forecast_is_rejected(self):
        valid = forecast(3)
        cases = [[], valid + [valid[0]], valid[1:]]
        for key, value in [("prediction", -1), ("prediction", math.nan),
                           ("price", 0), ("price", math.inf), ("date", "01.01.2026")]:
            changed = [dict(row) for row in valid]
            changed[0][key] = value
            cases.append(changed)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                simulate_scenario(case, {})


if __name__ == "__main__":
    unittest.main()
