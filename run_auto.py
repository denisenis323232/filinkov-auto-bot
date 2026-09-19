from __future__ import annotations

import asyncio
import json
import logging
from io import BytesIO

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto

from app import bot as b
from app.brand_images import brand_first_ten, BrandingNotConfigured

logging.getLogger("httpx").setLevel(logging.WARNING)

_original_send_car = b.send_car


def kb_car(car_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Таможня", callback_data=f"customs:{car_id}")],
        [InlineKeyboardButton("✏️ Изменить текст", callback_data=f"edit:{car_id}"), InlineKeyboardButton("🎙 Голос", callback_data=f"voice:{car_id}")],
        [InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{car_id}"), InlineKeyboardButton("🚀 Сейчас", callback_data=f"publish:{car_id}")],
        [InlineKeyboardButton("⏭ Пропустить", callback_data=f"skip:{car_id}"), InlineKeyboardButton("➡️ Следующая", callback_data=f"next:{car_id}")],
    ])


async def _auto_brand(message, car):
    if not car or not car["source_url"]:
        return car

    try:
        if not car["source_description"] or not car["media_json"]:
            source = await asyncio.to_thread(b.extract_card, car["source_url"], b.S.http_timeout)
            b.DB.set_source_card(car["id"], source.media_urls, source.description, source.engine_cc, source.engine_type)
            car = b.DB.get_car(car["id"])
            b.DB.set_car_text(car["id"], b.generate_post(car))

        branded = json.loads(car["branded_media_json"] or "[]") if "branded_media_json" in car.keys() else []
        if branded:
            return car

        media = json.loads(car["media_json"] or "[]")
        photos = [u for u in media if any(ext in u.lower() for ext in (".jpg", ".jpeg", ".png", ".webp"))][:10]
        if not photos:
            return car

        await message.reply_text("⏳ Готовлю первые 10 фото FILINKOV AUTO автоматически...")
        branded_bytes = await asyncio.to_thread(brand_first_ten, photos, b.S.http_timeout)

        album = []
        buffers = []
        for i, raw in enumerate(branded_bytes[:10]):
            bio = BytesIO(raw)
            bio.name = f"filinkov_{car['id']}_{i+1}.png"
            buffers.append(bio)
            album.append(InputMediaPhoto(media=bio))

        sent = await message.reply_media_group(media=album)
        file_ids = [m.photo[-1].file_id for m in sent if m.photo]
        if file_ids:
            b.DB.set_branded_media(car["id"], file_ids)
            car = b.DB.get_car(car["id"])
        return car

    except BrandingNotConfigured:
        await message.reply_text("⚙️ OPENAI_API_KEY не настроен — пока показываю оригинальные фото.")
        return car
    except Exception:
        b.log.exception("Automatic branding failed for car %s", car["id"] if car else "?")
        await message.reply_text("⚠️ Не смог обработать фото автоматически — показываю оригинальные.")
        return car


async def send_car(message, car):
    car = await _auto_brand(message, car)
    return await _original_send_car(message, car)


b.kb_car = kb_car
b.send_car = send_car

if __name__ == "__main__":
    b.main()
