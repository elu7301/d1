"""HTTP-интерфейс локальной системы: Flask + SQLite + JavaScript."""

import csv
import io
import json
from pathlib import Path
import secrets
import sqlite3

from flask import Flask, jsonify, render_template, request, Response
from werkzeug.exceptions import HTTPException

from common.data import load_sales
from common.database import connect, dataset_record, initialize, insert_dataset, read_sales
from common.forecasting import MODELS, records
from chapter3 import service


PROJECT = Path(__file__).resolve().parent.parent


def create_app(config=None):
    app = Flask(__name__, template_folder=str(PROJECT / "templates"), static_folder=str(PROJECT / "static"))
    app.config.update(DATABASE=PROJECT / "data" / "project.sqlite3", SOURCE_CSV=PROJECT / "data" / "m5_sales.csv",
                      MAX_CONTENT_LENGTH=10 * 1024 * 1024, CSRF_TOKEN=secrets.token_urlsafe(32),
                      JSON_SORT_KEYS=False)
    if config:
        app.config.update(config)
    app.json.ensure_ascii = False
    initialize(app.config["DATABASE"], app.config["SOURCE_CSV"])

    @app.before_request
    def protect_writes():
        if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
            token = request.headers.get("X-CSRF-Token", "")
            if not secrets.compare_digest(token, app.config["CSRF_TOKEN"]):
                return jsonify(error="Обновите страницу: отсутствует токен подтверждения запроса."), 403

    @app.after_request
    def response_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(Exception)
    def error_response(error):
        if isinstance(error, LookupError):
            return jsonify(error=str(error)), 404
        if isinstance(error, PermissionError):
            return jsonify(error=str(error)), 409
        if isinstance(error, (ValueError, TypeError, UnicodeError)):
            return jsonify(error=str(error) or "Некорректные параметры запроса."), 400
        if isinstance(error, HTTPException):
            message = {413: "Файл превышает ограничение 10 МБ.",
                       404: "Страница или запись не найдена.",
                       405: "Этот способ запроса не поддерживается."}.get(error.code, error.description)
            return jsonify(error=message), error.code
        if isinstance(error, sqlite3.Error):
            app.logger.exception("Ошибка SQLite")
            return jsonify(error="Не удалось выполнить операцию с базой данных. Повторите запрос."), 500
        app.logger.exception("Необработанная ошибка")
        return jsonify(error="Внутренняя ошибка приложения."), 500

    def body():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise ValueError("Ожидается JSON-объект с параметрами.")
        return value

    @app.get("/")
    def index():
        return render_template("index.html", csrf_token=app.config["CSRF_TOKEN"])

    @app.get("/api/health")
    def health():
        return jsonify(status="ok", database="sqlite", models=MODELS, mode="local_single_user")

    @app.get("/api/datasets")
    def datasets():
        with connect(app.config["DATABASE"]) as db:
            result = [dataset_record(row) for row in db.execute("SELECT * FROM datasets ORDER BY id DESC")]
        return jsonify(datasets=result)

    @app.post("/api/datasets")
    def upload_dataset():
        file = request.files.get("file")
        if file is None or not file.filename or not file.filename.lower().endswith(".csv"):
            raise ValueError("Выберите CSV-файл с продажами.")
        name = request.form.get("name", "").strip() or Path(file.filename).name
        if len(name) > 120:
            raise ValueError("Название набора должно содержать не более 120 символов.")
        currency = request.form.get("currency", "USD")
        if currency not in {"USD", "RUB", "EUR"}:
            raise ValueError("Валюта должна быть USD, RUB или EUR.")
        try:
            data = load_sales(io.StringIO(file.read().decode("utf-8-sig")))
        except (ValueError, UnicodeError) as error:
            raise ValueError(f"CSV не загружен: {error}") from None
        if len(data) > 50000 or data["sku"].nunique() > 20:
            raise ValueError("Для локального прототипа допустимы до 50 000 строк и до 20 товаров.")
        if data["date"].nunique() < 63:
            raise ValueError("Нужна история минимум за 63 дня: 56 для обучения и 7 для проверки.")
        with connect(app.config["DATABASE"]) as db:
            item, created = insert_dataset(db, data, name, "Пользовательский CSV", currency)
        return jsonify(dataset=item, created=created), 201 if created else 200

    @app.get("/api/datasets/<int:dataset_id>/preview")
    def preview(dataset_id):
        with connect(app.config["DATABASE"]) as db:
            item = dataset_record(service.require_row(db, "datasets", dataset_id))
            data = read_sales(db, dataset_id)
        return jsonify(rows=records(data.head(12)), quality=item["quality"], currency=item["currency"], dataset=item)

    @app.get("/api/runs")
    def runs():
        with connect(app.config["DATABASE"]) as db:
            rows = [dict(row) for row in db.execute(
                "SELECT r.id,r.name,r.dataset_id,d.name AS dataset_name,r.model,r.horizon,r.created_at "
                "FROM runs r JOIN datasets d ON d.id=r.dataset_id ORDER BY r.id DESC")]
        return jsonify(runs=rows)

    @app.post("/api/runs")
    def add_run():
        return jsonify(run=service.create_run(app.config["DATABASE"], body())), 201

    @app.get("/api/runs/<int:run_id>")
    def get_run(run_id):
        return jsonify(run=service.run_detail(app.config["DATABASE"], run_id))

    @app.post("/api/runs/<int:run_id>/scenarios")
    def add_scenario(run_id):
        return jsonify(scenario=service.create_scenario(app.config["DATABASE"], run_id, body())), 201

    @app.put("/api/scenarios/<int:scenario_id>")
    def edit_scenario(scenario_id):
        return jsonify(scenario=service.update_scenario(app.config["DATABASE"], scenario_id, body()))

    @app.delete("/api/scenarios/<int:scenario_id>")
    def remove_scenario(scenario_id):
        service.delete_scenario(app.config["DATABASE"], scenario_id)
        return jsonify(ok=True)

    @app.post("/api/runs/<int:run_id>/compare")
    def compare(run_id):
        return jsonify(service.compare_scenarios(app.config["DATABASE"], run_id, body().get("scenario_ids")))

    @app.post("/api/runs/<int:run_id>/sensitivity")
    def get_sensitivity(run_id):
        payload = body()
        points = service.sensitivity(app.config["DATABASE"], run_id, payload.get("scenario_id"),
                                     payload.get("demand_changes", [-20, -10, 0, 10, 20]))
        return jsonify(points=points)

    @app.get("/api/runs/<int:run_id>/export")
    def export(run_id):
        run = service.run_detail(app.config["DATABASE"], run_id)
        kind = request.args.get("kind", "comparison")
        if kind == "forecast":
            rows = run["forecast"]
        elif kind == "comparison":
            selected = request.args.get("scenario_ids")
            items = run["scenarios"]
            if selected is not None:
                ids = [service.identifier(value) for value in selected.split(",")]
                if not 1 <= len(ids) <= 8 or len(set(ids)) != len(ids):
                    raise ValueError("Для экспорта выберите от 1 до 8 разных сценариев.")
                lookup = {row["id"]: row for row in items}
                if any(value not in lookup for value in ids):
                    raise ValueError("Сценарий для экспорта не относится к этому исследованию.")
                items = [lookup[value] for value in ids]
            rows = [{"scenario_id": row["id"], "scenario_name": row["name"], "currency": run["currency"],
                     **{key: value for key, value in row["summary"].items() if not isinstance(value, (dict, list))}}
                    for row in items]
        elif kind == "daily":
            scenario_id = service.identifier(request.args.get("scenario_id"))
            matching = [row for row in run["scenarios"] if row["id"] == scenario_id]
            if not matching:
                raise LookupError("Сценарий не найден в этом исследовании.")
            rows = matching[0]["daily"]
        else:
            raise ValueError("Неизвестный вид экспорта.")
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(rows[0]))
        writer.writeheader()
        # Защищаем текстовые названия от выполнения формул в табличном редакторе.
        for row in rows:
            writer.writerow({key: "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")) else value
                             for key, value in row.items()})
        return Response("\ufeff" + output.getvalue(), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="run-{run_id}-{kind}.csv"'})

    return app
