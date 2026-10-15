"""Три простых прогноза и проверка на будущем временном отрезке."""

import numpy as np
import pandas as pd

from common.metrics import calculate_metrics


def make_features(date, price, snap, event, history, start_date):
    """Прошлые продажи, цена и заранее известные календарные признаки."""
    angle = 2 * np.pi * date.dayofyear / 365.25
    return [
        (date - start_date).days / 365.25, price, snap, event, np.sin(angle), np.cos(angle),
        *[int(date.dayofweek == day) for day in range(1, 7)],
        history[-7], history[-14], np.mean(history[-7:]), np.mean(history[-28:]),
    ]


def fit_linear_model(train, alpha=1.0):
    """Ridge-регрессия: линейная модель со штрафом на слишком большие веса."""
    history = train["sales"].astype(float).tolist()
    start_date = train.iloc[0]["date"]
    rows = []
    for index in range(28, len(train)):
        row = train.iloc[index]
        rows.append(make_features(row.date, row.price, row.snap, row.event, history[:index], start_date))
    features = np.asarray(rows, dtype=float)
    target = np.asarray(history[28:])
    # Среднее и масштаб вычисляются только по обучающему периоду.
    center = features.mean(axis=0)
    scale = features.std(axis=0)
    scale[scale == 0] = 1
    design = np.column_stack([np.ones(len(features)), (features - center) / scale])
    penalty = np.sqrt(alpha) * np.eye(design.shape[1])
    penalty[0, 0] = 0  # Свободный член не штрафуем.
    # Добавленные строки эквивалентны L2-штрафу. Не обращаем матрицу вручную.
    weights = np.linalg.lstsq(
        np.vstack([design, penalty]),
        np.concatenate([target, np.zeros(design.shape[1])]), rcond=None
    )[0]
    return weights, center, scale, start_date


def predict_models(train, future_plan):
    """В future_plan дата, предположение о цене, SNAP и событие; без sales."""
    history = train["sales"].astype(float).tolist()
    horizon = len(future_plan)
    mean7 = np.repeat(np.mean(history[-7:]), horizon)
    seasonal_naive = np.resize(np.asarray(history[-7:]), horizon)
    weights, center, scale, start_date = fit_linear_model(train)
    linear = []
    for row in future_plan.itertuples(index=False):
        features = np.asarray(make_features(row.date, row.price, row.snap, row.event, history, start_date))
        prediction = max(0.0, float(np.r_[1.0, (features - center) / scale] @ weights))
        linear.append(prediction)
        # Для следующих дней используем свои прогнозы, а не продажи теста.
        history.append(prediction)
    return {"mean7": mean7, "seasonal_naive": seasonal_naive, "linear_ridge": np.asarray(linear)}


def compare_models(sales, horizon=28):
    if not isinstance(horizon, int) or horizon < 1:
        raise ValueError("Горизонт должен быть положительным целым числом.")
    dates = sorted(sales["date"].unique())
    if len(dates) < horizon + 56:
        raise ValueError("Нужно хотя бы 56 обучающих дней и заданное число проверочных дней.")
    cutoff = pd.Timestamp(dates[-horizon])
    rows = []
    for sku, group in sales.groupby("sku", sort=True):
        group = group.sort_values("date").reset_index(drop=True)
        train = group[group["date"] < cutoff].copy()
        test = group[group["date"] >= cutoff].copy()
        if len(train) < 56 or len(test) != horizon:
            raise ValueError(f"У товара {sku} недостаточно данных для общего периода проверки.")
        # Фактическую цену будущей недели заранее не знаем.
        # На весь горизонт сохраняем последнюю цену обучения.
        plan = test[["date", "snap", "event"]].copy()
        plan["price"] = train.iloc[-1]["price"]
        for model, values in predict_models(train, plan).items():
            for date, actual, prediction in zip(test["date"], test["sales"], values):
                rows.append([date, sku, model, actual, prediction])
    predictions = pd.DataFrame(rows, columns=["date", "sku", "model", "actual", "prediction"])
    metrics = []
    for (sku, model), group in predictions.groupby(["sku", "model"], sort=True):
        metrics.append({"sku": sku, "model": model,
                        **calculate_metrics(group["actual"], group["prediction"])})
    # ALL — по всем наблюдениям, а не среднее процентов разных товаров.
    for model, group in predictions.groupby("model", sort=True):
        metrics.append({"sku": "ALL", "model": model,
                        **calculate_metrics(group["actual"], group["prediction"])})
    experiment = {
        "horizon_days": horizon, "ridge_alpha": 1.0,
        "train_start": pd.Timestamp(dates[0]).date().isoformat(),
        "train_end": pd.Timestamp(dates[-horizon - 1]).date().isoformat(),
        "test_start": cutoff.date().isoformat(),
        "test_end": pd.Timestamp(dates[-1]).date().isoformat(),
        "validation": "single_fixed_origin_recursive_forecast",
        "future_price": "last_training_price",
        "target": "observed_sales",
    }
    return predictions, pd.DataFrame(metrics), experiment
