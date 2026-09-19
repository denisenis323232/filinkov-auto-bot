from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo

import run_weekly_waiting as m

w = m.w
b = m.b
r = m.r
TZ = ZoneInfo(b.S.timezone)
STATE_ROOT = f"{r.DROPBOX_ROOT}/state"
STATE_FILE = f"{STATE_ROOT}/weekly_queue.json"
OWNER_FILE = f"{STATE_ROOT}/owner.json"

_original_save_schedule = w._save_schedule
_original_post_init = b.post_init
_original_is_admin = b.is_admin

# Current 14 cars selected from the 18.09.2026 supplier sheet.
RECOVERY_CARS = [
    {"source_url":"http://avtomirhrb.ru/?car_1/2380.html","model":"AUDI A3","inventory_no":"536001","color":"Белый / чёрный","trim":"2024 35 TFSI Sportback Luxury Elegant","year":2023,"month_text":"2023г август","mileage_km":16000,"condition_text":"Оригинальный цвет","source_status":"Готово","power_hp":150,"price_cny":129800.0,"price_usd":None},
    {"source_url":"http://avtomirhrb.ru/?car_1/2286.html","model":"AUDI Q3","inventory_no":"719370","color":"Белый / чёрный","trim":"2022 35T Stylish and Dynamic","year":2022,"month_text":"2022г май","mileage_km":25000,"condition_text":"Оригинальный цвет","source_status":"Готово","power_hp":150,"price_cny":159800.0,"price_usd":None},
    {"source_url":"http://avtomirhrb.ru/?car_1/2624.html","model":"BMW 3 Series","inventory_no":"571672","color":"Чёрный / красный","trim":"2023 320Li M Sport Package","year":2023,"month_text":"2023г февраль","mileage_km":15000,"condition_text":"Оригинальный цвет","source_status":"Готово","power_hp":156,"price_cny":214800.0,"price_usd":None},
    {"source_url":"http://avtomirhrb.ru/?car_1/2678.html","model":"BMW 1 Series","inventory_no":"641730","color":"Белый / чёрный","trim":"2023 120i M Sport Shadow","year":2023,"month_text":"2023г май","mileage_km":38000,"condition_text":"Оригинальный цвет","source_status":"Готово","power_hp":136,"price_cny":116800.0,"price_usd":None},
    {"source_url":"http://avtomirhrb.ru/?car_1/2509.html","model":"Changan CS35plus","inventory_no":"004113","color":"Белый / чёрный","trim":"2022 Blue Whale NE 1.4T Luxury Edition","year":2022,"month_text":"2022г январь","mileage_km":32000,"condition_text":"Оригинальный цвет","source_status":"Готово","power_hp":158,"price_cny":68800.0,"price_usd":None},
    {"source_url":"http://avtomirhrb.ru/?car_1/2704.html","model":"Geely Coolray","inventory_no":"704453","color":"Серый / чёрный","trim":"2021 1.4T Platinum","year":2023,"month_text":"2023г март","mileage_km":40000,"condition_text":"Оригинальный цвет","source_status":"В процессе","power_hp":141,"price_cny":69800.0,"price_usd":None},
    {"source_url":"http://avtomirhrb.ru/?car_1/2092.html","model":"Honda Vezel","inventory_no":"111512","color":"Белый / чёрный","trim":"2021 1.5L Pioneer Edition","year":2022,"month_text":"2022г июнь","mileage_km":25000,"condition_text":"Оригинальный цвет","source_status":"Готово","power_hp":131,"price_cny":89800.0,"price_usd":None},
    {"source_url":"http://avtomirhrb.ru/?car_1/2623.html","model":"Hyundai ix35","inventory_no":"438837","color":"Чёрный / чёрный","trim":"2021 240T FWD GLS Leading Edition","year":2022,"month_text":"2022г март","mileage_km":50000,"condition_text":"Оригинальный цвет","source_status":"Готово","power_hp":140,"price_cny":99800.0,"price_usd":None},
    {"source_url":"http://avtomirhrb.ru/?car_1/2709.html","model":"Jetta VS5","inventory_no":"010722","color":"Белый / чёрный","trim":"2023 280T AT Joy Edition","year":2023,"month_text":"2023г февраль","mileage_km":23000,"condition_text":"Оригинальный цвет","source_status":"Готово","power_hp":150,"price_cny":79800.0,"price_usd":None},
    {"source_url":"http://avtomirhrb.ru/?car_1/2305.html","model":"MG5","inventory_no":"053593","color":"Серый / чёрный","trim":"2021 180T Youth Luxury Edition","year":2022,"month_text":"2022г февраль","mileage_km":15000,"condition_text":"Оригинальный цвет","source_status":"Готово","power_hp":120,"price_cny":54800.0,"price_usd":None},
    {"source_url":"http://avtomirhrb.ru/?car_1/2645.html","model":"Mercedes-Benz GLB","inventory_no":"046517","color":"Белый / чёрный","trim":"2022 Facelift 180 Fashion","year":2021,"month_text":"2021г ноябрь","mileage_km":49000,"condition_text":"Оригинальный цвет","source_status":"Готово","power_hp":136,"price_cny":179800.0,"price_usd":None},
    {"source_url":"http://avtomirhrb.ru/?car_1/2703.html","model":"Mazda CX-5","inventory_no":"528568","color":"Белый / чёрный","trim":"2024 2.0L AT FWD Comfort","year":2023,"month_text":"2023г ноябрь","mileage_km":9000,"condition_text":"Оригинальный цвет","source_status":"Готово","power_hp":155,"price_cny":119800.0,"price_usd":None},
    {"source_url":"http://avtomirhrb.ru/?car_1/2152.html","model":"Volkswagen Golf","inventory_no":"253359","color":"Белый / чёрный","trim":"2022 280TSI DSG R-Line","year":2022,"month_text":"2022г январь","mileage_km":29000,"condition_text":"Оригинальный цвет","source_status":"В процессе","power_hp":150,"price_cny":109800.0,"price_usd":None},
    {"source_url":"http://avtomirhrb.ru/?car_1/2710.html","model":"Toyota Corolla","inventory_no":"773045","color":"Белый / чёрный","trim":"2022 1.2L Pioneer Edition","year":2023,"month_text":"2023г июнь","mileage_km":33000,"condition_text":"Оригинальный цвет","source_status":"Готово","power_hp":116,"price_cny":75800.0,"price_usd":None},
]
CURRENT_INVENTORIES = {x["inventory_no"] for x in RECOVERY_CARS}


