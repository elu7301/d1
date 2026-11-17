"""Контракт ML-модуля: таблица истории → прогноз и проверка качества.

Для улучшения модели измените predict_model и её version в MODELS.
Один и тот же адаптер используется для будущего прогноза и проверки на истории.
HTTP, база и расчёт сценариев работают с прежним форматом результата.
"""

import numpy as np
import pandas as pd

from chapter1.forecast import predict_models
from common.metrics import calculate_metrics


MODELS = {
    "mean7": {"name": "Среднее за 7 дней", "version": "mean7-v1"},
    "seasonal_naive": {"name": "Повторение последней недели", "version": "seasonal-naive-v1"},
    "linear_ridge": {"name": "Линейная Ridge", "version": "ridge-numpy-v1"},
}


def predict_model(train, plan, model):
    """Единая точка замены ML: возвращает по одному значению на дату плана."""
    history = train["sales"].to_numpy(dtype=float)
    if model == "mean7":
        values = np.repeat(history[-7:].mean(), len(plan))
    elif model == "seasonal_naive":
        values = np.resize(history[-7:], len(plan))
    elif model == "linear_ridge":
        values = predict_models(train, plan)["linear_ridge"]
    else:
        raise ValueError("Неизвестная модель прогноза.")
    values = np.asarray(values, dtype=float)
    if values.shape != (len(plan),) or not np.isfinite(values).all():
        raise ValueError("Модель должна вернуть конечные числовые прогнозы для всех дат.")
    return np.maximum(values, 0)


def records(frame):
    """JSON-совместимые записи: неопределённая WAPE становится null."""
    clean = frame.copy()
    for name in clean.select_dtypes(include=["datetime", "datetimetz"]).columns:
        clean[name] = clean[name].dt.strftime("%Y-%m-%d")
    return clean.astype(object).where(pd.notna(clean), None).to_dict("records")


def forecast_sales(sales, model, horizon, future_calendar=None):
    if model not in MODELS:
        raise ValueError("Неизвестная модель прогноза.")
    if isinstance(horizon, bool) or not isinstance(horizon, int) or not 7 <= horizon <= 56:
        raise ValueError("Горизонт должен быть целым числом от 7 до 56 дней.")
    if sales["date"].nunique() < 56 + horizon:
        raise ValueError("Недостаточно истории: нужны 56 обучающих дней и горизонт для проверки.")
    dates = pd.date_range(sales["date"].max(), periods=horizon + 1, freq="D")[1:]
    calendar = pd.DataFrame({"date": dates, "snap": 0, "event": 0})
    calendar_source = "SNAP и события будущих дат приняты равными 0; это допущение."
    if future_calendar is not None:
        provided = pd.DataFrame(future_calendar)
        if set(provided.columns) != {"date", "snap", "event"} or len(provided) != horizon:
            raise ValueError("Будущий календарь: ровно по одной строке date,snap,event на каждый день горизонта.")
        provided["date"] = pd.to_datetime(provided["date"], format="%Y-%m-%d", errors="coerce")
        if provided["date"].isna().any() or provided["date"].duplicated().any():
            raise ValueError("В будущем календаре неверные или повторные даты.")
        provided = provided.sort_values("date").reset_index(drop=True)
        if not provided["date"].equals(calendar["date"]):
            raise ValueError("Даты будущего календаря должны совпадать с горизонтом после конца истории.")
        for column in ["snap", "event"]:
            if not provided[column].isin([0, 1]).all():
                raise ValueError("SNAP и событие будущего календаря должны быть 0 или 1.")
        calendar = provided
        calendar_source = "Будущий календарь задан пользователем; предполагается известным при планировании."
    output = []
    for sku, group in sales.groupby("sku", sort=True):
        train = group.sort_values("date").reset_index(drop=True)
        plan = calendar.copy()
        plan["price"] = float(train.iloc[-1]["price"])
        predictions = predict_model(train, plan, model)
        for date, value in zip(dates, predictions):
            if not np.isfinite(value):
                raise ValueError("Модель вернула нечисловой прогноз.")
            output.append({"date": date.date().isoformat(), "sku": str(sku),
                           "prediction": float(max(0, value)), "price": float(plan.iloc[0]["price"])})
    assumptions = ["Прогнозируются наблюдаемые продажи, используемые как расчётный спрос.",
                   "Будущая цена равна последней цене истории.", calendar_source,
                   "Запасы, закупочная стоимость и влияние цены/промо задаются пользователем."]
    return output, assumptions


def evaluate_models(sales, horizon, windows=3):
    dates = sorted(sales["date"].unique())
    if isinstance(horizon, bool) or not isinstance(horizon, int) or not 7 <= horizon <= 56:
        raise ValueError("Горизонт должен быть целым числом от 7 до 56 дней.")
    count = min(windows, (len(dates) - 56) // horizon)
    if count < 1:
        raise ValueError("Недостаточно истории для временной проверки.")
    all_predictions, validations = [], []
    last_predictions = last_metrics = None
    for offset in range(count - 1, -1, -1):
        end = pd.Timestamp(dates[len(dates) - 1 - offset * horizon])
        current = sales[sales["date"] <= end]
        current_dates = sorted(current["date"].unique())
        cutoff = pd.Timestamp(current_dates[-horizon])
        rows = []
        for sku, group in current.groupby("sku", sort=True):
            train = group[group["date"] < cutoff].sort_values("date").reset_index(drop=True)
            test = group[group["date"] >= cutoff].sort_values("date")
            plan = test[["date", "snap", "event"]].copy()
            plan["price"] = float(train.iloc[-1]["price"])
            for model in MODELS:
                values = predict_model(train, plan, model)
                rows.extend(zip(test["date"], [str(sku)] * horizon, [model] * horizon, test["sales"], values))
        predictions = pd.DataFrame(rows, columns=["date", "sku", "model", "actual", "prediction"])
        scores = [{"sku": sku, "model": model, **calculate_metrics(group["actual"], group["prediction"])}
                  for (sku, model), group in predictions.groupby(["sku", "model"])]
        scores.extend({"sku": "ALL", "model": model, **calculate_metrics(group["actual"], group["prediction"])}
                      for model, group in predictions.groupby("model"))
        metrics = pd.DataFrame(scores)
        experiment = {"train_start": pd.Timestamp(current_dates[0]).date().isoformat(),
                      "train_end": pd.Timestamp(current_dates[-horizon - 1]).date().isoformat(),
                      "test_start": cutoff.date().isoformat(), "test_end": end.date().isoformat(),
                      "horizon_days": horizon, "validation": "single_fixed_origin_recursive_forecast"}
        validations.append({**experiment, "metrics": records(metrics[metrics["sku"] == "ALL"])})
        all_predictions.append(predictions)
        last_predictions, last_metrics = predictions, metrics
    pooled = pd.concat(all_predictions, ignore_index=True)
    overall = [{"model": model, "sku": "ALL", **calculate_metrics(group["actual"], group["prediction"])}
               for model, group in pooled.groupby("model")]
    return records(last_predictions), records(last_metrics), {
        "windows": validations, "rolling_metrics": records(pd.DataFrame(overall)),
        "method": f"Последовательных непересекающихся отложенных окон: {count}; фиксированное начало каждого прогноза.",
    }
