"""Прикладной пайплайн: реальные данные → прогноз → сценарии → сравнение."""

import json

from common.database import connect, dataset_record, encode, read_sales, timestamp
from common.forecasting import MODELS, evaluate_models, forecast_sales, records
from chapter2.scenarios import simulate_scenario, validate_parameters


def identifier(value):
    if isinstance(value, bool):
        raise ValueError("Некорректный идентификатор.")
    try:
        parsed = int(value)
    except (ValueError, TypeError):
        raise ValueError("Некорректный идентификатор.") from None
    if parsed <= 0 or str(parsed) != str(value):
        raise ValueError("Некорректный идентификатор.")
    return parsed


def require_row(database, table, item_id):
    # Название таблицы выбирается только кодом приложения, не пользователем.
    if table not in {"datasets", "runs", "scenarios"}:
        raise ValueError("Неизвестная таблица.")
    row = database.execute(f"SELECT * FROM {table} WHERE id=?", (identifier(item_id),)).fetchone()
    if row is None:
        raise LookupError("Запись не найдена.")
    return row


def scenario_record(database, row, include_daily=True):
    item = dict(row)
    item["parameters"] = json.loads(item.pop("parameters_json"))
    item["summary"] = json.loads(item.pop("summary_json"))
    item["is_baseline"] = bool(item["is_baseline"])
    if include_daily:
        item["daily"] = [json.loads(value[0]) for value in database.execute(
            "SELECT values_json FROM scenario_daily WHERE scenario_id=? ORDER BY date,sku", (item["id"],))]
    return item


def save_scenario(database, run_id, parameters, forecast, baseline=False, scenario_id=None):
    result = simulate_scenario(forecast, parameters)
    params = validate_parameters(parameters)
    now = timestamp()
    if scenario_id is None:
        scenario_id = database.execute(
            "INSERT INTO scenarios(run_id,name,is_baseline,parameters_json,summary_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (run_id, params["name"], int(baseline), encode(params), encode(result["summary"]), now, now)).lastrowid
    else:
        database.execute("UPDATE scenarios SET name=?,parameters_json=?,summary_json=?,updated_at=? WHERE id=?",
                         (params["name"], encode(params), encode(result["summary"]), now, scenario_id))
        database.execute("DELETE FROM scenario_daily WHERE scenario_id=?", (scenario_id,))
    database.executemany("INSERT INTO scenario_daily VALUES(?,?,?,?)",
                         [(scenario_id, row["date"], row["sku"], encode(row)) for row in result["daily"]])
    return scenario_record(database, require_row(database, "scenarios", scenario_id))


def run_detail(path, run_id):
    with connect(path) as database:
        row = dict(require_row(database, "runs", run_id))
        dataset = dataset_record(require_row(database, "datasets", row["dataset_id"]))
        for column, key in [("forecast_json", "forecast"), ("metrics_json", "metrics"),
                            ("backtest_json", "backtest"), ("assumptions_json", "assumptions")]:
            row[key] = json.loads(row.pop(column))
        validation = json.loads(row.pop("validation_json"))
        row["validation_windows"] = validation["windows"]
        row["rolling_metrics"] = validation["rolling_metrics"]
        row["validation_method"] = validation["method"]
        row["future_calendar"] = validation.get("future_calendar", [])
        row["future_calendar_supplied"] = validation.get("future_calendar_supplied", False)
        row["dataset_name"] = dataset["name"]
        row["dataset_fingerprint"] = dataset["fingerprint"]
        row["currency"] = dataset["currency"]
        data = read_sales(database, dataset["id"])
        dates = sorted(data["date"].unique())
        cutoff = dates[-min(180, len(dates))]
        row["history"] = records(data.loc[data["date"] >= cutoff, ["date", "sku", "sales"]])
        row["scenarios"] = [scenario_record(database, item) for item in database.execute(
            "SELECT * FROM scenarios WHERE run_id=? ORDER BY is_baseline DESC,id", (row["id"],))]
        return row


def create_run(path, payload):
    dataset_id = identifier(payload.get("dataset_id"))
    model = payload.get("model", "linear_ridge")
    horizon = payload.get("horizon", 28)
    if not isinstance(model, str) or model not in MODELS:
        raise ValueError("Выберите доступную модель прогноза.")
    name = payload.get("name", "Новое исследование")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
        raise ValueError("Название исследования: от 1 до 120 символов.")
    with connect(path) as database:
        require_row(database, "datasets", dataset_id)
        data = read_sales(database, dataset_id)
    forecast, assumptions = forecast_sales(data, model, horizon, payload.get("future_calendar"))
    backtest, metrics, validation = evaluate_models(data, horizon)
    supplied = payload.get("future_calendar")
    validation["future_calendar_supplied"] = supplied is not None
    validation["future_calendar"] = sorted(supplied, key=lambda row: row["date"]) if supplied is not None else [
        {"date": date, "snap": 0, "event": 0} for date in sorted({row["date"] for row in forecast})]
    with connect(path) as database:
        run_id = database.execute(
            "INSERT INTO runs(dataset_id,name,model,model_version,horizon,forecast_json,metrics_json,"
            "backtest_json,validation_json,assumptions_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (dataset_id, name.strip(), model, MODELS[model]["version"], horizon, encode(forecast),
             encode(metrics), encode(backtest), encode(validation), encode(assumptions), timestamp())).lastrowid
        save_scenario(database, run_id, {"name": "Базовый сценарий"}, forecast, baseline=True)
    return run_detail(path, run_id)


