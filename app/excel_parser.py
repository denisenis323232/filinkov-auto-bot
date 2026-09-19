from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

HEADER_ALIASES = {
    "url": ["Ссылка на сайт", "ссылка", "url"],
    "model": ["Модель автомобиля", "модель"],
    "inventory": ["Номер", "номер"],
    "color": ["Цвет кузова", "цвет"],
    "trim": ["Конфигурация", "комплектация"],
    "year": ["Год выпуска", "год"],
    "mileage": ["Пробег"],
    "condition": ["Состояние автомобиля", "состояние"],
    "source_status": ["Триангуляция ( тройной тест)", "статус"],
    "power": ["Л.с.", "л.с", "мощность"],
    "price_cny": ["Цена RMB", "rmb", "цена cny"],
    "price_usd": ["Цена в  $", "цена в $", "usd"],
}


def _norm(s: object) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _find_col(columns, aliases):
    normalized = {_norm(c): c for c in columns}
    for alias in aliases:
        a = _norm(alias)
        if a in normalized:
            return normalized[a]
    for c in columns:
        n = _norm(c)
        if any(_norm(a) in n for a in aliases):
            return c
    return None


def _int_from(value) -> int | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    m = re.search(r"\d+", str(value).replace(" ", ""))
    return int(m.group()) if m else None


def _float_from(value) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except Exception:
        m = re.search(r"\d+(?:[.,]\d+)?", str(value).replace(" ", ""))
        return float(m.group().replace(",", ".")) if m else None


def _year_month(value) -> tuple[int | None, str | None]:
    if value is None:
        return None, None
    text = str(value).strip()
    m = re.search(r"(20\d{2})", text)
    return (int(m.group(1)) if m else None, text or None)


def _source_key(url: str | None, inventory: str | None, row_no: int) -> str:
    if url:
        m = re.search(r"car_\d+/(\d+)\.html", url)
        if m:
            return f"avtomir:{m.group(1)}"
        return f"url:{url}"
    if inventory:
        return f"inventory:{inventory}"
    return f"row:{row_no}"


@dataclass
class ParseResult:
    cars: list[dict]
    total: int
    accepted: int
    manual: int
    rejected: int


def parse_excel(
    path: Path,
    import_id: int,
    min_age: int = 3,
    max_age: int = 5,
    max_power_hp: int = 160,
    now: datetime | None = None,
) -> ParseResult:
    now = now or datetime.now()
    df = pd.read_excel(path)
    cols = {k: _find_col(df.columns, v) for k, v in HEADER_ALIASES.items()}
    required = ["url", "model", "year", "power", "price_cny"]
    missing = [x for x in required if cols[x] is None]
    if missing:
        raise ValueError(f"Не найдены обязательные колонки: {', '.join(missing)}")

    cars = []
    accepted = manual = rejected = 0

    for idx, row in df.iterrows():
        url = str(row[cols["url"]]).strip() if not pd.isna(row[cols["url"]]) else None
        model = str(row[cols["model"]]).strip() if not pd.isna(row[cols["model"]]) else None
        inventory = (
            str(row[cols["inventory"]]).strip()
            if cols["inventory"] and not pd.isna(row[cols["inventory"]])
            else None
        )
        year, month_text = _year_month(row[cols["year"]])
        power = _int_from(row[cols["power"]])
        mileage = _int_from(row[cols["mileage"]]) if cols["mileage"] else None
        price_cny = _float_from(row[cols["price_cny"]])
        age = now.year - year if year else None

        status = "READY"
        reason = None
        if year is None or power is None or price_cny is None:
            status, reason = "MANUAL", "Не хватает года, мощности или цены"
            manual += 1
        elif power > max_power_hp:
            status, reason = "REJECTED", f"Мощность {power} л.с. > {max_power_hp}"
            rejected += 1
        elif age < min_age or age > max_age:
            status, reason = "REJECTED", f"Возраст {age} лет вне диапазона {min_age}–{max_age}"
            rejected += 1
        else:
            accepted += 1

        def val(key):
            c = cols.get(key)
            return None if c is None or pd.isna(row[c]) else str(row[c]).strip()

        cars.append(
            {
                "source_key": _source_key(url, inventory, idx + 2),
                "import_id": import_id,
                "source_url": url,
                "model": model,
                "inventory_no": inventory,
                "color": val("color"),
                "trim": val("trim"),
                "year": year,
                "month_text": month_text,
                "mileage_km": mileage,
                "condition_text": val("condition"),
                "source_status": val("source_status"),
                "power_hp": power,
                "price_cny": price_cny,
                "price_usd": _float_from(row[cols["price_usd"]]) if cols["price_usd"] else None,
                "status": status,
                "reject_reason": reason,
            }
        )

    return ParseResult(cars, len(cars), accepted, manual, rejected)
