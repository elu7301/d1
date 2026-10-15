"""Проверки на реальных данных: python -m unittest discover -s chapter1 -v."""

import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from common.data import load_sales, validate_sales
from common.metrics import calculate_metrics
from chapter1.forecast import compare_models


class ProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        project = Path(__file__).resolve().parent.parent
        cls.data = load_sales(project / "data" / "m5_sales.csv")

    def test_future_actuals_and_prices_do_not_change_forecast(self):
        first, _, _ = compare_models(self.data)
        cutoff = sorted(self.data["date"].unique())[-28]
        for column, value in [("sales", 9999), ("price", 999.0)]:
            changed = self.data.copy()
            changed.loc[changed["date"] >= cutoff, column] = value
            second, _, _ = compare_models(changed)
            np.testing.assert_allclose(first["prediction"], second["prediction"])

    def test_seasonal_naive_repeats_last_training_week(self):
        predictions, _, experiment = compare_models(self.data)
        sku = self.data.iloc[0]["sku"]
        history = self.data[(self.data["sku"] == sku) &
                            (self.data["date"] < experiment["test_start"])]
        expected = np.tile(history["sales"].tail(7), 4)
        actual = predictions[(predictions["sku"] == sku) &
                             (predictions["model"] == "seasonal_naive")]["prediction"]
        np.testing.assert_allclose(actual, expected)

    def test_duplicate_and_missing_day_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "повторные"):
            validate_sales(pd.concat([self.data, self.data.iloc[:1]]))
        with self.assertRaisesRegex(ValueError, "неполный"):
            validate_sales(self.data.iloc[1:])

    def test_invalid_values_are_rejected(self):
        for column, invalid in [("sales", -1), ("sales", np.inf), ("sales", np.nan),
                                ("price", 0), ("snap", 2), ("event", 2)]:
            changed = self.data.copy()
            changed[column] = changed[column].astype(float)
            changed.loc[0, column] = invalid
            with self.assertRaises(ValueError):
                validate_sales(changed)

    def test_zero_actuals_have_undefined_wape(self):
        scores = calculate_metrics([0, 0], [1, 3])
        self.assertEqual(scores["MAE"], 2)
        self.assertTrue(np.isnan(scores["WAPE_percent"]))

    def test_real_data_and_horizon(self):
        self.assertEqual(len(self.data), 5823)
        predictions, metrics, _ = compare_models(self.data, horizon=14)
        self.assertEqual(len(predictions), 3 * 3 * 14)
        self.assertEqual(len(metrics), 12)
        self.assertTrue((predictions["prediction"] >= 0).all())
        with self.assertRaises(ValueError):
            compare_models(self.data, horizon=0)


if __name__ == "__main__":
    unittest.main()
