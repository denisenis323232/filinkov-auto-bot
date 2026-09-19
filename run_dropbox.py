from __future__ import annotations

import asyncio
import hashlib
import html
import io
import json
import logging
import os
import re
import time

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.constants import ParseMode

from app import bot as b
from app.local_brand import brand_photo_bytes
from app.pricing import calculate_customs, fetch_cbr_rates, final_price as calculate_final_price

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

DROPBOX_TOKEN = os.getenv("DROPBOX_ACCESS_TOKEN", "").strip()
DROPBOX_ROOT = os.getenv("DROPBOX_ROOT", "/FILINKOV_AUTO").rstrip("/")
_original_post_init = b.post_init
_original_buttons = b.buttons
_RATES_CACHE: tuple[float, dict[str, float]] | None = None


def _safe(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-zА-Яа-я_-]+", "-", value or "").strip("-")
    return value[:40] or "car"


def job_id(car) -> str:
    base = _safe(str(car["inventory_no"] or f"car-{car['id']}"))
    digest = hashlib.sha1(str(car["source_key"] or car["id"]).encode("utf-8")).hexdigest()[:8]
    return f"{base}-{digest}"


def kb_car(car_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Пересчитать цену", callback_data=f"price:{car_id}")],
        [InlineKeyboardButton("✏️ Изменить текст", callback_data=f"edit:{car_id}"), InlineKeyboardButton("🎙 Голос", callback_data=f"voice:{car_id}")],
        [InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{car_id}"), InlineKeyboardButton("🚀 Сейчас", callback_data=f"publish:{car_id}")],
        [InlineKeyboardButton("⏭ Пропустить", callback_data=f"skip:{car_id}"), InlineKeyboardButton("➡️ Следующая", callback_data=f"next:{car_id}")],
    ])


