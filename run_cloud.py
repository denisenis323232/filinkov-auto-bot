from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.constants import ParseMode

from app import bot as b

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

ROOT = b.S.data_dir / "cloud_jobs"
ROOT.mkdir(parents=True, exist_ok=True)
PORT = int(os.getenv("PORT", "8080"))

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


def _download_photo(url: str, target_base: Path, timeout: int) -> Path:
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 FILINKOV-AUTO/1.0"})
    r.raise_for_status()
    ct = (r.headers.get("content-type") or "image/jpeg").split(";")[0].lower()
    ext = ".png" if "png" in ct else ".webp" if "webp" in ct else ".jpg"
    path = target_base.with_suffix(ext)
    path.write_bytes(r.content)
    return path


def stage_car(car) -> tuple[str, int]:
    jid = job_id(car)
    folder = ROOT / jid
    inbox = folder / "inbox"
    ready = folder / "ready"
    inbox.mkdir(parents=True, exist_ok=True)
    ready.mkdir(parents=True, exist_ok=True)

    media = json.loads(car["media_json"] or "[]")
    photos = [u for u in media if _is_photo(u)][:10]
    existing = sorted([p for p in inbox.iterdir() if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}])

    if len(existing) < len(photos):
        for old in existing:
            old.unlink(missing_ok=True)
        for i, url in enumerate(photos, 1):
            _download_photo(url, inbox / f"{i:02d}", b.S.http_timeout)

    manifest = {
        "job_id": jid,
        "car_id": int(car["id"]),
        "inventory_no": car["inventory_no"],
        "model": car["model"],
        "source_url": car["source_url"],
        "expected": len(photos),
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return jid, len(photos)


def _json_response(handler: BaseHTTPRequestHandler, payload, status=200):
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(raw)


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "FILINKOVCloud/1.0"

    def log_message(self, fmt, *args):
        logging.getLogger("filinkov-cloud").info("%s - %s", self.address_string(), fmt % args)

    def do_GET(self):
        path = unquote(self.path.split("?", 1)[0])
        if path == "/health":
            return _json_response(self, {"ok": True})
        if path == "/jobs":
            jobs = []
            for folder in sorted(ROOT.iterdir() if ROOT.exists() else []):
                mf = folder / "manifest.json"
                if not folder.is_dir() or not mf.exists():
                    continue
                try:
                    item = json.loads(mf.read_text(encoding="utf-8"))
                except Exception:
                    continue
                item["inbox_count"] = len(list((folder / "inbox").glob("*"))) if (folder / "inbox").exists() else 0
                item["ready_count"] = len(list((folder / "ready").glob("*"))) if (folder / "ready").exists() else 0
                item["imported"] = (folder / "imported.flag").exists()
                jobs.append(item)
            return _json_response(self, {"jobs": jobs})

        m = re.fullmatch(r"/jobs/([^/]+)/(inbox|ready)/([^/]+)", path)
        if m:
            jid, side, name = m.groups()
            if not re.fullmatch(r"[0-9A-Za-zА-Яа-я_-]+", jid) or not re.fullmatch(r"[0-9A-Za-z_.-]+", name):
                return _json_response(self, {"error": "bad path"}, 400)
            file_path = ROOT / jid / side / name
            if not file_path.exists() or not file_path.is_file():
                return _json_response(self, {"error": "not found"}, 404)
            raw = file_path.read_bytes()
            suffix = file_path.suffix.lower()
            ctype = "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else "image/jpeg"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)
            return

        m = re.fullmatch(r"/jobs/([^/]+)", path)
        if m:
            jid = m.group(1)
            mf = ROOT / jid / "manifest.json"
            if not mf.exists():
                return _json_response(self, {"error": "not found"}, 404)
            item = json.loads(mf.read_text(encoding="utf-8"))
            inbox = ROOT / jid / "inbox"
            ready = ROOT / jid / "ready"
            item["inbox"] = sorted(p.name for p in inbox.iterdir() if p.is_file()) if inbox.exists() else []
            item["ready"] = sorted(p.name for p in ready.iterdir() if p.is_file()) if ready.exists() else []
            item["imported"] = (ROOT / jid / "imported.flag").exists()
            return _json_response(self, item)

        return _json_response(self, {"error": "not found"}, 404)

    def do_POST(self):
        path = unquote(self.path.split("?", 1)[0])
        m = re.fullmatch(r"/jobs/([^/]+)/ready/([^/]+)", path)
        if not m:
            return _json_response(self, {"error": "not found"}, 404)
        jid, name = m.groups()
        if not re.fullmatch(r"[0-9A-Za-zА-Яа-я_-]+", jid) or not re.fullmatch(r"[0-9A-Za-z_.-]+", name):
            return _json_response(self, {"error": "bad path"}, 400)
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 25 * 1024 * 1024:
            return _json_response(self, {"error": "bad size"}, 400)
        raw = self.rfile.read(length)
        ready = ROOT / jid / "ready"
        ready.mkdir(parents=True, exist_ok=True)
        target = ready / name
        target.write_bytes(raw)
        return _json_response(self, {"ok": True, "job_id": jid, "name": name, "size": len(raw)})


def start_bridge():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), BridgeHandler)
    thread = threading.Thread(target=server.serve_forever, name="media-cloud", daemon=True)
    thread.start()
    logging.getLogger("filinkov-cloud").info("Cloud bridge listening on port %s", PORT)


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
            b.log.exception("Could not stage cloud job")
            await message.reply_text(f"⚠️ Не смог выгрузить фото в облако: {type(exc).__name__}: {exc}")
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
        await message.reply_text(f"☁️ {count} фото выгружены в облако. Жду готовые файлы по ID <code>{jid}</code>.", parse_mode=ParseMode.HTML)


async def poll_ready(context):
    owner = b.DB.get_setting("owner_user_id")
    if not owner:
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
        except Exception:
            continue
        ready = folder / "ready"
        files = sorted([p for p in ready.iterdir() if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]) if ready.exists() else []
        if expected <= 0 or len(files) < expected:
            continue
        files = files[:expected]
        car = b.DB.get_car(car_id)
        if not car:
            continue

        handles = [open(p, "rb") for p in files]
        try:
            caption = b._caption_html(b.render_channel_post(car, owner))
            album = [InputMediaPhoto(media=handles[0], caption=caption, parse_mode=ParseMode.HTML)]
            album.extend(InputMediaPhoto(media=h) for h in handles[1:])
            sent = await context.bot.send_media_group(chat_id=int(owner), media=album)
            file_ids = [m.photo[-1].file_id for m in sent if m.photo]
            if file_ids:
                b.DB.set_branded_media(car_id, file_ids)
                (folder / "imported.flag").write_text("ok", encoding="utf-8")
                await context.bot.send_message(chat_id=int(owner), text=f"✅ Фото FILINKOV готовы и привязаны к машине. ID: {folder.name}")
        except Exception:
            b.log.exception("Could not import ready cloud photos for %s", folder.name)
        finally:
            for h in handles:
                h.close()


async def post_init(app):
    await _original_post_init(app)
    start_bridge()
    app.job_queue.run_repeating(poll_ready, interval=15, first=5, name="cloud-ready-poller")


b.kb_car = kb_car
b.send_car = send_car
b.post_init = post_init

if __name__ == "__main__":
    b.main()
