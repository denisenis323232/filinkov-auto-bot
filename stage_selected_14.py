from __future__ import annotations

import hashlib
import json
import os
import re
import requests

from app import bot as b
from app.source_avtomir import extract_card

DROPBOX_TOKEN = os.getenv("DROPBOX_ACCESS_TOKEN", "").strip()
DROPBOX_ROOT = os.getenv("DROPBOX_ROOT", "/FILINKOV_AUTO").rstrip("/")
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "30"))

CARS = [
    dict(model="AUDI A3", inventory_no="536001", source_url="http://avtomirhrb.ru/?car_1/2380.html", color="Белый / чёрный", trim="2024 35 TFSI Sportback Luxury Elegant", year=2023, month_text="2023г август", mileage_km=16000, power_hp=150, price_cny=129800),
    dict(model="AUDI Q3", inventory_no="719370", source_url="http://avtomirhrb.ru/?car_1/2286.html", color="Белый / чёрный", trim="2022 35T Stylish and Dynamic", year=2022, month_text="2022г май", mileage_km=25000, power_hp=150, price_cny=159800),
    dict(model="BMW 3 Series", inventory_no="571672", source_url="http://avtomirhrb.ru/?car_1/2624.html", color="Чёрный / красный", trim="2023 320Li M Sport Package", year=2023, month_text="2023г февраль", mileage_km=15000, power_hp=156, price_cny=214800),
    dict(model="BMW 1 Series", inventory_no="641730", source_url="http://avtomirhrb.ru/?car_1/2678.html", color="Белый / чёрный", trim="2023 120i M Sport Shadow", year=2023, month_text="2023г май", mileage_km=38000, power_hp=136, price_cny=116800),
    dict(model="Changan CS35plus", inventory_no="004113", source_url="http://avtomirhrb.ru/?car_1/2509.html", color="Белый / чёрный", trim="2022 Blue Whale NE 1.4T Luxury Edition", year=2022, month_text="2022г январь", mileage_km=32000, power_hp=158, price_cny=68800),
    dict(model="Geely Coolray", inventory_no="704453", source_url="http://avtomirhrb.ru/?car_1/2704.html", color="Серый / чёрный", trim="2021 1.4T Platinum", year=2023, month_text="2023г март", mileage_km=40000, power_hp=141, price_cny=69800),
    dict(model="Honda Vezel", inventory_no="111512", source_url="http://avtomirhrb.ru/?car_1/2092.html", color="Белый / чёрный", trim="2021 1.5L Pioneer Edition", year=2022, month_text="2022г июнь", mileage_km=25000, power_hp=131, price_cny=89800),
    dict(model="Hyundai ix35", inventory_no="438837", source_url="http://avtomirhrb.ru/?car_1/2623.html", color="Чёрный / чёрный", trim="2021 240T FWD GLS Leading Edition", year=2022, month_text="2022г март", mileage_km=50000, power_hp=140, price_cny=99800),
    dict(model="Jetta VS5", inventory_no="010722", source_url="http://avtomirhrb.ru/?car_1/2709.html", color="Белый / чёрный", trim="2023 280T AT Joy Edition", year=2023, month_text="2023г февраль", mileage_km=23000, power_hp=150, price_cny=79800),
    dict(model="MG5", inventory_no="053593", source_url="http://avtomirhrb.ru/?car_1/2305.html", color="Серый / чёрный", trim="2021 180T Youth Luxury Edition", year=2022, month_text="2022г февраль", mileage_km=15000, power_hp=120, price_cny=54800),
    dict(model="Mercedes-Benz GLB", inventory_no="046517", source_url="http://avtomirhrb.ru/?car_1/2645.html", color="Белый / чёрный", trim="2022 Facelift 180 Fashion", year=2021, month_text="2021г ноябрь", mileage_km=49000, power_hp=136, price_cny=179800),
    dict(model="Mazda CX-5", inventory_no="528568", source_url="http://avtomirhrb.ru/?car_1/2703.html", color="Белый / чёрный", trim="2024 2.0L AT FWD Comfort", year=2023, month_text="2023г ноябрь", mileage_km=9000, power_hp=155, price_cny=119800),
    dict(model="Volkswagen Golf", inventory_no="253359", source_url="http://avtomirhrb.ru/?car_1/2152.html", color="Белый / чёрный", trim="2022 280TSI DSG R-Line", year=2022, month_text="2022г январь", mileage_km=29000, power_hp=150, price_cny=109800),
    dict(model="Toyota Corolla", inventory_no="773045", source_url="http://avtomirhrb.ru/?car_1/2710.html", color="Белый / чёрный", trim="2022 1.2L Pioneer Edition", year=2023, month_text="2023г июнь", mileage_km=33000, power_hp=116, price_cny=75800),
]


def source_key(url: str) -> str:
    match = re.search(r"car_\d+/(\d+)\.html", url or "")
    return f"avtomir:{match.group(1)}" if match else f"url:{url}"


