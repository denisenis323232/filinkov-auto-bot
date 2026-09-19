from __future__ import annotations

import asyncio
import hashlib
import html
import io
import json
import logging
import os
import re

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.constants import ParseMode

from app import bot as b

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

DROPBOX_TOKEN = os.getenv("DROPBOX_ACCESS_TOKEN", "").strip()
DROPBOX_ROOT = os.getenv("DROPBOX_ROOT", "/FILINKOV_AUTO").rstrip("/")
_original_post_init = b.post_init


def _safe(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-zА-Яа-я_-]+", "-", value or "").strip("-")
    return value[:40] or "car"


def job_id(car) -> str:
    base = _safe(str(car["inventory_no"] or f"car-{car['id']}"))
    digest = hashlib.sha1(str(car["source_key"] or car["id"]).encode("utf-8")).hexdigest()[:8]
    return f"{base}-{digest}"


def kb_car(car_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Таможня", callback_data=f"customs:{car_id}")],
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


def _download_source(url: str, timeout: int) -> tuple[bytes, str]:
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 FILINKOV-AUTO/1.0"})
    r.raise_for_status()
    ct = (r.headers.get("content-type") or "image/jpeg").lower()
    ext = ".png" if "png" in ct else ".webp" if "webp" in ct else ".jpg"
    return r.content, ext


def stage_dropbox(car) -> tuple[str, int]:
    jid = job_id(car)
    inbox = f"{DROPBOX_ROOT}/inbox/{jid}"
    ready = f"{DROPBOX_ROOT}/ready/{jid}"
    _dbx_ensure_folder(inbox)
    _dbx_ensure_folder(ready)

    photos = _photo_urls(car)
    for i, url in enumerate(photos, 1):
        raw, ext = _download_source(url, b.S.http_timeout)
        _dbx_upload(f"{inbox}/{i:02d}{ext}", raw)

    manifest = {
        "job_id": jid,
        "car_id": int(car["id"]),
        "inventory_no": car["inventory_no"],
        "model": car["model"],
        "expected": len(photos),
        "source_url": car["source_url"],
    }
    _dbx_upload(f"{inbox}/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    return jid, len(photos)


async def _ensure_source(car):
    if car["source_url"] and (not car["source_description"] or not car["media_json"]):
        source = await asyncio.to_thread(b.extract_card, car["source_url"], b.S.http_timeout)
        b.DB.set_source_card(car["id"], source.media_urls, source.description, source.engine_cc, source.engine_type)
        car = b.DB.get_car(car["id"])
        b.DB.set_car_text(car["id"], b.generate_post(car))
    return car


async def send_car(message, car):
    car = await _ensure_source(car)
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
                b.log.exception("Dropbox staging failed")
                await message.reply_text(f"⚠️ Dropbox: {type(exc).__name__}: {exc}")

    price = f"<b>{b.rub(car['final_price_rub'])} ₽</b>" if car["final_price_rub"] else "<i>ещё не рассчитана</i>"
    text = (
        f"🚗 <b>{html.escape(car['model'] or '—')}</b>\n"
        f"№ {html.escape(car['inventory_no'] or '—')}\n\n"
        f"{html.escape(str(car['month_text'] or car['year'] or '—'))}\n"
        f"Пробег: {b.rub(car['mileage_km'])} км\n"
        f"Мощность: {car['power_hp'] or '—'} л.с.\n"
        f"Комплектация: {html.escape(car['trim'] or '—')}\n\n"
        f"💸 ИТОГОВАЯ ЦЕНА В МОСКВЕ: {price}\n\n"
        f"☁️ ID фото: <code>{html.escape(jid)}</code>\n"
        f"Статус: <b>{html.escape(car['status'])}</b>"
    )
    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_car(car["id"]), disable_web_page_preview=True)

    if branded:
        await b.send_post_preview(message, car)
    elif count:
        await message.reply_text(
            f"☁️ {count} фото отправлены в Dropbox: <code>{DROPBOX_ROOT}/inbox/{jid}</code>\n"
            f"Готовые жду в: <code>{DROPBOX_ROOT}/ready/{jid}</code>",
            parse_mode=ParseMode.HTML,
        )


async def poll_ready(context):
    if not DROPBOX_TOKEN:
        return
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
            entries = await asyncio.to_thread(_dbx_list, ready_path)
        except Exception:
            continue
        files = [e for e in entries if e.get(".tag") == "file" and str(e.get("name", "")).lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
        files.sort(key=lambda x: x.get("name", ""))
        if len(files) < expected:
            continue
        files = files[:expected]

        raws = []
        for e in files:
            raws.append(await asyncio.to_thread(_dbx_download, e["path_lower"]))

        buffers = []
        album = []
        caption = b._caption_html(b.render_channel_post(car, owner))
        for i, raw in enumerate(raws):
            bio = io.BytesIO(raw)
            bio.name = f"filinkov_{jid}_{i+1}.jpg"
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
                await context.bot.send_message(chat_id=int(owner), text=f"✅ Готовые фото из Dropbox привязаны. ID: {jid}")
        except Exception:
            b.log.exception("Could not import Dropbox ready photos for %s", jid)


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
    app.job_queue.run_repeating(poll_ready, interval=20, first=8, name="dropbox-ready-poller")


b.kb_car = kb_car
b.send_car = send_car
b.post_init = post_init

if __name__ == "__main__":
    b.main()
