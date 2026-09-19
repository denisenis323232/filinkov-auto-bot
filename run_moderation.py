from __future__ import annotations

import asyncio
import io
import json
import re
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.constants import ParseMode

import run_hotfix as h

p = h.p
b = p.b
r = p.r
TZ = ZoneInfo(b.S.timezone)
QUEUE_KEY = "moderation_queue"
_original_buttons = b.buttons
_original_post_init = b.post_init


def kb_car(car_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👀 Посмотреть пост", callback_data=f"preview:{car_id}")],
        [InlineKeyboardButton("📸 Исходники поштучно", callback_data=f"originals:{car_id}"), InlineKeyboardButton("🔄 Забрать READY", callback_data=f"syncmedia:{car_id}")],
        [InlineKeyboardButton("✏️ Изменить текст", callback_data=f"edit:{car_id}"), InlineKeyboardButton("🎙 Голос", callback_data=f"voice:{car_id}")],
        [InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{car_id}"), InlineKeyboardButton("📅 В очередь", callback_data=f"modqueue:{car_id}")],
        [InlineKeyboardButton("🚀 Опубликовать сейчас", callback_data=f"publish:{car_id}"), InlineKeyboardButton("⏭ Пропустить", callback_data=f"skip:{car_id}")],
    ])


def _load_queue() -> list[dict]:
    raw = b.DB.get_setting(QUEUE_KEY, "[]") or "[]"
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except Exception:
        return []


def _save_queue(items: list[dict]) -> None:
    b.DB.set_setting(QUEUE_KEY, json.dumps(items, ensure_ascii=False))


def _next_slot(items: list[dict]) -> datetime:
    now = datetime.now(TZ)
    hours = sorted({int(x) for x in (b.S.publish_hours or (12, 18))}) or [12, 18]
    occupied = {
        datetime.fromisoformat(x["at"]).astimezone(TZ).replace(second=0, microsecond=0)
        for x in items if x.get("at") and x.get("state") != "published"
    }
    for day_add in range(0, 60):
        day = (now + timedelta(days=day_add)).date()
        for hour in hours:
            slot = datetime.combine(day, dt_time(hour, 0), tzinfo=TZ)
            if slot <= now:
                continue
            if slot.replace(second=0, microsecond=0) not in occupied:
                return slot
    return now + timedelta(hours=1)


def _numbered_images(entries: list[dict]) -> list[dict]:
    """Only accept individual photo slots 01..10. A collage/other filename is ignored."""
    found: dict[int, dict] = {}
    for entry in entries:
        if entry.get(".tag") != "file":
            continue
        name = str(entry.get("name") or "").lower()
        m = re.fullmatch(r"(0[1-9]|10)\.(jpg|jpeg|png|webp)", name)
        if m:
            found[int(m.group(1))] = entry
    return [found[n] for n in sorted(found)]


def _manifest_expected(car, jid: str) -> int:
    expected = min(10, len(r._photo_urls(car)))
    if expected:
        return expected
    try:
        raw = r._dbx_download(f"{r.DROPBOX_ROOT}/inbox/{jid}/manifest.json", max(60, b.S.http_timeout))
        data = json.loads(raw.decode("utf-8"))
        return max(0, min(10, int(data.get("expected") or 0)))
    except Exception:
        return 0


def _ready_files(car):
    jid = r.job_id(car)
    path = f"{r.DROPBOX_ROOT}/ready/{jid}"
    files = _numbered_images(r._dbx_list(path))
    expected = _manifest_expected(car, jid) or min(10, len(files))
    by_num = {int(str(f.get("name", "00"))[:2]): f for f in files}
    selected = [by_num[i] for i in range(1, expected + 1) if i in by_num]
    missing = [i for i in range(1, expected + 1) if i not in by_num]
    return jid, expected, selected, missing


def _original_files(car):
    jid = r.job_id(car)
    path = f"{r.DROPBOX_ROOT}/inbox/{jid}"
    files = _numbered_images(r._dbx_list(path))
    expected = _manifest_expected(car, jid) or min(10, len(files))
    return jid, expected, files[:expected]


def _media_state_path(jid: str) -> str:
    return f"{r.DROPBOX_ROOT}/ready/{jid}/telegram_media.json"


