from __future__ import annotations

from app import bot as b
from app.source_avtomir import extract_card

URL = "http://avtomirhrb.ru/?car_1/2296.html"


def main() -> int:
    row = {
        "source_key": URL,
        "import_id": None,
        "source_url": URL,
        "model": "AUDI Q2",
        "inventory_no": "487547",
        "color": "Белый / чёрный",
        "trim": "2022 35T Stylish and Dynamic",
        "year": 2022,
        "month_text": "2022г февраль",
        "mileage_km": 19000,
        "condition_text": "Оригинальный цвет",
        "source_status": "Готово",
        "power_hp": 150,
        "price_cny": 109800,
        "price_usd": None,
        "status": "READY",
        "reject_reason": None,
        "post_text": None,
    }
    car_id = b.DB.upsert_car(row)
    card = extract_card(URL, b.S.http_timeout)
    b.DB.set_source_card(car_id, card.media_urls, card.description, card.engine_cc, card.engine_type)
    car = b.DB.get_car(car_id)
    b.DB.set_car_text(car_id, b.generate_post(car))
    b.DB.set_car_status(car_id, "READY")
    print(f"Q2_CAR_READY id={car_id} inventory=487547 photos={len(card.media_urls)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