def dbx_rpc(endpoint: str, payload: dict):
    if not DROPBOX_TOKEN:
        raise RuntimeError("DROPBOX_ACCESS_TOKEN unavailable")
    resp = requests.post(
        f"https://api.dropboxapi.com/2/{endpoint}",
        headers={"Authorization": f"Bearer {DROPBOX_TOKEN}", "Content-Type": "application/json"},
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Dropbox {endpoint}: {resp.status_code} {resp.text[:300]}")
    return resp.json()


def ensure_folder(path: str):
    cur = ""
    for part in [p for p in path.split("/") if p]:
        cur += "/" + part
        try:
            dbx_rpc("files/create_folder_v2", {"path": cur, "autorename": False})
        except RuntimeError as exc:
            if "conflict" not in str(exc).lower():
                raise


def list_folder(path: str):
    try:
        data = dbx_rpc("files/list_folder", {"path": path, "recursive": False, "include_deleted": False})
    except RuntimeError as exc:
        if "not_found" in str(exc).lower():
            return []
        raise
    rows = list(data.get("entries", []))
    while data.get("has_more"):
        data = dbx_rpc("files/list_folder/continue", {"cursor": data["cursor"]})
        rows.extend(data.get("entries", []))
    return rows


def upload(path: str, raw: bytes):
    if not DROPBOX_TOKEN:
        raise RuntimeError("DROPBOX_ACCESS_TOKEN unavailable")
    resp = requests.post(
        "https://content.dropboxapi.com/2/files/upload",
        headers={
            "Authorization": f"Bearer {DROPBOX_TOKEN}",
            "Content-Type": "application/octet-stream",
            "Dropbox-API-Arg": json.dumps({"path": path, "mode": "overwrite", "autorename": False, "mute": True}),
        },
        data=raw, timeout=max(60, TIMEOUT),
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Dropbox upload: {resp.status_code} {resp.text[:300]}")


def download_image(url: str):
    resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0 FILINKOV-AUTO/1.0"})
    resp.raise_for_status()
    ct = (resp.headers.get("content-type") or "image/jpeg").lower()
    ext = ".png" if "png" in ct else ".webp" if "webp" in ct else ".jpg"
    return resp.content, ext


def numbered(entries):
    out = {}
    for entry in entries:
        if entry.get(".tag") != "file":
            continue
        match = re.fullmatch(r"(0[1-9]|10)\.(jpg|jpeg|png|webp)", str(entry.get("name", "")).lower())
        if match:
            out[int(match.group(1))] = entry
    return out


def main():
    loaded = 0
    for pos, src in enumerate(CARS, 1):
        row = {
            "source_key": source_key(src["source_url"]),
            "import_id": None,
            "source_url": src["source_url"],
            "model": src["model"],
            "inventory_no": src["inventory_no"],
            "color": src["color"],
            "trim": src["trim"],
            "year": src["year"],
            "month_text": src["month_text"],
            "mileage_km": src["mileage_km"],
            "condition_text": "Оригинальный цвет",
            "source_status": "Готово",
            "power_hp": src["power_hp"],
            "price_cny": src["price_cny"],
            "price_usd": None,
            "status": "READY",
            "reject_reason": None,
            "post_text": None,
        }
        car_id = b.DB.upsert_car(row)
        loaded += 1

        photos = []
        try:
            card = extract_card(src["source_url"], TIMEOUT)
            b.DB.set_source_card(car_id, card.media_urls, card.description, card.engine_cc, card.engine_type)
            car = b.DB.get_car(car_id)
            b.DB.set_car_text(car_id, b.generate_post(car))
            photos = [u for u in card.media_urls if any(x in u.lower() for x in (".jpg", ".jpeg", ".png", ".webp"))][:10]
        except Exception as exc:
            print(f"WARNING source enrichment {src['inventory_no']}: {type(exc).__name__}: {exc}", flush=True)

        suffix = hashlib.sha1(src["source_url"].encode("utf-8")).hexdigest()[:8]
        jid = f'{src["inventory_no"]}-{suffix}'

        if photos and DROPBOX_TOKEN:
            try:
                inbox = f"{DROPBOX_ROOT}/inbox/{jid}"
                ready = f"{DROPBOX_ROOT}/ready/{jid}"
                ensure_folder(inbox)
                ensure_folder(ready)
                existing = numbered(list_folder(inbox))
                for i, url in enumerate(photos, 1):
                    if i in existing:
                        continue
                    raw, ext = download_image(url)
                    upload(f"{inbox}/{i:02d}{ext}", raw)
                manifest = {
                    "job_id": jid,
                    "car_id": car_id,
                    "selection_position": pos,
                    "inventory_no": src["inventory_no"],
                    "model": src["model"],
                    "expected": len(photos),
                    "source_url": src["source_url"],
                    "branding": "individual-photo-edit",
                    "state": "waiting_ready_photos",
                    "rule": "ready must contain individual files 01..10; no collages",
                }
                upload(f"{inbox}/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
            except Exception as exc:
                print(f"WARNING Dropbox stage {jid}: {type(exc).__name__}: {exc}", flush=True)

        print(f"SELECTED {pos:02d}/14 {jid} source_photos={len(photos)}", flush=True)

    print(f"SELECTED_SET_READY {loaded}/14", flush=True)


if __name__ == "__main__":
    main()
