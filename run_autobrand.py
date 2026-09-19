from __future__ import annotations

import asyncio
import html
import json
import logging
from io import BytesIO

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.constants import ParseMode

from app import bot as b
from app.brand_images import brand_first_ten, BrandingNotConfigured, BrandingFailed
from app.source_avtomir import extract_card
from app.texts import generate_post, render_channel_post, rub

logging.getLogger("httpx").setLevel(logging.WARNING)


def kb_car(car_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Таможня", callback_data=f"customs:{car_id}"), InlineKeyboardButton("✏️ Изменить текст", callback_data=f"edit:{car_id}")],
        [InlineKeyboardButton("🎙 Голос", callback_data=f"voice:{car_id}"), InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{car_id}")],
        [InlineKeyboardButton("🚀 Сейчас", callback_data=f"publish:{car_id}"), InlineKeyboardButton("⏭ Пропустить", callback_data=f"skip:{car_id}")],
        [InlineKeyboardButton("➡️ Следующая", callback_data=f"next:{car_id}")],
    ])


def branded_ids(car):
    if "branded_media_json" not in car.keys() or not car["branded_media_json"]:
        return []
    try:
        return json.loads(car["branded_media_json"] or "[]")[:10]
    except Exception:
        return []


async def ensure_branded(message, car):
    existing = branded_ids(car)
    if existing:
        return b.DB.get_car(car["id"])

    if not car["source_url"]:
        await message.reply_text("⚠️ У машины нет ссылки на карточку поставщика.")
        return None

    await message.reply_text("⏳ Готовлю 10 фото FILINKOV AUTO...")
    source = extract_card(car["source_url"], b.S.http_timeout)
    b.DB.set_source_card(car["id"], source.media_urls, source.description, source.engine_cc, source.engine_type)
    car = b.DB.get_car(car["id"])
    b.DB.set_car_text(car["id"], generate_post(car))

    photos = [u for u in source.media_urls if any(x in u.lower() for x in (".jpg", ".jpeg", ".png", ".webp"))][:10]
    if not photos:
        await message.reply_text("⚠️ В карточке не нашёл фотографий.")
        return None

    try:
        branded = await asyncio.to_thread(brand_first_ten, photos, b.S.http_timeout)
    except BrandingNotConfigured:
        await message.reply_text("⚠️ OPENAI_API_KEY не настроен.")
        return None
    except BrandingFailed as exc:
        await message.reply_text(f"⚠️ Брендинг фото не прошёл: {exc}")
        return None
    except Exception as exc:
        await message.reply_text(f"⚠️ Ошибка обработки фото: {type(exc).__name__}: {exc}")
        return None

    # Upload edited images once to Telegram to obtain reusable file_ids.
    buffers = []
    upload = []
    for i, raw in enumerate(branded[:10]):
        bio = BytesIO(raw)
        bio.name = f"filinkov_{car['id']}_{i+1}.png"
        buffers.append(bio)
        upload.append(InputMediaPhoto(media=bio))

    sent = await message.reply_media_group(media=upload)
    ids = [m.photo[-1].file_id for m in sent if m.photo]
    b.DB.set_branded_media(car["id"], ids)
    return b.DB.get_car(car["id"])


async def send_branded_post(message, car):
    ids = branded_ids(car)
    if not ids:
        return
    caption = b._caption_html(render_channel_post(car, b.DB.get_setting("owner_user_id")))
    album = [InputMediaPhoto(media=ids[0], caption=caption, parse_mode=ParseMode.HTML)]
    album.extend(InputMediaPhoto(media=x) for x in ids[1:])
    await message.reply_media_group(media=album)


async def send_car(message, car):
    car = await ensure_branded(message, car)
    if not car:
        return

    price = f"<b>{rub(car['final_price_rub'])} ₽</b>" if car["final_price_rub"] else "<i>ещё не рассчитана</i>"
    text = (
        f"🚗 <b>{html.escape(car['model'] or '—')}</b>\n"
        f"№ {html.escape(car['inventory_no'] or '—')}\n\n"
        f"{html.escape(str(car['month_text'] or car['year'] or '—'))}\n"
        f"Пробег: {rub(car['mileage_km'])} км\n"
        f"Мощность: {car['power_hp'] or '—'} л.с.\n"
        f"Комплектация: {html.escape(car['trim'] or '—')}\n\n"
        f"💸 ИТОГОВАЯ ЦЕНА В МОСКВЕ: {price}\n\n"
        f"Статус: <b>{html.escape(car['status'])}</b>"
    )
    await message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_car(car["id"]), disable_web_page_preview=True)
    await send_branded_post(message, car)


b.kb_car = kb_car
b.send_car = send_car

if __name__ == "__main__":
    b.main()