def create_scenario(path, run_id, parameters):
    with connect(path) as database:
        run = require_row(database, "runs", run_id)
        return save_scenario(database, run["id"], parameters, json.loads(run["forecast_json"]))


def update_scenario(path, scenario_id, parameters):
    with connect(path) as database:
        scenario = require_row(database, "scenarios", scenario_id)
        if scenario["is_baseline"]:
            raise PermissionError("Базовый сценарий зафиксирован. Создайте отдельный вариант.")
        run = require_row(database, "runs", scenario["run_id"])
        return save_scenario(database, run["id"], parameters, json.loads(run["forecast_json"]),
                             scenario_id=scenario["id"])


def delete_scenario(path, scenario_id):
    with connect(path) as database:
        scenario = require_row(database, "scenarios", scenario_id)
        if scenario["is_baseline"]:
            raise PermissionError("Базовый сценарий удалить нельзя.")
        database.execute("DELETE FROM scenarios WHERE id=?", (scenario["id"],))


def compare_scenarios(path, run_id, scenario_ids):
    if not isinstance(scenario_ids, list) or not 2 <= len(scenario_ids) <= 8:
        raise ValueError("Для сравнения выберите от 2 до 8 сценариев.")
    ids = [identifier(value) for value in scenario_ids]
    if len(set(ids)) != len(ids):
        raise ValueError("Сценарии для сравнения не должны повторяться.")
    with connect(path) as database:
        run = require_row(database, "runs", run_id)
        baseline = database.execute("SELECT * FROM scenarios WHERE run_id=? AND is_baseline=1", (run["id"],)).fetchone()
        baseline_summary = json.loads(baseline["summary_json"])
        output = []
        for scenario_id in ids:
            row = require_row(database, "scenarios", scenario_id)
            if row["run_id"] != run["id"]:
                raise ValueError("Сравнивать можно только сценарии одного исследования.")
            item = scenario_record(database, row)
            item["delta_profit"] = item["summary"]["profit"] - baseline_summary["profit"]
            output.append(item)
        return {"scenarios": output, "baseline_id": baseline["id"],
                "note": "Финансовые результаты рассчитаны при заданных допущениях; автоматического выбора решения нет."}


def sensitivity(path, run_id, scenario_id, changes):
    if not isinstance(changes, list) or not 2 <= len(changes) <= 21:
        raise ValueError("Задайте от 2 до 21 значения изменения спроса.")
    with connect(path) as database:
        run = require_row(database, "runs", run_id)
        scenario = require_row(database, "scenarios", scenario_id)
        if scenario["run_id"] != run["id"]:
            raise ValueError("Сценарий относится к другому исследованию.")
        params = json.loads(scenario["parameters_json"])
        forecast = json.loads(run["forecast_json"])
    points = []
    for change in changes:
        summary = simulate_scenario(forecast, {**params, "demand_change_pct": change})["summary"]
        points.append({"demand_change_pct": float(change), **{key: summary[key] for key in
                       ["profit", "lost_sales_units", "ending_stock_units", "service_level"]}})
    return points