def _source_key(url: str) -> str:
    mt = re.search(r"car_\d+/(\d+)\.html", url or "")
    return f"avtomir:{mt.group(1)}" if mt else f"url:{url}"


def _seed_schedule() -> list[dict]:
    start = datetime(2026, 9, 21, 12, 0, tzinfo=TZ)
    slots = []
    day = start.date()
    while len(slots) < len(RECOVERY_CARS):
        slots.append(datetime.combine(day, dt_time(12, 0), tzinfo=TZ))
        if len(slots) < len(RECOVERY_CARS):
            slots.append(datetime.combine(day, dt_time(18, 0), tzinfo=TZ))
        day += timedelta(days=1)
    return [{"source_key": _source_key(car["source_url"]), "at": slots[i].isoformat(), "state": "scheduled"} for i, car in enumerate(RECOVERY_CARS)]


def _car_snapshot(row) -> dict:
    d = dict(row)
    for key in ("id", "import_id", "created_at", "updated_at", "published_at"):
        d.pop(key, None)
    return d


def _upload_state(schedule: list[dict]) -> None:
    try:
        r._dbx_ensure_folder(STATE_ROOT)
        cars = []
        state_schedule = []
        for item in schedule:
            car = b.DB.get_car(int(item["car_id"])) if item.get("car_id") else None
            if not car or car["inventory_no"] not in CURRENT_INVENTORIES:
                continue
            cars.append(_car_snapshot(car))
            state_schedule.append({
                "source_key": car["source_key"],
                "at": item["at"],
                "state": item.get("state", "scheduled"),
                "warned": item.get("warned", False),
            })
        payload = {"version": 2, "selection": "2026-09-19-current-14", "cars": cars, "schedule": state_schedule}
        r._dbx_upload(STATE_FILE, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        b.log.warning("Could not persist weekly state to Dropbox; continuing without it")


def save_schedule(schedule: list[dict]) -> None:
    _original_save_schedule(schedule)
    _upload_state(schedule)


def _load_dropbox_state() -> dict | None:
    try:
        raw = r._dbx_download(STATE_FILE)
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            return None
        cars = value.get("cars") or []
        inventories = {str(x.get("inventory_no") or "") for x in cars if isinstance(x, dict)}
        if inventories != CURRENT_INVENTORIES:
            b.log.info("Ignoring stale Dropbox weekly state; using current 14-car selection")
            return None
        return value
    except Exception:
        return None


def _restore_owner() -> None:
    if b.DB.get_setting("owner_user_id"):
        return
    try:
        data = json.loads(r._dbx_download(OWNER_FILE).decode("utf-8"))
        owner = str(data.get("owner_user_id") or "").strip()
        if owner:
            b.DB.set_setting("owner_user_id", owner)
    except Exception:
        pass


def is_admin(user_id: int) -> bool:
    ok = _original_is_admin(user_id)
    if ok:
        try:
            r._dbx_ensure_folder(STATE_ROOT)
            r._dbx_upload(OWNER_FILE, json.dumps({"owner_user_id": str(user_id)}).encode("utf-8"))
        except Exception:
            pass
    return ok


def _restore_state() -> list[int]:
    _restore_owner()
    state = _load_dropbox_state()
    if not state:
        state = {"cars": RECOVERY_CARS, "schedule": _seed_schedule()}

    key_to_id: dict[str, int] = {}
    for raw in state.get("cars", []):
        car = dict(raw)
        if str(car.get("inventory_no") or "") not in CURRENT_INVENTORIES:
            continue
        url = str(car.get("source_url") or "")
        car["source_key"] = _source_key(url)
        car["import_id"] = None
        car["status"] = "READY"
        car["reject_reason"] = None
        car.setdefault("post_text", None)
        car_id = b.DB.upsert_car(car)
        b.DB.set_car_status(car_id, "READY")
        key_to_id[car["source_key"]] = car_id

    restored_schedule = []
    for item in state.get("schedule", []):
        source_key = item.get("source_key")
        car_id = key_to_id.get(source_key)
        if not car_id:
            continue
        restored_schedule.append({
            "car_id": car_id,
            "at": item["at"],
            "state": item.get("state", "scheduled"),
            **({"warned": True} if item.get("warned") else {}),
        })
    if restored_schedule:
        _original_save_schedule(restored_schedule)
        b.log.info("Current weekly state restored: %s cars", len(restored_schedule))
    return list(key_to_id.values())


async def _enrich_restored(car_ids: list[int]) -> None:
    for car_id in car_ids:
        try:
            car = b.DB.get_car(car_id)
            if not car:
                continue
            car, _breakdown, price_error = await r._ensure_source_and_price(car, force_price=True)
            b.DB.set_car_status(car_id, "READY")
            if price_error:
                b.log.warning("Recovered car %s price warning: %s", car_id, price_error)
        except Exception:
            b.log.exception("Recovered car enrichment failed: %s", car_id)
    schedule = w._load_schedule()
    if schedule:
        _upload_state(schedule)


async def post_init(app):
    await _original_post_init(app)
    car_ids = await asyncio.to_thread(_restore_state)
    if car_ids:
        app.create_task(_enrich_restored(car_ids), name="restore-weekly-state")


w._save_schedule = save_schedule
b.is_admin = is_admin
b.post_init = post_init

if __name__ == "__main__":
    b.main()
