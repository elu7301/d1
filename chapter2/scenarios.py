"""Дневной расчёт сценария: прогноз → продажи, запасы, дефицит и затраты.

Это детерминированная модель ожидаемых значений, без генерации случайных данных.
Ценовая эластичность, закупочные цены и правила поставок задаются пользователем.
"""

from collections import defaultdict
from datetime import date, timedelta
import math


# Значение по умолчанию, минимум, максимум, целочисленность.
PARAMETERS = {
    "demand_change_pct": (0, -80, 200, False),
    "price_change_pct": (0, -50, 100, False),
    "elasticity": (1, 0, 5, False),
    "promo_uplift_pct": (0, 0, 200, False),
    "initial_stock_days": (7, 0, 90, False),
    "target_stock_days": (14, 1, 90, False),
    "lead_time_days": (3, 1, 30, True),
    "supply_delay_days": (0, 0, 30, True),
    "review_period_days": (7, 1, 14, True),
    "unit_cost_ratio": (0.65, 0.05, 0.95, False),
    "holding_cost_rate_daily": (0.001, 0, 0.02, False),
    "order_fixed_cost": (2, 0, 10000, False),
    "promo_budget": (0, 0, 100000, False),
}


def validate_parameters(params):
    """Дополняет пропущенные параметры значениями по умолчанию и проверяет их."""
    if not isinstance(params, dict):
        raise ValueError("Параметры сценария должны быть объектом.")
    unknown = set(params) - set(PARAMETERS) - {"name"}
    if unknown:
        raise ValueError("Неизвестные параметры сценария: " + ", ".join(sorted(unknown)))
    name = params.get("name", "Базовый сценарий")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
        raise ValueError("Название сценария должно содержать от 1 до 80 символов.")
    clean = {"name": name.strip()}
    for key, (default, minimum, maximum, integer) in PARAMETERS.items():
        value = params.get(key, default)
        try:
            if isinstance(value, bool):
                raise ValueError
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f"Параметр {key} должен быть числом.") from None
        if not math.isfinite(number) or not minimum <= number <= maximum:
            raise ValueError(f"Параметр {key} должен быть от {minimum} до {maximum}.")
        if integer and not number.is_integer():
            raise ValueError(f"Параметр {key} должен быть целым числом дней.")
        clean[key] = int(number) if integer else number
    return clean


def _validate_forecast(forecast):
    """Группирует прогноз по товарам; пропуски дней не превращает в нули."""
    if not isinstance(forecast, list) or not forecast:
        raise ValueError("Для сценария нужен непустой список строк прогноза.")
    products = defaultdict(list)
    seen = set()
    for row in forecast:
        if not isinstance(row, dict) or not {"date", "sku", "prediction", "price"} <= set(row):
            raise ValueError("В прогнозе нужны date, sku, prediction и price.")
        try:
            day = date.fromisoformat(row["date"])
            if day.isoformat() != row["date"]:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("Дата прогноза должна быть в формате ГГГГ-ММ-ДД.") from None
        sku = row["sku"]
        if not isinstance(sku, str) or not sku.strip() or sku.strip() == "ALL":
            raise ValueError("Код товара должен быть непустой строкой и отличаться от ALL.")
        sku = sku.strip()
        values = {}
        for key in ["prediction", "price"]:
            try:
                if isinstance(row[key], bool):
                    raise ValueError
                number = float(row[key])
            except (TypeError, ValueError):
                raise ValueError(f"Поле {key} в прогнозе должно быть числом.") from None
            if not math.isfinite(number) or number < 0 or (key == "price" and number == 0):
                raise ValueError("Прогноз должен быть неотрицательным, цена — положительной; без NaN и бесконечностей.")
            values[key] = number
        if (sku, day) in seen:
            raise ValueError("В прогнозе есть повторные строки для одного товара и дня.")
        seen.add((sku, day))
        products[sku].append({"date": day, **values})
    reference_dates = None
    for rows in products.values():
        rows.sort(key=lambda row: row["date"])
        days = [row["date"] for row in rows]
        if any(right - left != timedelta(days=1) for left, right in zip(days, days[1:])):
            raise ValueError("В прогнозе должен быть полный дневной календарь без пропусков.")
        if reference_dates is not None and days != reference_dates:
            raise ValueError("Все товары должны иметь одинаковый календарь прогноза.")
        reference_dates = days
    return products


