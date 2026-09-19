from __future__ import annotations

import asyncio
import hashlib
import html
import io
import json
import logging
import os
import re
from pathlib import Path

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.constants import ParseMode

from app import bot as b

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

ROOT = b.S.data_dir / "cloud_jobs"
ROOT.mkdir(parents=True, exist_ok=True)
DROPBOX_ROOT = "/FILINKOV_AUTO"
DROPBOX_TOKEN = os.getenv("DROPBOX_ACCESS_TOKEN", "").strip()

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


def _is_photo(url: str) -> bool:
    low = (url or "").lower()
    return any(x in low for x in (".jpg", ".jpeg", ".png", ".webp"))


def _require_dropbox():
    if not DROPBOX_TOKEN:
        raise RuntimeError("DROPBOX_ACCESS_TOKEN не настроен")


def _dbx_api(endpoint: str, payload: dict, timeout: int = 30):
    _require_dropbox()
    r = requests.post(
        f"https://api.dropboxapi.com/2/{endpoint}",
        headers={"Authorization": f"Bearer {DROPBOX_TOKEN}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Dropbox {endpoint}: HTTP {r.status_code}: {r.text[:300]}")
    return r.json() if r.content else {}


def _dbx_create_folder(path: str):
    _require_dropbox()
    r = requests.post(
        "https://api.dropboxapi.com/2/files/create_folder_v2",
        headers={"Authorization": f"Bearer {DROPBOX_TOKEN}", "Content-Type": "application/json"},
        json={"path": path, "autorename": False},
        timeout=30,
    )
    if r.status_code == 409 and "conflict" in r.text.lower():
        return
    if r.status_code >= 400:
        raise RuntimeError(f"Dropbox create_folder: HTTP {r.status_code}: {r.text[:300]}")


def _dbx_upload(path: str, raw: bytes):
    _require_dropbox()
    arg = {"path": path, "mode": "overwrite", "autorename": False, "mute": True, "strict_conflict": False}
    r = requests.post(
        "https://content.dropboxapi.com/2/files/upload",
        headers={
            "Authorization": f"Bearer {DROPBOX_TOKEN}",
            "Content-Type": "application/octet-stream",
            "Dropbox-API-Arg": json.dumps(arg, ensure_ascii=True),
        },
        data=raw,
        timeout=max(60, b.S.http_timeout),
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Dropbox upload: HTTP {r.status_code}: {r.text[:300]}")


def _dbx_list(path: str) -> list[dict]:
    try:
        data = _dbx_api("files/list_folder", {"path": path, "recursive": False, "include_deleted": False}, timeout=30)
    except RuntimeError as exc:
        if "path/not_found" in str(exc).lower() or "not_found" in str(exc).lower():
            return []
        raise
    entries = list(data.get("entries") or [])
    while data.get("has_more"):
        data = _dbx_api("files/list_folder/continue", {"cursor": data["cursor"]}, timeout=30)
        entries.extend(data.get("entries") or [])
    return entries


def _dbx_download(path: str) -> bytes:
    _require_dropbox()
    r = requests.post(
        "https://content.dropboxapi.com/2/files/download",
        headers={
            "Authorization": f"Bearer {DROPBOX_TOKEN}",
            "Dropbox-API-Arg": json.dumps({"path": path}, ensure_ascii=True),
        },
        timeout=max(60, b.S.http_timeout),
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Dropbox download: HTTP {r.status_code}: {r.text[:300]}")
    return r.content


def _download_source(url: str) -> tuple[bytes, str]:
    r = requests.get(url, timeout=b.S.http_timeout, headers={"User-Agent": "Mozilla/5.0 FILINKOV-AUTO/1.0"})
    r.raise_for_status()
    ct = (r.headers.get("content-type") or "image/jpeg").split(";")[0].lower()
    ext = ".png" if "png" in ct else ".webp" if "webp" in ct else ".jpg"
    return r.content, ext


def _verify_dropbox():
    _dbx_create_folder(DROPBOX_ROOT)
    _dbx_create_folder(f"{DROPBOX_ROOT}/inbox")
    _dbx_create_folder(f"{DROPBOX_ROOT}/ready")
    _dbx_upload(f"{DROPBOX_ROOT}/.bot_healthcheck.txt", b"FILINKOV AUTO bot Dropbox connection OK")
    _dbx_list(DROPBOX_ROOT)


def stage_car(car) -> tuple[str, int]:
    jid = job_id(car)
    local = ROOT / jid
    local.mkdir(parents=True, exist_ok=True)

    inbox_path = f"{DROPBOX_ROOT}/inbox/{jid}"
    ready_path = f"{DROPBOX_ROOT}/ready/{jid}"
    _dbx_create_folder(DROPBOX_ROOT)
    _dbx_create_folder(f"{DROPBOX_ROOT}/inbox")
    _dbx_create_folder(f"{DROPBOX_ROOT}/ready")
    _dbx_create_folder(inbox_path)
    _dbx_create_folder(ready_path)

    media = json.loads(car["media_json"] or "[]")
    photos = [u for u in media if _is_photo(u)][:10]
    existing_names = {e.get("name") for e in _dbx_list(inbox_path) if e.get(".tag") == "file"}

    for i, url in enumerate(photos, 1):
        prefix = f"{i:02d}."
        if any((name or "").startswith(prefix) for name in existing_names):
            continue
        raw, ext = _download_source(url)
        _dbx_upload(f"{inbox_path}/{i:02d}{ext}", raw)

    manifest = {
        "job_id": jid,
        "car_id": int(car["id"]),
        "source_key": car["source_key"],
        "inventory_no": car["inventory_no"],
        "model": car["model"],
        "source_url": car["source_url"],
        "expected": len(photos),
        "inbox": inbox_path,
        "ready": ready_path,
    }
    manifest_raw = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    _dbx_upload(f"{inbox_path}/manifest.json", manifest_raw)
    (local / "manifest.json").write_bytes(manifest_raw)
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

    if not branded:
        try:
            jid, count = await asyncio.to_thread(stage_car, car)
        except Exception as exc:
            b.log.exception("Could not stage Dropbox job")
            await message.reply_text(f"⚠️ Не смог выгрузить фото в Dropbox: {type(exc).__name__}: {exc}")
            count = 0
    else:
        count = len(branded)

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
            f"☁️ {count} фото лежат в Dropbox: <code>/FILINKOV_AUTO/inbox/{html.escape(jid)}</code>\n"
            f"Готовые положи в <code>/FILINKOV_AUTO/ready/{html.escape(jid)}</code> — бот заберёт их сам.",
            parse_mode=ParseMode.HTML,
        )


async def poll_ready(context):
    owner = b.DB.get_setting("owner_user_id")
    if not owner or not DROPBOX_TOKEN:
        return

    for folder in sorted(ROOT.iterdir() if ROOT.exists() else []):
        if not folder.is_dir() or (folder / "imported.flag").exists():
            continue
        mf = folder / "manifest.json"
        if not mf.exists():
            continue
        try:
            manifest = json.loads(mf.read_text(encoding="utf-8"))
            expected = int(manifest.get("expected") or 0)
            car_id = int(manifest["car_id"])
            ready_path = manifest["ready"]
        except Exception:
            continue

        try:
            entries = await asyncio.to_thread(_dbx_list, ready_path)
        except Exception:
            b.log.exception("Could not list Dropbox ready folder for %s", folder.name)
            continue

        files = [
            e for e in entries
            if e.get(".tag") == "file" and str(e.get("name") or "").lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        ]
        files.sort(key=lambda e: e.get("name") or "")
        if expected <= 0 or len(files) < expected:
            continue
        files = files[:expected]

        car = b.DB.get_car(car_id)
        if not car:
            continue

        buffers = []
        try:
            for e in files:
                raw = await asyncio.to_thread(_dbx_download, e["path_lower"])
                bio = io.BytesIO(raw)
                bio.name = e.get("name") or "photo.jpg"
                buffers.append(bio)

            caption = b._caption_html(b.render_channel_post(car, owner))
            album = [InputMediaPhoto(media=buffers[0], caption=caption, parse_mode=ParseMode.HTML)]
            album.extend(InputMediaPhoto(media=h) for h in buffers[1:])
            sent = await context.bot.send_media_group(chat_id=int(owner), media=album)
            file_ids = [m.photo[-1].file_id for m in sent if m.photo]
            if file_ids:
                b.DB.set_branded_media(car_id, file_ids)
                (folder / "imported.flag").write_text("ok", encoding="utf-8")
                await asyncio.to_thread(_dbx_upload, f"{ready_path}/imported.flag", b"ok")
                await context.bot.send_message(chat_id=int(owner), text=f"✅ Фото FILINKOV готовы и привязаны к машине. ID: {folder.name}")
        except Exception:
            b.log.exception("Could not import ready Dropbox photos for %s", folder.name)
        finally:
            for bio in buffers:
                bio.close()


async def post_init(app):
    await _original_post_init(app)
    if DROPBOX_TOKEN:
        try:
            await asyncio.to_thread(_verify_dropbox)
            logging.getLogger("filinkov-dropbox").info("Dropbox auth/read/write verification OK")
        except Exception as exc:
            logging.getLogger("filinkov-dropbox").error("Dropbox verification failed: %s", exc)
        app.job_queue.run_repeating(poll_ready, interval=15, first=5, name="dropbox-ready-poller")
        logging.getLogger("filinkov-dropbox").info("Dropbox workflow enabled")
    else:
        logging.getLogger("filinkov-dropbox").warning("DROPBOX_ACCESS_TOKEN is missing")


b.kb_car = kb_car
b.send_car = send_car
b.post_init = post_init

if __name__ == "__main__":
    b.main()
