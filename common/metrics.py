"""Одинаковые показатели ошибки для сравнения любых прогнозов."""

import numpy as np


def calculate_metrics(actual, predicted):
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    error = predicted - actual
    denominator = np.abs(actual).sum()
    return {
        "MAE": float(np.abs(error).mean()),
        "RMSE": float(np.sqrt(np.mean(error ** 2))),
        "WAPE_percent": float(100 * np.abs(error).sum() / denominator) if denominator else np.nan,
        "bias_units": float(error.mean()),
    }
