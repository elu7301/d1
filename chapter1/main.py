"""Анализ реальных продаж M5 и сравнение трёх простых прогнозов."""

import argparse
from pathlib import Path

from common.data import load_sales
from chapter1.analysis import summarize_sales, make_plots
from chapter1.forecast import compare_models


def main():
    project = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=project / "data" / "m5_sales.csv",
                        help="CSV с ежедневными продажами одного магазина")
    parser.add_argument("--horizon", type=int, default=28, help="Дней в проверочном периоде")
    parser.add_argument("--plots", action="store_true", help="Открыть графики на экране")
    args = parser.parse_args()

    try:
        sales = load_sales(args.data)
        predictions, metrics, experiment = compare_models(sales, args.horizon)
    except (ValueError, OSError) as error:
        parser.exit(2, f"Ошибка: {error}\n")

    summary, weekdays = summarize_sales(sales)
    print("Данные:", args.data.resolve())
    print("Обучение:", experiment["train_start"], "—", experiment["train_end"])
    print("Проверка:", experiment["test_start"], "—", experiment["test_end"])
    print("Цель: наблюдаемые продажи. Будущая цена равна последней цене обучения.")
    print("Фактические будущие продажи и цены в прогноз не подставляются.")
    print("\nПоказатели по товарам:")
    print(summary.round(3).to_string(index=False))
    print("\nСредние продажи по дням недели (0 — понедельник, 6 — воскресенье):")
    print(weekdays.round(2).to_string())
    print("\nОшибки прогнозов (ALL — все товары вместе):")
    print(metrics.round(3).to_string(index=False))

    if args.plots:
        import matplotlib.pyplot as plt
        make_plots(sales, predictions)
        plt.show()


if __name__ == "__main__":
    main()
