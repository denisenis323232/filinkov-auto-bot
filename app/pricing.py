from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date

import requests

CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp"


@dataclass(frozen=True)
class CustomsBreakdown:
    cny_rub: float
    eur_rub: float
    customs_value_rub: float
    duty_rub: float
    clearance_fee_rub: float
    recycling_fee_rub: float
    total_customs_rub: float
    age_class: str
    duty_rate_label: str


def fetch_cbr_rates(timeout: int = 20) -> dict[str, float]:
    r = requests.get(CBR_URL, timeout=timeout, headers={"User-Agent": "FILINKOV-AUTO/1.0"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    result: dict[str, float] = {}
    for valute in root.findall("Valute"):
        code = valute.findtext("CharCode") or ""
        if code not in {"CNY", "EUR"}:
            continue
        value = float((valute.findtext("Value") or "0").replace(",", "."))
        nominal = float(valute.findtext("Nominal") or "1")
        result[code] = value / nominal
    missing = {"CNY", "EUR"} - result.keys()
    if missing:
        raise RuntimeError(f"Курсы {', '.join(sorted(missing))} не найдены в ответе ЦБ")
    return result


def fetch_cny_rub(timeout: int = 20) -> float:
    return fetch_cbr_rates(timeout)["CNY"]


def _parse_month(month_text: str | None) -> int | None:
    text = (month_text or "").lower().replace("ё", "е")
    if not text:
        return None

    # Numeric forms such as 2022-09, 2022/9, 2022年9月.
    m = re.search(r"20\d{2}\D{0,6}?(1[0-2]|0?[1-9])(?:\D|$)", text)
    if m:
        return int(m.group(1))

    names = {
        1: ("январ", "january", " jan"),
        2: ("феврал", "february", " feb"),
        3: ("март", "march", " mar"),
        4: ("апрел", "april", " apr"),
        5: ("май", "мая", "may"),
        6: ("июн", "june", " jun"),
        7: ("июл", "july", " jul"),
        8: ("август", "august", " aug"),
        9: ("сентябр", "september", " sep"),
        10: ("октябр", "october", " oct"),
        11: ("ноябр", "november", " nov"),
        12: ("декабр", "december", " dec"),
    }
    padded = " " + text
    for month, words in names.items():
        if any(w in padded for w in words):
            return month
    return None


def vehicle_age_class(year: int | None, month_text: str | None, today: date | None = None) -> str:
    """Return up_to_3, 3_to_5, or over_5.

    Exact 3- and 5-year boundary months are intentionally rejected because the
    supplier data usually has month but not the exact production date.
    """
    if not year:
        raise ValueError("нет года выпуска")
    today = today or date.today()
    month = _parse_month(month_text)

    if month is not None:
        age_months = (today.year - int(year)) * 12 + (today.month - month)
        if age_months < 36:
            return "up_to_3"
        if age_months == 36:
            raise ValueError("возраст автомобиля на границе 3 лет — нужна точная дата выпуска")
        if age_months < 60:
            return "3_to_5"
        if age_months == 60:
            raise ValueError("возраст автомобиля на границе 5 лет — нужна точная дата выпуска")
        return "over_5"

    diff = today.year - int(year)
    if diff <= 2:
        return "up_to_3"
    if diff == 4:
        return "3_to_5"
    if diff >= 6:
        return "over_5"
    raise ValueError("нет месяца выпуска для точного определения возрастной ставки")


def customs_clearance_fee_rub(customs_value_rub: float) -> float:
    """Customs operation fee under RF Government Resolution No. 1637, current scale."""
    v = float(customs_value_rub)
    if v <= 200_000:
        return 1_231.0
    if v <= 450_000:
        return 2_462.0
    if v <= 1_200_000:
        return 4_924.0
    if v <= 2_700_000:
        return 13_541.0
    if v <= 4_200_000:
        return 18_465.0
    if v <= 5_500_000:
        return 21_344.0
    if v <= 10_000_000:
        return 49_240.0
    return 73_860.0


def _duty_3_to_5_eur(engine_cc: int) -> tuple[float, str]:
    cc = int(engine_cc)
    if cc <= 1000:
        rate = 1.5
    elif cc <= 1500:
        rate = 1.7
    elif cc <= 1800:
        rate = 2.5
    elif cc <= 2300:
        rate = 2.7
    elif cc <= 3000:
        rate = 3.0
    else:
        rate = 3.6
    return cc * rate, f"{rate:g} €/см³"


def _duty_over_5_eur(engine_cc: int) -> tuple[float, str]:
    cc = int(engine_cc)
    if cc <= 1000:
        rate = 3.0
    elif cc <= 1500:
        rate = 3.2
    elif cc <= 1800:
        rate = 3.5
    elif cc <= 2300:
        rate = 4.8
    elif cc <= 3000:
        rate = 5.0
    else:
        rate = 5.7
    return cc * rate, f"{rate:g} €/см³"


def _duty_up_to_3_eur(customs_value_eur: float, engine_cc: int) -> tuple[float, str]:
    value = float(customs_value_eur)
    if value <= 8_500:
        pct, min_rate = 0.54, 2.5
    elif value <= 16_700:
        pct, min_rate = 0.48, 3.5
    elif value <= 42_300:
        pct, min_rate = 0.48, 5.5
    elif value <= 84_500:
        pct, min_rate = 0.48, 7.5
    elif value <= 169_000:
        pct, min_rate = 0.48, 15.0
    else:
        pct, min_rate = 0.48, 20.0
    duty = max(value * pct, int(engine_cc) * min_rate)
    return duty, f"{pct * 100:g}% стоимости, минимум {min_rate:g} €/см³"


def recycling_fee_rub(power_hp: int | None, age_class: str) -> float:
    """Preferential personal-use recycling fee for cars up to 160 hp.

    This assumes import by an individual for personal use and qualification for
    the preferential coefficient. Cars above 160 hp are deliberately not auto-priced.
    """
    if power_hp is None:
        raise ValueError("нет мощности двигателя")
    if int(power_hp) > 160:
        raise ValueError("мощность выше 160 л.с. — льготный утильсбор не применяется")
    return 3_400.0 if age_class == "up_to_3" else 5_200.0


def calculate_customs(
    *,
    price_cny: float,
    cny_rub: float,
    eur_rub: float,
    engine_cc: int | None,
    power_hp: int | None,
    year: int | None,
    month_text: str | None,
    engine_type: str | None = None,
) -> CustomsBreakdown:
    if not price_cny or float(price_cny) <= 0:
        raise ValueError("нет цены автомобиля в CNY")
    if not engine_cc or int(engine_cc) <= 0:
        raise ValueError("не найден точный объем двигателя")
    if (engine_type or "").lower() == "electric":
        raise ValueError("для чистого электромобиля нужен отдельный расчет совокупного таможенного платежа")

    age_class = vehicle_age_class(year, month_text)
    customs_value_rub = float(price_cny) * float(cny_rub)
    customs_value_eur = customs_value_rub / float(eur_rub)

    if age_class == "3_to_5":
        duty_eur, label = _duty_3_to_5_eur(int(engine_cc))
    elif age_class == "over_5":
        duty_eur, label = _duty_over_5_eur(int(engine_cc))
    else:
        duty_eur, label = _duty_up_to_3_eur(customs_value_eur, int(engine_cc))

    duty_rub = duty_eur * float(eur_rub)
    clearance = customs_clearance_fee_rub(customs_value_rub)
    recycling = recycling_fee_rub(power_hp, age_class)
    total = duty_rub + clearance + recycling

    return CustomsBreakdown(
        cny_rub=float(cny_rub),
        eur_rub=float(eur_rub),
        customs_value_rub=customs_value_rub,
        duty_rub=duty_rub,
        clearance_fee_rub=clearance,
        recycling_fee_rub=recycling,
        total_customs_rub=total,
        age_class=age_class,
        duty_rate_label=label,
    )


def final_price(
    price_cny: float,
    cny_rub: float,
    customs_rub: float,
    extra_cny: float = 13000,
    delivery_rub: float = 200000,
    other_rub: float = 140000,
) -> float:
    return price_cny * cny_rub + extra_cny * cny_rub + customs_rub + delivery_rub + other_rub
