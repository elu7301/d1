"""Интеграционные проверки Flask и SQLite на настоящем наборе M5.

Временная база изолирована от рабочей. Исходный CSV только читается.
Запуск: python -m unittest chapter3.test_app -v
"""

from datetime import timedelta
from io import BytesIO, StringIO
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from chapter3.app import create_app
from common.data import load_sales
from common.forecasting import evaluate_models, forecast_sales


PROJECT = Path(__file__).resolve().parent.parent
SOURCE = PROJECT / "data" / "m5_sales.csv"


class ApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix="diplom_app_test_")
        cls.database = Path(cls.folder.name) / "test.sqlite3"
        cls.config = {"TESTING": True, "DATABASE": str(cls.database), "SOURCE_CSV": str(SOURCE)}
        cls.app = create_app(cls.config)
        cls.client = cls.app.test_client()
        cls.token = cls.app.config["CSRF_TOKEN"]
        cls.dataset_id = cls.client.get("/api/datasets").get_json()["datasets"][0]["id"]
        response = cls.client.post("/api/runs", json={
            "dataset_id": cls.dataset_id, "name": "Проверка полного цикла",
            "model": "linear_ridge", "horizon": 28,
        }, headers={"X-CSRF-Token": cls.token})
        if response.status_code not in [200, 201]:
            raise AssertionError(f"Не создан контрольный расчёт: {response.status_code} {response.get_data(as_text=True)}")
        cls.initial_run = response.get_json()["run"]
        cls.run_id = cls.initial_run["id"]
        cls.baseline_id = next(item["id"] for item in cls.initial_run["scenarios"] if item["is_baseline"])

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def headers(self):
        return {"X-CSRF-Token": self.token}

    def snapshot(self):
        """Сравниваем содержимое всех таблиц, а не только число записей."""
        with sqlite3.connect(self.database) as database:
            content = "\n".join(database.iterdump())
        return hashlib.sha256(content.encode()).hexdigest()

    def assert_api_error(self, response, status):
        self.assertEqual(response.status_code, status, response.get_data(as_text=True))
        self.assertTrue(response.is_json)
        self.assertIsInstance(response.get_json().get("error"), str)
        self.assertNotIn("Traceback", response.get_data(as_text=True))

    def create_scenario(self, name="Альтернатива для проверки"):
        response = self.client.post(f"/api/runs/{self.run_id}/scenarios", json={
            "name": name, "demand_change_pct": 20, "supply_delay_days": 2,
        }, headers=self.headers())
        self.assertIn(response.status_code, [200, 201], response.get_data(as_text=True))
        return response.get_json()["scenario"]

    def test_bootstrap_and_preview_use_real_persisted_sales(self):
        response = self.client.get("/api/datasets")
        self.assertEqual(response.status_code, 200)
        dataset = next(item for item in response.get_json()["datasets"] if item["id"] == self.dataset_id)
        self.assertEqual(dataset["rows"], 5823)
        self.assertEqual(dataset["products"], 3)
        self.assertEqual(dataset["currency"], "USD")
        preview = self.client.get(f"/api/datasets/{self.dataset_id}/preview")
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview.get_json()["rows"])
        self.assertTrue(preview.get_json()["quality"]["complete_calendar"])
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn(b"csrf-token", home.data)
        self.assertIn(self.token.encode(), home.data)

    def test_upload_same_real_content_is_deduplicated(self):
        before = self.snapshot()
        response = self.client.post("/api/datasets", data={
            "file": (BytesIO(SOURCE.read_bytes()), "m5_copy.csv"),
            "name": "Повторная загрузка M5", "currency": "USD",
        }, headers=self.headers(), content_type="multipart/form-data")
        self.assertIn(response.status_code, [200, 201], response.get_data(as_text=True))
        self.assertEqual(response.get_json()["dataset"]["id"], self.dataset_id)
        self.assertEqual(self.snapshot(), before)

    def test_invalid_upload_is_atomic(self):
        before = self.snapshot()
        real = pd.read_csv(SOURCE, dtype={"sku": "string"})
        duplicate = pd.concat([real, real.iloc[:1]]).to_csv(index=False).encode()
        for content in [b"date,sku,sales\nwrong,A,-1\n", duplicate]:
            response = self.client.post("/api/datasets", data={
                "file": (BytesIO(content), "invalid.csv"), "name": "Неверная таблица", "currency": "USD",
            }, headers=self.headers(), content_type="multipart/form-data")
            self.assert_api_error(response, 400)
            self.assertEqual(self.snapshot(), before)

    def test_run_contains_future_forecast_and_validation_snapshots(self):
        response = self.client.get(f"/api/runs/{self.run_id}")
        self.assertEqual(response.status_code, 200)
        run = response.get_json()["run"]
        self.assertEqual(len(run["forecast"]), 3 * 28)
        self.assertEqual(min(row["date"] for row in run["forecast"]), "2016-05-23")
        self.assertEqual(max(row["date"] for row in run["forecast"]), "2016-06-19")
        self.assertTrue(run["metrics"])
        self.assertTrue(run["backtest"])
        self.assertTrue(run["assumptions"])
        self.assertEqual(sum(bool(item["is_baseline"]) for item in run["scenarios"]), 1)
        json.dumps(run, allow_nan=False)
        listed = self.client.get("/api/runs").get_json()["runs"]
        self.assertIn(self.run_id, [item["id"] for item in listed])

    def test_scenario_crud_and_comparison(self):
        scenario = self.create_scenario()
        scenario_id = scenario["id"]
        response = self.client.put(f"/api/scenarios/{scenario_id}", json={
            "name": "Исправленная альтернатива", "demand_change_pct": -20,
        }, headers=self.headers())
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        updated = response.get_json()["scenario"]
        self.assertEqual(updated["name"], "Исправленная альтернатива")
        compared = self.client.post(f"/api/runs/{self.run_id}/compare", json={
            "scenario_ids": [self.baseline_id, scenario_id],
        }, headers=self.headers())
        self.assertEqual(compared.status_code, 200, compared.get_data(as_text=True))
        self.assertEqual(compared.get_json()["baseline_id"], self.baseline_id)
        self.assertIn(scenario_id, [item["id"] for item in compared.get_json()["scenarios"]])
        self.assertTrue(all("delta_profit" in item for item in compared.get_json()["scenarios"]))
        deleted = self.client.delete(f"/api/scenarios/{scenario_id}", headers=self.headers())
        self.assertEqual(deleted.status_code, 200)
        run = self.client.get(f"/api/runs/{self.run_id}").get_json()["run"]
        self.assertNotIn(scenario_id, [item["id"] for item in run["scenarios"]])

    def test_baseline_is_protected(self):
        before = self.snapshot()
        edited = self.client.put(f"/api/scenarios/{self.baseline_id}", json={
            "name": "Попытка изменить базу", "demand_change_pct": 50,
        }, headers=self.headers())
        self.assert_api_error(edited, 409)
        deleted = self.client.delete(f"/api/scenarios/{self.baseline_id}", headers=self.headers())
        self.assert_api_error(deleted, 409)
        self.assertEqual(self.snapshot(), before)

    def test_scenarios_from_different_runs_cannot_be_compared(self):
        created = self.client.post("/api/runs", json={
            "dataset_id": self.dataset_id, "name": "Другой горизонт", "model": "mean7", "horizon": 7,
        }, headers=self.headers())
        self.assertIn(created.status_code, [200, 201], created.get_data(as_text=True))
        other = created.get_json()["run"]
        other_id = next(item["id"] for item in other["scenarios"] if item["is_baseline"])
        response = self.client.post(f"/api/runs/{self.run_id}/compare", json={
            "scenario_ids": [self.baseline_id, other_id],
        }, headers=self.headers())
        self.assert_api_error(response, 400)

    def test_sensitivity_changes_no_database_records(self):
        before = self.snapshot()
        response = self.client.post(f"/api/runs/{self.run_id}/sensitivity", json={
            "scenario_id": self.baseline_id, "demand_changes": [-20, 0, 20],
        }, headers=self.headers())
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(len(response.get_json()["points"]), 3)
        json.dumps(response.get_json(), allow_nan=False)
        self.assertEqual(self.snapshot(), before)

    def test_csv_exports_download_saved_results(self):
        for kind in ["comparison", "forecast", "daily"]:
            response = self.client.get(f"/api/runs/{self.run_id}/export", query_string={
                "kind": kind, "scenario_id": self.baseline_id,
            })
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            self.assertIn("text/csv", response.content_type)
            self.assertIn("attachment", response.headers.get("Content-Disposition", ""))
            reader = csv.DictReader(StringIO(response.data.decode("utf-8-sig")))
            rows = list(reader)
            self.assertTrue(rows)
            if kind in ["forecast", "daily"]:
                self.assertEqual(len(rows), 84)

    def test_invalid_horizon_and_nonfinite_scenario_are_rejected(self):
        before = self.snapshot()
        for horizon in [0, 57, 14.5, True]:
            response = self.client.post("/api/runs", json={
                "dataset_id": self.dataset_id, "name": "Неверный расчёт", "model": "mean7", "horizon": horizon,
            }, headers=self.headers())
            self.assert_api_error(response, 400)
        response = self.client.post(f"/api/runs/{self.run_id}/scenarios", json={
            "name": "Неверный сценарий", "elasticity": float("nan"),
        }, headers=self.headers())
        self.assert_api_error(response, 400)
        self.assertEqual(self.snapshot(), before)

    def test_csrf_and_unknown_api_paths_return_json_errors(self):
        before = self.snapshot()
        response = self.client.post("/api/runs", json={"dataset_id": self.dataset_id, "horizon": 28})
        self.assert_api_error(response, 403)
        response = self.client.delete(f"/api/scenarios/{self.baseline_id}", headers={"X-CSRF-Token": "wrong"})
        self.assert_api_error(response, 403)
        self.assert_api_error(self.client.get("/api/not-a-route"), 404)
        self.assertEqual(self.snapshot(), before)

    def test_run_survives_application_restart(self):
        before = self.client.get(f"/api/runs/{self.run_id}").get_json()["run"]
        new_app = create_app(self.config)
        response = new_app.test_client().get(f"/api/runs/{self.run_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["run"], before)
        datasets = new_app.test_client().get("/api/datasets").get_json()["datasets"]
        self.assertEqual(sum(item["id"] == self.dataset_id for item in datasets), 1)

    def test_supplied_future_calendar_is_normalized_and_survives_restart(self):
        dates = pd.date_range("2016-05-23", periods=7)
        calendar = [{"date": day.date().isoformat(), "snap": index % 2,
                     "event": int(index in [1, 4])} for index, day in enumerate(dates)]
        response = self.client.post("/api/runs", json={
            "dataset_id": self.dataset_id, "name": "Сохранение будущего календаря",
            "model": "mean7", "horizon": 7, "future_calendar": list(reversed(calendar)),
        }, headers=self.headers())
        self.assertIn(response.status_code, [200, 201], response.get_data(as_text=True))
        created = response.get_json()["run"]
        self.assertTrue(created["future_calendar_supplied"])
        self.assertEqual(created["future_calendar"], calendar)
        reopened = create_app(self.config).test_client().get(f"/api/runs/{created['id']}")
        self.assertEqual(reopened.status_code, 200)
        saved = reopened.get_json()["run"]
        self.assertTrue(saved["future_calendar_supplied"])
        self.assertEqual(saved["future_calendar"], calendar)

    def test_selected_comparison_export_excludes_unselected_and_foreign_scenarios(self):
        chosen = self.create_scenario("Вариант для выгрузки")
        unselected = self.create_scenario("Не выбран для выгрузки")
        response = self.client.get(f"/api/runs/{self.run_id}/export", query_string={
            "kind": "comparison", "scenario_ids": f"{self.baseline_id},{chosen['id']}",
        })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        rows = list(csv.DictReader(StringIO(response.data.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 2)
        exported_ids = {int(row["scenario_id"]) for row in rows}
        self.assertEqual(exported_ids, {self.baseline_id, chosen["id"]})
        self.assertNotIn(unselected["id"], exported_ids)
        another = self.client.post("/api/runs", json={
            "dataset_id": self.dataset_id, "name": "Другой расчёт для проверки выгрузки",
            "model": "mean7", "horizon": 7,
        }, headers=self.headers())
        self.assertIn(another.status_code, [200, 201], another.get_data(as_text=True))
        foreign_id = next(item["id"] for item in another.get_json()["run"]["scenarios"] if item["is_baseline"])
        forbidden = self.client.get(f"/api/runs/{self.run_id}/export", query_string={
            "kind": "comparison", "scenario_ids": f"{self.baseline_id},{foreign_id}",
        })
        self.assert_api_error(forbidden, 400)


class ForecastAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = load_sales(SOURCE)
        # Реальные последние 140 дней, без генерации наблюдений.
        first = sorted(data["date"].unique())[-140]
        cls.sales = data[data["date"] >= first].copy()

    def test_adapter_forecasts_after_history_and_returns_json_safe_values(self):
        rows, assumptions = forecast_sales(self.sales, "linear_ridge", 28)
        self.assertEqual(len(rows), 84)
        last = self.sales["date"].max().date()
        self.assertEqual(min(row["date"] for row in rows), (last + timedelta(days=1)).isoformat())
        self.assertEqual(max(row["date"] for row in rows), (last + timedelta(days=28)).isoformat())
        self.assertTrue(all(row["prediction"] >= 0 for row in rows))
        self.assertTrue(assumptions)
        json.dumps(rows, allow_nan=False)

    def test_future_calendar_must_match_dates_and_binary_indicators(self):
        dates = pd.date_range(self.sales["date"].max().date() + timedelta(days=1), periods=7)
        calendar = [{"date": day.date().isoformat(), "snap": 0, "event": 0} for day in dates]
        rows, assumptions = forecast_sales(self.sales, "mean7", 7, calendar)
        self.assertEqual(len(rows), 21)
        self.assertTrue(any("пользовател" in item for item in assumptions))
        for field, value in [("date", "2000-01-01"), ("snap", 2), ("event", None)]:
            invalid = [dict(row) for row in calendar]
            invalid[0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                forecast_sales(self.sales, "mean7", 7, invalid)

    def test_backtest_uses_three_sequential_windows(self):
        last_predictions, last_metrics, validation = evaluate_models(self.sales, 28)
        self.assertEqual(len(last_predictions), 3 * 3 * 28)
        self.assertEqual(len(last_metrics), 12)
        self.assertEqual(len(validation["windows"]), 3)
        for previous, current in zip(validation["windows"], validation["windows"][1:]):
            self.assertLess(previous["test_end"], current["test_start"])
        self.assertEqual(len(validation["rolling_metrics"]), 3)
        json.dumps(validation, allow_nan=False)

    def test_future_forecast_and_backtest_share_model_replacement_point(self):
        with patch("common.forecasting.predict_model",
                   side_effect=lambda train, plan, model: np.ones(len(plan))) as replaced:
            future, _ = forecast_sales(self.sales, "linear_ridge", 28)
            self.assertTrue(replaced.called)
            replaced.reset_mock()
            backtest, metrics, validation = evaluate_models(self.sales, 28)
            self.assertTrue(replaced.called)
        self.assertTrue(all(row["prediction"] == 1 for row in future))
        self.assertTrue(all(row["prediction"] == 1 for row in backtest))
        self.assertEqual({call.args[2] for call in replaced.call_args_list},
                         {"mean7", "seasonal_naive", "linear_ridge"})
        cutoff = sorted(self.sales["date"].unique())[-28]
        actual = self.sales.loc[self.sales["date"] >= cutoff, "sales"].to_numpy()
        expected_mae = float(np.mean(np.abs(actual - 1)))
        for score in metrics:
            if score["sku"] == "ALL":
                self.assertAlmostEqual(score["MAE"], expected_mae)
        self.assertEqual(len(validation["windows"]), 3)


if __name__ == "__main__":
    unittest.main()