def _load_media_state(jid: str) -> dict:
    try:
        raw = r._dbx_download(_media_state_path(jid), max(60, b.S.http_timeout))
        value = json.loads(raw.decode("utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _save_media_state(jid: str, car_id: int, file_ids: list[str], notified: bool = True) -> None:
    payload = {
        "job_id": jid,
        "car_id": car_id,
        "telegram_file_ids": file_ids,
        "notified": notified,
        "mode": "individual_files_01_to_10",
        "updated_at": datetime.now(TZ).isoformat(),
    }
    r._dbx_upload(_media_state_path(jid), json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"), timeout=max(60, b.S.http_timeout))


def _restore_media_state(car) -> list[str]:
    jid = r.job_id(car)
    state = _load_media_state(jid)
    ids = state.get("telegram_file_ids") or []
    if isinstance(ids, list) and ids:
        ids = [str(x) for x in ids][:10]
        b.DB.set_branded_media(car["id"], ids)
        return ids
    return []


async def _send_ready_album(reply_message, car, files: list[dict], jid: str) -> list[str]:
    raws = [await asyncio.to_thread(r._dbx_download, f["path_lower"], max(60, b.S.http_timeout)) for f in files]
    buffers = []
    album = []
    caption = b._caption_html(b.render_channel_post(car, b.DB.get_setting("owner_user_id")))
    for i, raw in enumerate(raws):
        bio = io.BytesIO(raw)
        bio.name = files[i].get("name") or f"{i + 1:02d}.jpg"
        buffers.append(bio)
        album.append(InputMediaPhoto(media=bio, caption=caption if i == 0 else None, parse_mode=ParseMode.HTML if i == 0 else None))
    try:
        sent = await reply_message.reply_media_group(media=album)
        file_ids = [m.photo[-1].file_id for m in sent if m.photo]
        if len(file_ids) == len(files):
            b.DB.set_branded_media(car["id"], file_ids)
            await asyncio.to_thread(_save_media_state, jid, int(car["id"]), file_ids, True)
        return file_ids
    finally:
        for bio in buffers:
            bio.close()


async def _send_ready_album_to_chat(bot, chat_id: int, car, files: list[dict], jid: str) -> list[str]:
    raws = [await asyncio.to_thread(r._dbx_download, f["path_lower"], max(60, b.S.http_timeout)) for f in files]
    buffers = []
    album = []
    caption = b._caption_html(b.render_channel_post(car, b.DB.get_setting("owner_user_id")))
    for i, raw in enumerate(raws):
        bio = io.BytesIO(raw)
        bio.name = files[i].get("name") or f"{i + 1:02d}.jpg"
        buffers.append(bio)
        album.append(InputMediaPhoto(media=bio, caption=caption if i == 0 else None, parse_mode=ParseMode.HTML if i == 0 else None))
    try:
        sent = await bot.send_media_group(chat_id=chat_id, media=album)
        file_ids = [m.photo[-1].file_id for m in sent if m.photo]
        if len(file_ids) == len(files):
            b.DB.set_branded_media(car["id"], file_ids)
            await asyncio.to_thread(_save_media_state, jid, int(car["id"]), file_ids, True)
        return file_ids
    finally:
        for bio in buffers:
            bio.close()


async def _import_ready_to_telegram(message, car):
    jid, expected, files, missing = await asyncio.to_thread(_ready_files, car)
    branded = json.loads(car["branded_media_json"] or "[]") if "branded_media_json" in car.keys() else []
    if len(branded) == expected and expected > 0:
        return branded

    restored = await asyncio.to_thread(_restore_media_state, car)
    if len(restored) == expected and expected > 0:
        return restored

    if expected <= 0:
        await message.reply_text(f"⚠️ Не знаю ожидаемое число фото для ID <code>{jid}</code>.", parse_mode=ParseMode.HTML)
        return []
    if missing:
        await message.reply_text(
            f"⏳ Готово <b>{len(files)}/{expected}</b> отдельных фото. "
            f"Не хватает: <code>{', '.join(f'{n:02d}' for n in missing)}</code>.\n"
            "Бот не возьмёт коллаж или файл с другим именем — только 01.jpg … 10.jpg.",
            parse_mode=ParseMode.HTML,
        )
        return []
    return await _send_ready_album(message, car, files, jid)


async def _ensure_price(car):
    car, _breakdown, price_error = await r._ensure_source_and_price(car, force_price=True)
    if price_error:
        return car, price_error
    return car, None


async def _show_originals_separately(message, car):
    jid, expected, files = await asyncio.to_thread(_original_files, car)
    if not files:
        await message.reply_text(f"Оригиналы ещё не скачаны. ID: <code>{jid}</code>", parse_mode=ParseMode.HTML)
        return
    await message.reply_text(f"📸 {len(files)}/{expected} оригиналов. Отправляю <b>каждую фотографию отдельным сообщением</b>.", parse_mode=ParseMode.HTML)
    for i, entry in enumerate(files, 1):
        raw = await asyncio.to_thread(r._dbx_download, entry["path_lower"], max(60, b.S.http_timeout))
        bio = io.BytesIO(raw)
        bio.name = entry.get("name") or f"{i:02d}.jpg"
        try:
            await message.reply_photo(photo=bio, caption=f"{entry.get('name', f'{i:02d}.jpg')} · {car['model']} №{car['inventory_no']}")
        finally:
            bio.close()


async def buttons(update, context):
    q = update.callback_query
    if not q:
        return
    data = q.data or ""
    action, _, payload = data.partition(":")

    if action == "preview":
        if not await b.guard(update):
            return
        await q.answer()
        car = b.DB.get_car(int(payload))
        if not car:
            await q.message.reply_text("Машина не найдена.")
            return
        car, price_error = await _ensure_price(car)
        if price_error:
            await q.message.reply_text(f"⚠️ Цена пока не рассчитана: {price_error}")
        await _import_ready_to_telegram(q.message, car)
        return

    if action == "originals":
        if not await b.guard(update):
            return
        await q.answer()
        car = b.DB.get_car(int(payload))
        if car:
            await _show_originals_separately(q.message, car)
        return

    if action == "syncmedia":
        if not await b.guard(update):
            return
        await q.answer()
        car = b.DB.get_car(int(payload))
        if not car:
            return
        media = await _import_ready_to_telegram(q.message, car)
        if media:
            await q.message.reply_text(f"✅ Подхватил {len(media)} отдельных фото. Можно согласовывать.", reply_markup=kb_car(int(payload)))
        return

    if action == "modqueue":
        if not await b.guard(update):
            return
        await q.answer()
        car_id = int(payload)
        car = b.DB.get_car(car_id)
        if not car:
            return
        car, price_error = await _ensure_price(car)
        jid, expected, _files, missing = await asyncio.to_thread(_ready_files, car)
        branded = json.loads(car["branded_media_json"] or "[]") if car else []
        if price_error or not car["final_price_rub"]:
            await q.message.reply_text(f"💰 В очередь не ставлю: цена не готова ({price_error or 'нет итоговой цены'}).")
            return
        if missing:
            await q.message.reply_text(f"📸 В очередь не ставлю: готово не всё. Не хватает {', '.join(f'{n:02d}' for n in missing)}.")
            return
        if len(branded) != expected:
            branded = await _import_ready_to_telegram(q.message, car)
        if expected <= 0 or len(branded) != expected:
            await q.message.reply_text(f"📸 В очередь не ставлю: нужно {expected or 10} отдельных готовых фото FILINKOV.")
            return
        if car["status"] != "APPROVED":
            await q.message.reply_text("✅ Сначала нажми «Одобрить».")
            return
        items = _load_queue()
        if any(int(x.get("car_id", -1)) == car_id and x.get("state") != "published" for x in items):
            await q.message.reply_text("📅 Эта машина уже стоит в очереди.")
            return
        slot = _next_slot(items)
        items.append({"car_id": car_id, "at": slot.isoformat(), "state": "queued"})
        _save_queue(items)
        await q.message.reply_text(f"📅 Поставил в очередь: <b>{slot:%d.%m в %H:%M}</b>", parse_mode=ParseMode.HTML)
        return

    if action == "publish":
        if not await b.guard(update):
            return
        car_id = int(payload)
        car = b.DB.get_car(car_id)
        if not car:
            await q.answer()
            return
        if car["status"] != "APPROVED":
            await q.answer()
            await q.message.reply_text("✅ Сначала нажми «Одобрить». Без согласования публикация заблокирована.")
            return
        jid, expected, _files, missing = await asyncio.to_thread(_ready_files, car)
        branded = json.loads(car["branded_media_json"] or "[]")
        if missing or len(branded) != expected:
            await q.answer()
            if not missing:
                branded = await _import_ready_to_telegram(q.message, car)
            if missing or len(branded) != expected:
                await q.message.reply_text(f"📸 Публикация заблокирована: нужны все {expected or 10} отдельных фото.")
                return
        # Let the original handler publish after our approval/photo gate.
        return await _original_buttons(update, context)

    await _original_buttons(update, context)


async def ready_moderation_tick(context):
    """When all numbered ready photos exist, send exactly one moderation package. Never publishes."""
    owner = b.DB.get_setting("owner_user_id")
    if not owner:
        return
    try:
        owner_id = int(owner)
    except Exception:
        return

    cars = b.DB.list_cars(("READY", "APPROVED"), limit=500)
    for car in cars:
        try:
            jid, expected, files, missing = await asyncio.to_thread(_ready_files, car)
            if expected <= 0 or missing or len(files) != expected:
                continue

            branded = json.loads(car["branded_media_json"] or "[]")
            if len(branded) == expected:
                continue

            state = await asyncio.to_thread(_load_media_state, jid)
            ids = state.get("telegram_file_ids") or []
            if isinstance(ids, list) and len(ids) == expected:
                b.DB.set_branded_media(car["id"], [str(x) for x in ids])
                continue

            file_ids = await _send_ready_album_to_chat(context.bot, owner_id, car, files, jid)
            if len(file_ids) != expected:
                continue
            await context.bot.send_message(
                chat_id=owner_id,
                text=(
                    f"✅ <b>{car['model']}</b> №{car['inventory_no']}\n"
                    f"Готовы <b>{expected} отдельных фото</b> 01…{expected:02d}.\n"
                    "Никаких коллажей. Проверь пост и нажми «Одобрить», потом «В очередь»."
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=kb_car(int(car["id"])),
            )
            # One new moderation package per tick to avoid message floods.
            break
        except Exception:
            b.log.exception("Ready moderation import failed for car %s", car["id"])


async def moderation_tick(context):
    items = _load_queue()
    if not items:
        return
    now = datetime.now(TZ)
    changed = False
    for item in items:
        if item.get("state") == "published":
            continue
        try:
            at = datetime.fromisoformat(item["at"]).astimezone(TZ)
        except Exception:
            continue
        if at > now:
            continue
        car_id = int(item["car_id"])
        car = b.DB.get_car(car_id)
        if not car or car["status"] != "APPROVED" or not car["final_price_rub"]:
            continue
        jid, expected, _files, missing = await asyncio.to_thread(_ready_files, car)
        branded = json.loads(car["branded_media_json"] or "[]")
        if missing or expected <= 0 or len(branded) != expected:
            continue
        channel = b.DB.get_setting("channel_id", b.S.channel_id)
        if not channel:
            continue
        caption = b._caption_html(b.render_channel_post(car, b.DB.get_setting("owner_user_id")))
        try:
            album = [InputMediaPhoto(media=branded[0], caption=caption, parse_mode=ParseMode.HTML)]
            album.extend(InputMediaPhoto(media=x) for x in branded[1:10])
            await context.bot.send_media_group(chat_id=channel, media=album)
            b.DB.mark_published(car_id)
            item["state"] = "published"
            changed = True
        except Exception:
            b.log.exception("Moderation queue publish failed for car %s", car_id)
    if changed:
        _save_queue(items)


async def post_init(app):
    await _original_post_init(app)
    app.job_queue.run_repeating(ready_moderation_tick, interval=20, first=8, name="individual-ready-moderation")
    app.job_queue.run_repeating(moderation_tick, interval=30, first=10, name="moderation-publish-tick")


b.kb_car = kb_car
r.kb_car = kb_car
b.buttons = buttons
r.b.buttons = buttons
b.post_init = post_init

# Old background processors/publishers remain disabled. This module only imports completed
# individual files from ready and puts them in front of the owner for explicit approval.
r.poll_ready = h.quiet_poll_ready
h.w.weekly_publish_tick = h.safe_weekly_tick

if __name__ == "__main__":
    b.main()
