"""Показатели продаж и чёрно-белые графики без сохранения файлов."""

from itertools import cycle


def summarize_sales(data):
    """Основные показатели по товарам и средние продажи по дням недели."""
    summary = data.groupby("sku").agg(
        days=("sales", "count"), total_sales=("sales", "sum"),
        mean_sales=("sales", "mean"), std_sales=("sales", "std"),
        zero_sales_share=("sales", lambda values: float((values == 0).mean())),
        mean_price=("price", "mean"),
    ).reset_index()
    weekdays = data.assign(weekday=data["date"].dt.dayofweek).groupby(
        ["weekday", "sku"]
    )["sales"].mean().unstack("sku")
    return summary, weekdays


def make_plots(data, predictions):
    """Возвращает рисунки; основной запуск показывает их только с --plots."""
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    _, weekdays = summarize_sales(data)
    history = data.pivot(index="date", columns="sku", values="sales").rolling(7).mean()
    figure_history, axis = plt.subplots(figsize=(10, 4))
    for sku, style in zip(history.columns, cycle(["-", "--", ":", "-."])):
        axis.plot(history.index, history[sku], color="black", linestyle=style, label=sku)
    axis.set(title="Продажи — среднее за 7 дней", xlabel="Дата", ylabel="Единиц в день")
    axis.legend(fontsize=8)
    axis.grid(alpha=0.2)
    figure_history.tight_layout()

    axis = weekdays.plot.bar(figsize=(9, 4), rot=0, color="white", edgecolor="black")
    for bars, hatch in zip(axis.containers, cycle(["", "///", "...", "xxx"])):
        for bar in bars:
            bar.set_hatch(hatch)
    axis.legend(title="Товар", fontsize=8)
    axis.set_xticklabels(["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"])
    axis.set(title="Средние продажи по дням недели", xlabel="День недели", ylabel="Единиц в день")
    figure_weekdays = axis.figure
    figure_weekdays.tight_layout()

    products = sorted(predictions["sku"].unique())
    figure_forecasts, axes = plt.subplots(len(products), 1, figsize=(10, 3 * len(products)), squeeze=False)
    labels = {"mean7": "Среднее за 7 дней", "seasonal_naive": "Сезонный наивный", "linear_ridge": "Линейная модель"}
    styles = {"mean7": ":", "seasonal_naive": "--", "linear_ridge": "-."}
    for axis, sku in zip(axes[:, 0], products):
        group = predictions[predictions["sku"] == sku]
        actual = group.drop_duplicates("date")
        axis.plot(actual["date"], actual["actual"], color="black", label="Факт", linewidth=1.8)
        for model, values in group.groupby("model"):
            axis.plot(values["date"], values["prediction"], color="black",
                      linestyle=styles[model], label=labels[model], linewidth=1.2)
        axis.set(title=sku, ylabel="Продажи, ед.")
        axis.grid(alpha=0.2)
        axis.legend(fontsize=8, ncol=2)
    figure_forecasts.autofmt_xdate()
    figure_forecasts.tight_layout()
    return figure_history, figure_weekdays, figure_forecasts
