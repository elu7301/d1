"""SQLite: исходные продажи и неизменяемые снимки расчётов."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

import pandas as pd

from common.data import load_sales


SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, source TEXT NOT NULL,
    currency TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE,
    rows INTEGER NOT NULL, products INTEGER NOT NULL,
    start_date TEXT NOT NULL, end_date TEXT NOT NULL,
    quality_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sales (
    dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    date TEXT NOT NULL, sku TEXT NOT NULL, store_id TEXT NOT NULL,
    sales INTEGER NOT NULL CHECK(sales >= 0), price REAL NOT NULL CHECK(price > 0),
    snap INTEGER NOT NULL CHECK(snap IN (0,1)), event INTEGER NOT NULL CHECK(event IN (0,1)),
    PRIMARY KEY(dataset_id, date, sku)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY, dataset_id INTEGER NOT NULL REFERENCES datasets(id),
    name TEXT NOT NULL, model TEXT NOT NULL, model_version TEXT NOT NULL,
    horizon INTEGER NOT NULL, forecast_json TEXT NOT NULL, metrics_json TEXT NOT NULL,
    backtest_json TEXT NOT NULL, validation_json TEXT NOT NULL,
    assumptions_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scenarios (
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    name TEXT NOT NULL, is_baseline INTEGER NOT NULL DEFAULT 0,
    parameters_json TEXT NOT NULL, summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scenario_daily (
    scenario_id INTEGER NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    date TEXT NOT NULL, sku TEXT NOT NULL, values_json TEXT NOT NULL,
    PRIMARY KEY(scenario_id, date, sku)
);
CREATE INDEX IF NOT EXISTS ix_runs_dataset ON runs(dataset_id);
CREATE INDEX IF NOT EXISTS ix_scenarios_run ON scenarios(run_id);
PRAGMA user_version = 1;
"""


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


@contextmanager
def connect(path):
    database = sqlite3.connect(str(path), timeout=15)
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA foreign_keys = ON")
    try:
        with database:
            yield database
    finally:
        database.close()


def dataset_record(row):
    item = dict(row)
    item["quality"] = json.loads(item.pop("quality_json"))
    return item


def insert_dataset(database, data, name, source, currency):
    """Загрузка атомарна: некорректная таблица сюда не передаётся."""
    normalized = data.copy()
    normalized["date"] = normalized["date"].dt.strftime("%Y-%m-%d")
    if "store_id" not in normalized:
        normalized["store_id"] = "STORE_1"
    columns = ["date", "sku", "store_id", "sales", "price", "snap", "event"]
    # CSV формируется только в памяти для контрольной суммы; файл не создаётся.
    digest = hashlib.sha256((currency + normalized[columns].to_csv(index=False)).encode()).hexdigest()
    existing = database.execute("SELECT * FROM datasets WHERE fingerprint = ?", (digest,)).fetchone()
    if existing:
        return dataset_record(existing), False
    quality = {"missing_values": 0, "duplicates": 0, "complete_calendar": True,
               "zero_sales_days": int((data["sales"] == 0).sum()),
               "store_id": str(normalized["store_id"].iloc[0]),
               "stock_data_available": False, "target": "observed_sales"}
    cursor = database.execute(
        "INSERT INTO datasets(name,source,currency,fingerprint,rows,products,start_date,end_date,quality_json,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (name, source, currency, digest, len(data), int(data["sku"].nunique()),
         normalized["date"].min(), normalized["date"].max(), encode(quality), timestamp()))
    dataset_id = cursor.lastrowid
    values = [(dataset_id, str(row.date), str(row.sku), str(row.store_id), int(row.sales),
               float(row.price), int(row.snap), int(row.event))
              for row in normalized[columns].itertuples(index=False)]
    database.executemany("INSERT INTO sales VALUES(?,?,?,?,?,?,?,?)", values)
    return dataset_record(database.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()), True


def read_sales(database, dataset_id):
    rows = database.execute(
        "SELECT date,sku,store_id,sales,price,snap,event FROM sales WHERE dataset_id=? ORDER BY sku,date",
        (dataset_id,)).fetchall()
    data = pd.DataFrame([dict(row) for row in rows])
    if not data.empty:
        data["date"] = pd.to_datetime(data["date"])
    return data


def initialize(path, source_csv):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as database:
        database.execute("PRAGMA journal_mode = WAL")
        database.executescript(SCHEMA)
        # Реальный набор импортируется один раз. Файл-источник не изменяется.
        if not database.execute("SELECT id FROM datasets LIMIT 1").fetchone():
            insert_dataset(database, load_sales(source_csv), "Walmart M5 · CA_1 · 3 товара",
                           "Walmart M5, Zenodo 10.5281/zenodo.10203108", "USD")
