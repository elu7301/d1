"""Загрузка и проверка ежедневных продаж. Пригодится во всех главах."""

import numpy as np
import pandas as pd


def load_sales(path):
    data = pd.read_csv(path, dtype={"sku": "string"})
    return validate_sales(data)


def validate_sales(data):
    """Проверяет таблицу в памяти и возвращает её в хронологическом порядке."""
    data = data.copy()
    if "sku" in data:
        data["sku"] = data["sku"].astype("string")
    required = ["date", "sku", "sales", "price", "snap", "event"]
    missing = set(required) - set(data.columns)
    if missing:
        raise ValueError(f"В CSV отсутствуют столбцы: {', '.join(sorted(missing))}.")
    if data.empty:
        raise ValueError("CSV не содержит строк.")
    if data[required].isna().any().any():
        raise ValueError("В обязательных столбцах есть пропуски. Сначала выясните их причину.")
    data["sku"] = data["sku"].str.strip()
    if data["sku"].eq("").any() or data["sku"].eq("ALL").any():
        raise ValueError("Пустой код товара и служебный код ALL недопустимы.")
    data["date"] = pd.to_datetime(data["date"], format="%Y-%m-%d", errors="coerce")
    if data["date"].isna().any():
        raise ValueError("Даты должны быть записаны как ГГГГ-ММ-ДД.")
    numeric = ["sales", "price", "snap", "event"]
    for column in numeric:
        data[column] = pd.to_numeric(data[column], errors="coerce")
        if not np.isfinite(data[column].to_numpy(dtype=float)).all():
            raise ValueError(f"Столбец {column} содержит пропуск или некорректное число.")
    if (data["sales"] < 0).any() or (data["sales"] % 1 != 0).any():
        raise ValueError("sales — неотрицательное целое число проданных единиц.")
    if (data["price"] <= 0).any():
        raise ValueError("price должна быть положительной.")
    for column in ["snap", "event"]:
        if not data[column].isin([0, 1]).all():
            raise ValueError(f"{column} должен содержать только 0 или 1.")
    if "store_id" in data and (data["store_id"].isna().any() or data["store_id"].nunique() != 1):
        raise ValueError("В одном файле должны быть данные одного магазина.")
    if data.duplicated(["date", "sku"]).any():
        raise ValueError("Есть повторные строки для одной пары date + sku.")
    calendar = pd.date_range(data["date"].min(), data["date"].max(), freq="D")
    for sku, group in data.groupby("sku"):
        if len(group) != len(calendar) or not calendar.isin(group["date"]).all():
            raise ValueError(f"У товара {sku} неполный дневной календарь. Пропуск не равен нулю продаж.")
    return data.sort_values(["sku", "date"]).reset_index(drop=True)
