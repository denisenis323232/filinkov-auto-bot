from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS imports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  filename TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  total_rows INTEGER NOT NULL DEFAULT 0,
  accepted_rows INTEGER NOT NULL DEFAULT 0,
  manual_rows INTEGER NOT NULL DEFAULT 0,
  rejected_rows INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cars (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_key TEXT NOT NULL UNIQUE,
  import_id INTEGER,
  source_url TEXT,
  model TEXT,
  inventory_no TEXT,
  color TEXT,
  trim TEXT,
  year INTEGER,
  month_text TEXT,
  mileage_km INTEGER,
  condition_text TEXT,
  source_status TEXT,
  power_hp INTEGER,
  price_cny REAL,
  price_usd REAL,
  cny_rub REAL,
  customs_rub REAL,
  final_price_rub REAL,
  status TEXT NOT NULL DEFAULT 'NEW',
  reject_reason TEXT,
  post_text TEXT,
  voice_file_id TEXT,
  media_json TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  published_at TEXT,
  FOREIGN KEY(import_id) REFERENCES imports(id)
);
CREATE INDEX IF NOT EXISTS idx_cars_status ON cars(status);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self.connect() as con:
            row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def create_import(self, filename: str) -> int:
        with self.connect() as con:
            cur = con.execute("INSERT INTO imports(filename) VALUES(?)", (filename,))
            return int(cur.lastrowid)

    def update_import_counts(self, import_id: int, total: int, accepted: int, manual: int, rejected: int) -> None:
        with self.connect() as con:
            con.execute(
                "UPDATE imports SET total_rows=?, accepted_rows=?, manual_rows=?, rejected_rows=? WHERE id=?",
                (total, accepted, manual, rejected, import_id),
            )

    def upsert_car(self, car: dict[str, Any]) -> int:
        cols = [
            "source_key", "import_id", "source_url", "model", "inventory_no", "color", "trim", "year", "month_text",
            "mileage_km", "condition_text", "source_status", "power_hp", "price_cny", "price_usd", "status", "reject_reason", "post_text",
        ]
        vals = [car.get(c) for c in cols]
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c not in {"source_key", "status"})
        sql = (
            f"INSERT INTO cars({','.join(cols)}) VALUES({placeholders}) "
            f"ON CONFLICT(source_key) DO UPDATE SET {updates}, updated_at=CURRENT_TIMESTAMP"
        )
        with self.connect() as con:
            con.execute(sql, vals)
            row = con.execute("SELECT id FROM cars WHERE source_key=?", (car["source_key"],)).fetchone()
            return int(row["id"])

    def get_car(self, car_id: int):
        with self.connect() as con:
            return con.execute("SELECT * FROM cars WHERE id=?", (car_id,)).fetchone()

    def list_cars(self, statuses: Iterable[str] = ("READY",), limit: int = 20):
        statuses = tuple(statuses)
        q = ",".join("?" for _ in statuses)
        with self.connect() as con:
            return con.execute(
                f"SELECT * FROM cars WHERE status IN ({q}) ORDER BY id LIMIT ?",
                (*statuses, limit),
            ).fetchall()

    def count_by_status(self):
        with self.connect() as con:
            rows = con.execute("SELECT status, COUNT(*) n FROM cars GROUP BY status ORDER BY status").fetchall()
            return {r["status"]: r["n"] for r in rows}

    def set_car_status(self, car_id: int, status: str, reject_reason: str | None = None):
        with self.connect() as con:
            con.execute(
                "UPDATE cars SET status=?, reject_reason=COALESCE(?,reject_reason), updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, reject_reason, car_id),
            )

    def set_car_text(self, car_id: int, text: str):
        with self.connect() as con:
            con.execute("UPDATE cars SET post_text=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (text, car_id))

    def set_customs(self, car_id: int, customs_rub: float, cny_rub: float, final_price: float):
        with self.connect() as con:
            con.execute(
                "UPDATE cars SET customs_rub=?, cny_rub=?, final_price_rub=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (customs_rub, cny_rub, final_price, car_id),
            )

    def set_media(self, car_id: int, media_urls: list[str]):
        with self.connect() as con:
            con.execute(
                "UPDATE cars SET media_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (json.dumps(media_urls, ensure_ascii=False), car_id),
            )

    def set_voice(self, car_id: int, file_id: str):
        with self.connect() as con:
            con.execute("UPDATE cars SET voice_file_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (file_id, car_id))

    def mark_published(self, car_id: int):
        with self.connect() as con:
            con.execute(
                "UPDATE cars SET status='PUBLISHED', published_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (car_id,),
            )