def simulate_scenario(forecast, params):
    """Считает альтернативу по одному и тому же исходному прогнозу.

    Начальный запас = средний базовый прогноз × initial_stock_days.
    Уровень пополнения = средний спрос сценария × target_stock_days.
    Заказ до этого уровня учитывает остаток и весь товар в пути.
    Дневной порядок: поступление → пересмотр заказа → продажа → хранение.
    """
    params = validate_parameters(params)
    products = _validate_forecast(forecast)
    price_factor = 1 + params["price_change_pct"] / 100
    demand_factor = ((1 + params["demand_change_pct"] / 100)
                     * price_factor ** (-params["elasticity"])
                     * (1 + params["promo_uplift_pct"] / 100))
    lead_time = params["lead_time_days"] + params["supply_delay_days"]
    horizon = len(next(iter(products.values())))
    # Бюджет промо относится ко всему сценарию, а не к каждому товару отдельно.
    daily_promo = params["promo_budget"] / (horizon * len(products))
    daily = []
    initial_units = initial_value = ending_units = ending_value = 0.0
    pipeline_units = pipeline_value = procurement_spend = 0.0
    orders_count = 0

    for sku, rows in sorted(products.items()):
        mean_forecast = math.fsum(row["prediction"] for row in rows) / horizon
        stock = mean_forecast * params["initial_stock_days"]
        target = mean_forecast * demand_factor * params["target_stock_days"]
        # Закупочная цена фиксирована от исходной первой цены, не от новой цены продажи.
        unit_cost = rows[0]["price"] * params["unit_cost_ratio"]
        if not all(math.isfinite(value) for value in [stock, target, unit_cost]):
            raise ValueError("Слишком большие значения прогноза или цены для расчёта.")
        initial_units += stock
        initial_value += stock * unit_cost
        pipeline = []  # Пары (номер дня поступления, количество).
        for index, row in enumerate(rows):
            opening_stock = stock
            received = math.fsum(quantity for due, quantity in pipeline if due == index)
            pipeline = [(due, quantity) for due, quantity in pipeline if due > index]
            stock += received
            ordered = ordering_cost = 0.0
            if index % params["review_period_days"] == 0:
                position = stock + math.fsum(quantity for _, quantity in pipeline)
                ordered = max(0.0, target - position)
                if ordered > 1e-9:
                    pipeline.append((index + lead_time, ordered))
                    procurement_spend += ordered * unit_cost
                    ordering_cost = params["order_fixed_cost"]
                    orders_count += 1
                else:
                    ordered = 0.0
            demand = row["prediction"] * demand_factor
            sales = min(demand, stock)
            lost_sales = max(0.0, demand - sales)
            stock = max(0.0, stock - sales)
            price = row["price"] * price_factor
            revenue = sales * price
            cogs = sales * unit_cost
            holding_cost = stock * unit_cost * params["holding_cost_rate_daily"]
            profit = revenue - cogs - holding_cost - ordering_cost - daily_promo
            entry = {
                "date": row["date"].isoformat(), "sku": sku,
                "demand": demand, "sales": sales, "lost_sales": lost_sales,
                "opening_stock": opening_stock, "received": received, "ordered": ordered,
                "closing_stock": stock, "pipeline": math.fsum(quantity for _, quantity in pipeline),
                "price": price, "revenue": revenue, "cogs": cogs,
                "holding_cost": holding_cost, "ordering_cost": ordering_cost,
                "promo_cost": daily_promo, "profit": profit,
            }
            if not all(math.isfinite(value) for value in entry.values() if isinstance(value, (int, float))):
                raise ValueError("Слишком большие значения для финансового расчёта.")
            daily.append(entry)
        ending_units += stock
        ending_value += stock * unit_cost
        remaining = math.fsum(quantity for _, quantity in pipeline)
        pipeline_units += remaining
        pipeline_value += remaining * unit_cost

    total = lambda key: math.fsum(row[key] for row in daily)
    demand = total("demand")
    sales = total("sales")
    summary = {
        "name": params["name"], "horizon_days": horizon, "products": len(products),
        "total_demand": demand, "sales_units": sales, "lost_sales_units": total("lost_sales"),
        "service_level": sales / demand if demand else 1.0,
        "stockout_days": sum(row["lost_sales"] > 1e-9 for row in daily),
        "initial_stock_units": initial_units, "initial_stock_value": initial_value,
        "received_units": total("received"), "ordered_units": total("ordered"),
        "ending_stock_units": ending_units, "ending_stock_value": ending_value,
        "pipeline_units": pipeline_units, "pipeline_value": pipeline_value,
        "revenue": total("revenue"), "cost_of_goods_sold": total("cogs"),
        "holding_cost": total("holding_cost"), "ordering_cost": total("ordering_cost"),
        "promo_cost": total("promo_cost"), "profit": total("profit"),
        "procurement_spend": procurement_spend, "orders_count": orders_count,
        "stock_balance": initial_units + total("received") - sales - ending_units,
        "assumptions": {
            "demand": "Прогноз наблюдаемых продаж используется как базовый ожидаемый спрос; потерянный спрос не восстановлен.",
            "price_response": "Степенная реакция на цену с заданной пользователем эластичностью; причинный эффект не оценивался.",
            "unit_cost": "Фиксированная доля исходной первой цены каждого товара; сценарная розничная цена её не изменяет.",
            "initial_stock": "Средний исходный прогноз за горизонт × заданные дни запаса, без сценарного роста спроса.",
            "initial_pipeline": "На начало горизонта ранее размещённых заказов нет; новые поставки формируются политикой пополнения.",
            "replenishment": "До среднего спроса сценария × целевые дни покрытия, с учётом всего товара в пути.",
            "delivery": "Поступление в начале дня через срок поставки плюс задержка; неудовлетворённый спрос теряется, не переносится в заказы покупателей.",
            "financial_result": "Выручка минус себестоимость проданного, хранение, оформление заказов и промобюджет; без налогов и постоянных затрат.",
            "procurement": "Стоимость всех размещённых заказов, включая ещё не поступившие; не вычитается из прибыли повторно.",
            "stockout_days": "Число пар товар — день с неудовлетворённым расчётным спросом.",
            "precision": "Дробные значения — ожидаемые единицы, без округления до упаковок и без случайной генерации.",
        },
    }
    if not all(math.isfinite(value) for value in summary.values() if isinstance(value, (int, float))):
        raise ValueError("Слишком большие значения для итоговых показателей.")
    return {"summary": summary, "daily": sorted(daily, key=lambda row: (row["date"], row["sku"]))}