def _dbx_rpc(endpoint: str, payload: dict, timeout: int = 30):
    if not DROPBOX_TOKEN:
        raise RuntimeError("DROPBOX_ACCESS_TOKEN не настроен")
    r = requests.post(
        f"https://api.dropboxapi.com/2/{endpoint}",
        headers={"Authorization": f"Bearer {DROPBOX_TOKEN}", "Content-Type": "application/json"},
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=timeout,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Dropbox {endpoint}: {r.status_code} {r.text[:500]}")
    return r.json()


def _dbx_ensure_folder(path: str):
    parts = [p for p in path.split("/") if p]
    current = ""
    for part in parts:
        current += "/" + part
        try:
            _dbx_rpc("files/create_folder_v2", {"path": current, "autorename": False})
        except RuntimeError as exc:
            if "conflict" not in str(exc).lower():
                raise


def _dbx_upload(path: str, raw: bytes, timeout: int = 60):
    if not DROPBOX_TOKEN:
        raise RuntimeError("DROPBOX_ACCESS_TOKEN не настроен")
    r = requests.post(
        "https://content.dropboxapi.com/2/files/upload",
        headers={
            "Authorization": f"Bearer {DROPBOX_TOKEN}",
            "Content-Type": "application/octet-stream",
            "Dropbox-API-Arg": json.dumps({"path": path, "mode": "overwrite", "autorename": False, "mute": True}, ensure_ascii=True),
        },
        data=raw,
        timeout=timeout,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Dropbox upload: {r.status_code} {r.text[:500]}")
    return r.json()


def _dbx_list(path: str):
    try:
        data = _dbx_rpc("files/list_folder", {"path": path, "recursive": False, "include_deleted": False})
    except RuntimeError as exc:
        if "not_found" in str(exc).lower():
            return []
        raise
    entries = list(data.get("entries", []))
    cursor = data.get("cursor")
    while data.get("has_more"):
        data = _dbx_rpc("files/list_folder/continue", {"cursor": cursor})
        entries.extend(data.get("entries", []))
        cursor = data.get("cursor")
    return entries


def _dbx_download(path: str, timeout: int = 60) -> bytes:
    if not DROPBOX_TOKEN:
        raise RuntimeError("DROPBOX_ACCESS_TOKEN не настроен")
    r = requests.post(
        "https://content.dropboxapi.com/2/files/download",
        headers={"Authorization": f"Bearer {DROPBOX_TOKEN}", "Dropbox-API-Arg": json.dumps({"path": path})},
        timeout=timeout,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Dropbox download: {r.status_code} {r.text[:500]}")
    return r.content


def _verify_dropbox():
    _dbx_ensure_folder(DROPBOX_ROOT)
    _dbx_ensure_folder(f"{DROPBOX_ROOT}/inbox")
    _dbx_ensure_folder(f"{DROPBOX_ROOT}/ready")
    _dbx_upload(f"{DROPBOX_ROOT}/.bot_healthcheck.txt", b"FILINKOV AUTO Dropbox OK")
    _dbx_list(DROPBOX_ROOT)


def _photo_urls(car):
    media = json.loads(car["media_json"] or "[]")
    return [u for u in media if any(ext in u.lower() for ext in (".jpg", ".jpeg", ".png", ".webp"))][:10]


def _image_entries(entries: list[dict]) -> list[dict]:
    result = [
        e
        for e in entries
        if e.get(".tag") == "file"
        and str(e.get("name", "")).lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
    ]
    result.sort(key=lambda x: x.get("name", ""))
    return result


def _download_source(url: str, timeout: int) -> tuple[bytes, str]:
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 FILINKOV-AUTO/1.0"})
    r.raise_for_status()
    ct = (r.headers.get("content-type") or "image/jpeg").lower()
    ext = ".png" if "png" in ct else ".webp" if "webp" in ct else ".jpg"
    return r.content, ext


def _brand_and_upload(raw: bytes, name: str, ready_path: str) -> dict:
    branded, result = brand_photo_bytes(raw, name)
    _dbx_upload(f"{ready_path}/{name}", branded, timeout=max(60, b.S.http_timeout))
    report = result.to_dict()
    report["file"] = name
    return report


def stage_dropbox(car) -> tuple[str, int]:
    """Upload originals to inbox and branded copies to ready in one pass."""
    jid = job_id(car)
    inbox = f"{DROPBOX_ROOT}/inbox/{jid}"
    ready = f"{DROPBOX_ROOT}/ready/{jid}"
    _dbx_ensure_folder(inbox)
    _dbx_ensure_folder(ready)

    photos = _photo_urls(car)
    reports: list[dict] = []
    for i, url in enumerate(photos, 1):
        raw, ext = _download_source(url, b.S.http_timeout)
        name = f"{i:02d}{ext}"
        _dbx_upload(f"{inbox}/{name}", raw, timeout=max(60, b.S.http_timeout))
        reports.append(_brand_and_upload(raw, name, ready))

    manifest = {
        "job_id": jid,
        "car_id": int(car["id"]),
        "inventory_no": car["inventory_no"],
        "model": car["model"],
        "expected": len(photos),
        "source_url": car["source_url"],
        "branding": "opencv-local-v1",
    }
    _dbx_upload(f"{inbox}/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    _dbx_upload(f"{ready}/branding_report.json", json.dumps(reports, ensure_ascii=False, indent=2).encode("utf-8"))
    return jid, len(photos)


def _process_pending_job(jid: str) -> bool:
    """Recover jobs that were staged before local branding was enabled."""
    inbox = f"{DROPBOX_ROOT}/inbox/{jid}"
    ready = f"{DROPBOX_ROOT}/ready/{jid}"
    try:
        manifest = json.loads(_dbx_download(f"{inbox}/manifest.json").decode("utf-8"))
    except Exception:
        return False

    expected = int(manifest.get("expected") or 0)
    if expected <= 0:
        return False

    _dbx_ensure_folder(ready)
    ready_images = _image_entries(_dbx_list(ready))
    if len(ready_images) >= expected:
        return True

    originals = _image_entries(_dbx_list(inbox))[:expected]
    if len(originals) < expected:
        return False

    ready_names = {e.get("name") for e in ready_images}
    reports: list[dict] = []
    for entry in originals:
        name = str(entry.get("name") or "")
        if name in ready_names:
            continue
        raw = _dbx_download(entry["path_lower"], timeout=max(60, b.S.http_timeout))
        reports.append(_brand_and_upload(raw, name, ready))

    if reports:
        _dbx_upload(
            f"{ready}/branding_report.json",
            json.dumps(reports, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        changed = sum(1 for item in reports if item.get("changed"))
        b.log.info("Local branding %s: %s files processed, %s changed", jid, len(reports), changed)
    return len(_image_entries(_dbx_list(ready))) >= expected


def _process_pending_jobs(limit: int = 5) -> None:
    inbox_root = f"{DROPBOX_ROOT}/inbox"
    folders = [e for e in _dbx_list(inbox_root) if e.get(".tag") == "folder"]
    for entry in folders[:limit]:
        jid = str(entry.get("name") or "")
        if jid:
            try:
                _process_pending_job(jid)
            except Exception:
                b.log.exception("Could not locally brand Dropbox job %s", jid)


def _rates() -> dict[str, float]:
    global _RATES_CACHE
    now = time.monotonic()
    if _RATES_CACHE and now - _RATES_CACHE[0] < 900:
        return _RATES_CACHE[1]
    value = fetch_cbr_rates(b.S.http_timeout)
    _RATES_CACHE = (now, value)
    return value


def _calculate_price(car):
    rates = _rates()
    breakdown = calculate_customs(
        price_cny=float(car["price_cny"] or 0),
        cny_rub=rates["CNY"],
        eur_rub=rates["EUR"],
        engine_cc=car["engine_cc"],
        power_hp=car["power_hp"],
        year=car["year"],
        month_text=car["month_text"],
        engine_type=car["engine_type"],
    )
    total = calculate_final_price(
        price_cny=float(car["price_cny"] or 0),
        cny_rub=rates["CNY"],
        customs_rub=breakdown.total_customs_rub,
        extra_cny=b.S.extra_cny,
        delivery_rub=b.S.delivery_rub,
        other_rub=b.S.other_rub,
    )
    b.DB.set_customs(car["id"], breakdown.total_customs_rub, rates["CNY"], total)
    refreshed = b.DB.get_car(car["id"])
    b.DB.set_car_text(car["id"], b.generate_post(refreshed))
    return b.DB.get_car(car["id"]), breakdown


def _price_summary_html(car, breakdown) -> str:
    age_label = {
        "up_to_3": "до 3 лет",
        "3_to_5": "3–5 лет",
        "over_5": "старше 5 лет",
    }.get(breakdown.age_class, breakdown.age_class)
    return (
        f"💰 <b>Расчёт готов</b>\n"
        f"Объём: <b>{car['engine_cc']} см³</b> · возраст: <b>{age_label}</b>\n"
        f"Пошлина: <b>{b.rub(breakdown.duty_rub)} ₽</b> ({html.escape(breakdown.duty_rate_label)})\n"
        f"Таможенный сбор: <b>{b.rub(breakdown.clearance_fee_rub)} ₽</b>\n"
        f"Утильсбор: <b>{b.rub(breakdown.recycling_fee_rub)} ₽</b>\n"
        f"Всего таможня: <b>{b.rub(breakdown.total_customs_rub)} ₽</b>\n"
        f"Курс ЦБ: CNY <b>{breakdown.cny_rub:.4f}</b> ₽ · EUR <b>{breakdown.eur_rub:.4f}</b> ₽\n\n"
        f"💸 <b>ИТОГО В МОСКВЕ: {b.rub(car['final_price_rub'])} ₽</b>"
    )


async def _ensure_source_and_price(car, force_price: bool = True):
    if car["source_url"] and (not car["source_description"] or not car["media_json"] or not car["engine_cc"]):
        source = await asyncio.to_thread(b.extract_card, car["source_url"], b.S.http_timeout)
        b.DB.set_source_card(car["id"], source.media_urls, source.description, source.engine_cc, source.engine_type)
        car = b.DB.get_car(car["id"])

    breakdown = None
    price_error = None
    if force_price or not car["final_price_rub"]:
        try:
            car, breakdown = await asyncio.to_thread(_calculate_price, car)
        except ValueError as exc:
            price_error = str(exc)
            b.log.info("Auto price skipped for car %s: %s", car["id"], exc)
        except Exception as exc:
            price_error = f"{type(exc).__name__}: {exc}"
            b.log.exception("Auto price failed for car %s", car["id"])

    if not car["post_text"]:
        b.DB.set_car_text(car["id"], b.generate_post(car))
        car = b.DB.get_car(car["id"])
    return car, breakdown, price_error


async def send_car(message, car):
    car, breakdown, price_error = await _ensure_source_and_price(car, force_price=True)
    branded = json.loads(car["branded_media_json"] or "[]") if "branded_media_json" in car.keys() else []
    jid = job_id(car)
    count = 0

    if not branded:
        if not DROPBOX_TOKEN:
            await message.reply_text("⚙️ Нужен DROPBOX_ACCESS_TOKEN в Railway. Фото пока не отправляю, чтобы не публиковать исходники с чужим логотипом.")
        else:
            try:
                jid, count = await asyncio.to_thread(stage_dropbox, car)
            except Exception as exc:
                b.log.exception("Dropbox/local branding staging failed")
                await message.reply_text(f"⚠️ Обработка фото: {type(exc).__name__}: {exc}")

    price = f"<b>{b.rub(car['final_price_rub'])} ₽</b>" if car["final_price_rub"] else "<i>авторасчёт не выполнен</i>"
    customs = f"{b.rub(car['customs_rub'])} ₽" if car["customs_rub"] else "—"
    engine = f"{car['engine_cc']} см³" if car["engine_cc"] else "не найден"
    text = (
        f"🚗 <b>{html.escape(car['model'] or '—')}</b>\n"
        f"№ {html.escape(car['inventory_no'] or '—')}\n\n"
        f"{html.escape(str(car['month_text'] or car['year'] or '—'))}\n"
        f"Пробег: {b.rub(car['mileage_km'])} км\n"
        f"Мощность: {car['power_hp'] or '—'} л.с.\n"
        f"Двигатель: {html.escape(engine)}\n"
        f"Комплектация: {html.escape(car['trim'] or '—')}\n\n"
        f"🛃 Таможня + сборы: <b>{customs}</b>\n"
        f"💸 ИТОГОВАЯ ЦЕНА В МОСКВЕ: {price}\n\n"
        f"☁️ ID фото: <code>{html.escape(jid)}</code>\n"
        f"Статус: <b>{html.escape(car['status'])}</b>"
    )
    if price_error:
        text += f"\n\n⚠️ Авторасчёт: {html.escape(price_error)}"
    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_car(car["id"]), disable_web_page_preview=True)

    if branded:
        await b.send_post_preview(message, car)
    elif count:
        await message.reply_text(
            f"⚙️ {count} фото загружены и локально обработаны. ID: <code>{html.escape(jid)}</code>\n"
            f"Бот сейчас заберёт готовые фото из Dropbox.",
            parse_mode=ParseMode.HTML,
        )


async def buttons(update, context):
    q = update.callback_query
    if q and (q.data or "").startswith("price:"):
        if not await b.guard(update):
            return
        await q.answer()
        try:
            car_id = int((q.data or "").split(":", 1)[1])
            car = b.DB.get_car(car_id)
            car, breakdown, price_error = await _ensure_source_and_price(car, force_price=True)
            if breakdown:
                await q.message.reply_text(_price_summary_html(car, breakdown), parse_mode=ParseMode.HTML)
            else:
                await q.message.reply_text(f"⚠️ Не смог посчитать автоматически: {price_error or 'не хватает данных'}")
        except Exception as exc:
            b.log.exception("Manual auto-price refresh failed")
            await q.message.reply_text(f"⚠️ Ошибка расчёта: {type(exc).__name__}: {exc}")
        return
    await _original_buttons(update, context)


async def poll_ready(context):
    if not DROPBOX_TOKEN:
        return

    await asyncio.to_thread(_process_pending_jobs)

    owner = b.DB.get_setting("owner_user_id")
    if not owner:
        return

    cars = b.DB.list_cars(("READY", "APPROVED"), limit=200)
    for car in cars:
        branded = json.loads(car["branded_media_json"] or "[]") if "branded_media_json" in car.keys() else []
        if branded:
            continue
        jid = job_id(car)
        expected = len(_photo_urls(car))
        if expected <= 0:
            continue
        ready_path = f"{DROPBOX_ROOT}/ready/{jid}"
        try:
            files = _image_entries(await asyncio.to_thread(_dbx_list, ready_path))
        except Exception:
            continue
        if len(files) < expected:
            continue
        files = files[:expected]

        raws = []
        for entry in files:
            raws.append(await asyncio.to_thread(_dbx_download, entry["path_lower"], max(60, b.S.http_timeout)))

        buffers = []
        album = []
        caption = b._caption_html(b.render_channel_post(car, owner))
        for i, raw in enumerate(raws):
            bio = io.BytesIO(raw)
            bio.name = files[i].get("name") or f"filinkov_{jid}_{i+1}.jpg"
            buffers.append(bio)
            if i == 0:
                album.append(InputMediaPhoto(media=bio, caption=caption, parse_mode=ParseMode.HTML))
            else:
                album.append(InputMediaPhoto(media=bio))
        try:
            sent = await context.bot.send_media_group(chat_id=int(owner), media=album)
            file_ids = [m.photo[-1].file_id for m in sent if m.photo]
            if file_ids:
                b.DB.set_branded_media(car["id"], file_ids)
                await context.bot.send_message(chat_id=int(owner), text=f"✅ Фото FILINKOV готовы и привязаны. ID: {jid}")
        except Exception:
            b.log.exception("Could not import Dropbox ready photos for %s", jid)
        finally:
            for bio in buffers:
                bio.close()


async def post_init(app):
    await _original_post_init(app)
    if DROPBOX_TOKEN:
        try:
            await asyncio.to_thread(_verify_dropbox)
            b.log.info("Dropbox auth/read/write verification OK")
        except Exception as exc:
            b.log.error("Dropbox verification failed: %s", exc)
    else:
        b.log.error("DROPBOX_ACCESS_TOKEN is missing")
    app.job_queue.run_repeating(poll_ready, interval=20, first=5, name="dropbox-ready-poller")


b.kb_car = kb_car
b.send_car = send_car
b.buttons = buttons
b.post_init = post_init

if __name__ == "__main__":
    b.main()
