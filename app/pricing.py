from __future__ import annotations

import xml.etree.ElementTree as ET

import requests

CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp"


def fetch_cny_rub(timeout: int = 20) -> float:
    r = requests.get(CBR_URL, timeout=timeout, headers={"User-Agent": "FILINKOV-AUTO/1.0"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    for valute in root.findall("Valute"):
        if valute.findtext("CharCode") == "CNY":
            value = float(valute.findtext("Value").replace(",", "."))
            nominal = float(valute.findtext("Nominal") or "1")
            return value / nominal
    raise RuntimeError("Курс CNY не найден в ответе ЦБ")


def final_price(
    price_cny: float,
    cny_rub: float,
    customs_rub: float,
    extra_cny: float = 13000,
    delivery_rub: float = 200000,
    other_rub: float = 140000,
) -> float:
    return price_cny * cny_rub + extra_cny * cny_rub + customs_rub + delivery_rub + other_rub
