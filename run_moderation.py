from __future__ import annotations

import asyncio
import io
import json
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
    occupied = {datetime.fromisoformat(x["at"]).astimezone(TZ).replace(second=0, microsecond=0) for x in items if x.get("at") and x.get("state") != "published"}
    for day_add in range(0, 60):
        day = (now + timedelta(days=day_add)).date()
        for hour in hours:
            slot = datetime.combine(day, dt_time(hour, 0), tzinfo=TZ)
            if slot <= now:
                continue
            if slot.replace(second=0, microsecond=0) not in occupied:
                return slot
    return now + timedelta(hours=1)


def _ready_files(car):
    jid = r.job_id(car)
    path = f"{r.DROPBOX_ROOT}/ready/{jid}"
    files = r._image_entries(r._dbx_list(path))
    expected = len(r._photo_urls(car)) or min(10, len(files))
    return jid, files[: min(10, expected or 10)]


async def _import_ready_to_telegram(message, car):
    branded = json.loads(car["branded_media_json"] or "[]") if "branded_media_json" in car.keys() else []
    if branded:
        return branded

    jid, files = await asyncio.to_thread(_ready_files, car)
    if not files:
        await message.reply_text(
            f"⏳ Для этой машины ещё нет готовых обработанных фото.\nID: <code>{jid}</code>",
            parse_mode=ParseMode.HTML,
        )
        return []

    raws = [await asyncio.to_thread(r._dbx_download, f["path_lower"], max(60, b.S.http_timeout)) for f in files]
    buffers = []
    album = []
    caption = b._caption_html(b.render_channel_post(car, b.DB.get_setting("owner_user_id")))
    for i, raw in enumerate(raws):
        bio = io.BytesIO(raw)
        bio.name = files[i].get("name") or f"filinkov_{jid}_{i+1}.jpg"
        buffers.append(bio)
        album.append(InputMediaPhoto(media=bio, caption=caption if i == 0 else None, parse_mode=ParseMode.HTML if i == 0 else None))
    try:
        sent = await message.reply_media_group(media=album)
        file_ids = [m.photo[-1].file_id for m in sent if m.photo]
        if file_ids:
            b.DB.set_branded_media(car["id"], file_ids)
        return file_ids
    finally:
        for bio in buffers:
            bio.close()


async def _ensure_price(car):
    car, _breakdown, price_error = await r._ensure_source_and_price(car, force_price=True)
    if price_error:
        return car, price_error
    return car, None


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

    if action == "modqueue":
        if not await b.guard(update):
            return
        await q.answer()
        car_id = int(payload)
        car = b.DB.get_car(car_id)
        if not car:
            return
        car, price_error = await _ensure_price(car)
        branded = json.loads(car["branded_media_json"] or "[]") if car else []
        if price_error or not car["final_price_rub"]:
            await q.message.reply_text(f"💰 В очередь не ставлю: цена не готова ({price_error or 'нет итоговой цены'}).")
            return
        if not branded:
            branded = await _import_ready_to_telegram(q.message, car)
        if not branded:
            await q.message.reply_text("📸 В очередь не ставлю: нет готовых фото FILINKOV.")
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

    await _original_buttons(update, context)


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
        branded = json.loads(car["branded_media_json"] or "[]")
        if not branded:
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
    app.job_queue.run_repeating(moderation_tick, interval=30, first=10, name="moderation-publish-tick")


b.kb_car = kb_car
r.kb_car = kb_car
b.buttons = buttons
r.b.buttons = buttons
b.post_init = post_init

# No background Dropbox->Telegram album imports and no legacy weekly autopublish.
r.poll_ready = h.quiet_poll_ready
h.w.weekly_publish_tick = h.safe_weekly_tick

if __name__ == "__main__":
    b.main()
