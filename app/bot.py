from __future__ import annotations

import asyncio
import html
import json
import logging
from io import BytesIO
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)

from .brand_images import brand_first_ten, BrandingNotConfigured
from .config import load_settings
from .db import Database
from .excel_parser import parse_excel
from .pricing import fetch_cny_rub, final_price
from .source_avtomir import extract_card
from .texts import generate_post, render_channel_post, generate_voice_script, rub

log = logging.getLogger("filinkov-auto")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
S = load_settings()
DB = Database(S.data_dir / "filinkov_auto.sqlite3")


def kb_home():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Загрузить Excel", callback_data="upload_excel")],
        [InlineKeyboardButton("🚗 Очередь", callback_data="queue:0"), InlineKeyboardButton("📈 Статус", callback_data="stats")],
        [InlineKeyboardButton("⚙️ Настройки", callback_data="settings")],
    ])


def kb_car(car_id: int):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ 10 фото FILINKOV", callback_data=f"media:{car_id}"), InlineKeyboardButton("💰 Таможня", callback_data=f"customs:{car_id}")],
        [InlineKeyboardButton("✏️ Изменить текст", callback_data=f"edit:{car_id}"), InlineKeyboardButton("🎙 Голос", callback_data=f"voice:{car_id}")],
        [InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{car_id}"), InlineKeyboardButton("🚀 Сейчас", callback_data=f"publish:{car_id}")],
        [InlineKeyboardButton("⏭ Пропустить", callback_data=f"skip:{car_id}"), InlineKeyboardButton("➡️ Следующая", callback_data=f"next:{car_id}")],
    ])


def _caption_html(text: str, limit: int = 1024) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    nl = cut.rfind("\n")
    if nl >= 700:
        cut = cut[:nl]
    return cut.rstrip() + "…"


def is_admin(user_id: int) -> bool:
    owner = DB.get_setting("owner_user_id")
    if owner is None:
        DB.set_setting("owner_user_id", str(user_id))
        return True
    return str(user_id) == owner


async def guard(update: Update) -> bool:
    user = update.effective_user
    if user and is_admin(user.id):
        return True
    if update.callback_query:
        await update.callback_query.answer("Нет доступа", show_alert=True)
    elif update.effective_message:
        await update.effective_message.reply_text("Нет доступа.")
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await update.message.reply_text(
        "🚗 <b>FILINKOV AUTO — контент-менеджер</b>\n\n"
        "Пришли Excel поставщика. Я отфильтрую машины, заберу описание и первые 10 фото и подготовлю пост.",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_home(),
    )


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await update.message.reply_text(f"Твой Telegram ID: <code>{update.effective_user.id}</code>", parse_mode=ParseMode.HTML)


async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    doc = update.message.document
    name = doc.file_name or "supplier.xlsx"
    if not name.lower().endswith((".xls", ".xlsx")):
        await update.message.reply_text("Нужен Excel: .xls или .xlsx")
        return
    await update.message.reply_text("⏳ Excel получил. Разбираю и фильтрую...")
    folder = S.data_dir / "imports"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    f = await doc.get_file()
    await f.download_to_drive(custom_path=target)

    import_id = DB.create_import(name)
    try:
        result = parse_excel(target, import_id, S.min_age_years, S.max_age_years, S.max_power_hp)
        for car in result.cars:
            DB.upsert_car(car)
        DB.update_import_counts(import_id, result.total, result.accepted, result.manual, result.rejected)
    except Exception as exc:
        log.exception("Excel parse failed")
        await update.message.reply_text(f"❌ Не смог разобрать Excel: {exc}")
        return

    await update.message.reply_text(
        "✅ <b>Excel обработан</b>\n\n"
        f"Всего: <b>{result.total}</b>\n"
        f"Подходят: <b>{result.accepted}</b>\n"
        f"Ручная проверка: <b>{result.manual}</b>\n"
        f"Отсеяно: <b>{result.rejected}</b>\n\n"
        "Открывай очередь 👇",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚗 Открыть очередь", callback_data="queue:0")]]),
    )


def _photo_sources(car):
    branded = json.loads(car["branded_media_json"] or "[]") if "branded_media_json" in car.keys() else []
    if branded:
        return branded[:10]
    media = json.loads(car["media_json"] or "[]")
    return [u for u in media if any(x in u.lower() for x in (".jpg", ".jpeg", ".png", ".webp"))][:10]


async def send_post_preview(message, car):
    caption = _caption_html(render_channel_post(car, DB.get_setting("owner_user_id")))
    photos = _photo_sources(car)
    if photos:
        album = [InputMediaPhoto(media=photos[0], caption=caption, parse_mode=ParseMode.HTML)]
        album.extend(InputMediaPhoto(media=u) for u in photos[1:])
        await message.reply_media_group(media=album)
    else:
        await message.reply_text(caption, parse_mode=ParseMode.HTML)


async def send_car(message, car):
    if car["source_url"] and not car["source_description"]:
        try:
            source = extract_card(car["source_url"], S.http_timeout)
            DB.set_source_card(car["id"], source.media_urls, source.description, source.engine_cc, source.engine_type)
            car = DB.get_car(car["id"])
        except Exception:
            log.exception("Source enrichment failed for car %s", car["id"])

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
    await send_post_preview(message, car)


async def queue_from(message, offset: int = 0):
    cars = DB.list_cars(("READY", "APPROVED"), limit=200)
    if not cars:
        await message.reply_text("Очередь пустая. Загрузи Excel.", reply_markup=kb_home())
        return
    idx = max(0, min(offset, len(cars) - 1))
    await send_car(message, cars[idx])


async def stats(message):
    counts = DB.count_by_status()
    labels = {"READY": "Готовы", "MANUAL": "Ручная проверка", "REJECTED": "Отсеяны", "APPROVED": "Одобрены", "PUBLISHED": "Опубликованы", "SKIPPED": "Пропущены"}
    lines = ["📈 <b>Статус базы</b>", ""]
    for key, value in counts.items():
        lines.append(f"{labels.get(key, key)}: <b>{value}</b>")
    await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def brand_media(q, car_id: int):
    car = DB.get_car(car_id)
    if not car["source_url"]:
        await q.message.reply_text("У машины нет ссылки на карточку.")
        return
    await q.message.reply_text(
        "⏳ Готовлю первые 10 фото. Меняю только вывеску/логотип поставщика и номер. Сам автомобиль, фон, свет и ракурс не трогаю."
    )
    source = extract_card(car["source_url"], S.http_timeout)
    DB.set_source_card(car_id, source.media_urls, source.description, source.engine_cc, source.engine_type)
    car = DB.get_car(car_id)
    DB.set_car_text(car_id, generate_post(car))
    photos = [u for u in source.media_urls if any(x in u.lower() for x in (".jpg", ".jpeg", ".png", ".webp"))][:10]
    if not photos:
        await q.message.reply_text("⚠️ В карточке не нашёл фотографий.")
        return
    try:
        branded_bytes = await asyncio.to_thread(brand_first_ten, photos, S.http_timeout)
    except BrandingNotConfigured:
        await q.message.reply_text(
            "⚙️ Брендинг фото уже встроен, но на Railway ещё не задан OPENAI_API_KEY. "
            "После добавления ключа эта кнопка автоматически обработает все 10 фото."
        )
        return

    caption = _caption_html(render_channel_post(DB.get_car(car_id), DB.get_setting("owner_user_id")))
    buffers = []
    album = []
    for i, raw in enumerate(branded_bytes[:10]):
        bio = BytesIO(raw)
        bio.name = f"filinkov_{car_id}_{i+1}.png"
        buffers.append(bio)
        if i == 0:
            album.append(InputMediaPhoto(media=bio, caption=caption, parse_mode=ParseMode.HTML))
        else:
            album.append(InputMediaPhoto(media=bio))
    sent = await q.message.reply_media_group(media=album)
    file_ids = [m.photo[-1].file_id for m in sent if m.photo]
    DB.set_branded_media(car_id, file_ids)
    await q.message.reply_text(f"✅ Готово: {len(file_ids)} фото FILINKOV AUTO сохранены для публикации.")


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    q = update.callback_query
    await q.answer()
    action, _, payload = q.data.partition(":")

    if action == "upload_excel":
        await q.message.reply_text("📊 Пришли сюда Excel-файл поставщика (.xls/.xlsx).")
    elif action == "queue":
        await queue_from(q.message, int(payload or 0))
    elif action == "stats":
        await stats(q.message)
    elif action == "settings":
        channel = DB.get_setting("channel_id", S.channel_id or "не задан")
        await q.message.reply_text(
            "⚙️ <b>Настройки</b>\n\n"
            f"Канал: <code>{html.escape(channel)}</code>\n"
            f"Возраст: {S.min_age_years}–{S.max_age_years} лет\n"
            f"Макс. мощность: {S.max_power_hp} л.с.",
            parse_mode=ParseMode.HTML,
        )
    elif action == "next":
        cars = DB.list_cars(("READY", "APPROVED"), limit=200)
        ids = [c["id"] for c in cars]
        try:
            idx = ids.index(int(payload)) + 1
        except ValueError:
            idx = 0
        if idx >= len(cars):
            idx = 0
        if cars:
            await send_car(q.message, cars[idx])
    elif action == "skip":
        DB.set_car_status(int(payload), "SKIPPED")
        await q.message.reply_text("⏭ Пропущено.")
    elif action == "approve":
        car_id = int(payload)
        car = DB.get_car(car_id)
        if not car["final_price_rub"]:
            await q.message.reply_text("💰 Сначала нужна конечная цена.")
            return
        DB.set_car_text(car_id, generate_post(car))
        DB.set_car_status(car_id, "APPROVED")
        await q.message.reply_text("✅ Одобрено.")
    elif action == "customs":
        context.user_data["await_customs_for"] = int(payload)
        await q.message.reply_text("💰 Пришли сумму таможни по ТКС в рублях одним числом. Например: <code>612000</code>", parse_mode=ParseMode.HTML)
    elif action == "edit":
        car = DB.get_car(int(payload))
        current = car["post_text"] or generate_post(car)
        context.user_data["await_text_for"] = int(payload)
        await q.message.reply_text("✏️ Пришли новым сообщением готовый текст поста.\n\nТекущий вариант:\n\n" + current)
    elif action == "voice":
        car = DB.get_car(int(payload))
        context.user_data["await_voice_for"] = int(payload)
        await q.message.reply_text("🎙 Запиши и пришли голосовое. Подсказка:\n\n" + generate_voice_script(car))
    elif action == "media":
        try:
            await brand_media(q, int(payload))
        except Exception as exc:
            log.exception("Branding/source card failed")
            await q.message.reply_text(f"⚠️ Не удалось подготовить фото: {type(exc).__name__}: {exc}")
    elif action == "publish":
        car = DB.get_car(int(payload))
        if not car or not car["final_price_rub"]:
            await q.message.reply_text("💰 Публикация заблокирована: сначала должна быть рассчитана конечная цена.")
            return
        await publish_car(q.message, context, int(payload))


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    text = (update.message.text or "").strip()
    if "await_customs_for" in context.user_data:
        car_id = context.user_data.pop("await_customs_for")
        try:
            customs = float(text.replace(" ", "").replace(",", "."))
            car = DB.get_car(car_id)
            rate = fetch_cny_rub(S.http_timeout)
            total = final_price(car["price_cny"], rate, customs, S.extra_cny, S.delivery_rub, S.other_rub)
            DB.set_customs(car_id, customs, rate, total)
            DB.set_car_text(car_id, generate_post(DB.get_car(car_id)))
            await update.message.reply_text(f"✅ <b>ИТОГОВАЯ ЦЕНА В МОСКВЕ: {rub(total)} ₽</b>", parse_mode=ParseMode.HTML)
        except Exception as exc:
            await update.message.reply_text(f"❌ Не получилось посчитать: {exc}")
        return
    if "await_text_for" in context.user_data:
        car_id = context.user_data.pop("await_text_for")
        DB.set_car_text(car_id, text)
        await update.message.reply_text("✅ Текст сохранён.")


async def voice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    car_id = context.user_data.pop("await_voice_for", None)
    if not car_id:
        await update.message.reply_text("Сначала открой машину и нажми «🎙 Голос».")
        return
    DB.set_voice(car_id, update.message.voice.file_id)
    await update.message.reply_text("✅ Голосовое прикреплено к машине.")


async def setchannel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if not context.args:
        await update.message.reply_text("Использование: /setchannel @filinkovauto")
        return
    channel = context.args[0].strip()
    DB.set_setting("channel_id", channel)
    await update.message.reply_text(f"✅ Канал сохранён: {channel}")


async def publish_car(message, context: ContextTypes.DEFAULT_TYPE, car_id: int):
    car = DB.get_car(car_id)
    if not car:
        await message.reply_text("Машина не найдена.")
        return
    channel = DB.get_setting("channel_id", S.channel_id)
    if not channel:
        await message.reply_text("Сначала задай канал: /setchannel @filinkovauto")
        return
    caption = _caption_html(render_channel_post(car, DB.get_setting("owner_user_id")))
    photos = _photo_sources(car)
    media = json.loads(car["media_json"] or "[]")
    videos = [u for u in media if any(x in u.lower() for x in (".mp4", ".mov"))][:1]
    try:
        if photos:
            album = [InputMediaPhoto(media=photos[0], caption=caption, parse_mode=ParseMode.HTML)]
            album.extend(InputMediaPhoto(media=u) for u in photos[1:10])
            await context.bot.send_media_group(chat_id=channel, media=album)
        else:
            await context.bot.send_message(chat_id=channel, text=caption, parse_mode=ParseMode.HTML)
        if videos:
            await context.bot.send_video(chat_id=channel, video=videos[0])
        if car["voice_file_id"]:
            await context.bot.send_voice(chat_id=channel, voice=car["voice_file_id"])
        DB.mark_published(car_id)
        await message.reply_text("🚀 Опубликовано в канал.")
    except Exception as exc:
        log.exception("Publish failed")
        await message.reply_text(f"❌ Не получилось опубликовать: {exc}")


async def scheduled_publish(context: ContextTypes.DEFAULT_TYPE):
    if not S.auto_publish:
        return
    approved = DB.list_cars(("APPROVED",), limit=1)
    if not approved:
        return
    car = approved[0]
    channel = DB.get_setting("channel_id", S.channel_id)
    if not channel:
        return
    caption = _caption_html(render_channel_post(car, DB.get_setting("owner_user_id")))
    photos = _photo_sources(car)
    try:
        if photos:
            album = [InputMediaPhoto(media=photos[0], caption=caption, parse_mode=ParseMode.HTML)]
            album.extend(InputMediaPhoto(media=u) for u in photos[1:10])
            await context.bot.send_media_group(chat_id=channel, media=album)
        else:
            await context.bot.send_message(chat_id=channel, text=caption, parse_mode=ParseMode.HTML)
        DB.mark_published(car["id"])
    except Exception:
        log.exception("Scheduled publish failed")


async def post_init(app: Application):
    if S.auto_publish:
        from datetime import time
        tz = ZoneInfo(S.timezone)
        for hour in S.publish_hours:
            app.job_queue.run_daily(scheduled_publish, time=time(hour=hour, tzinfo=tz), name=f"publish-{hour}")


def main():
    app = Application.builder().token(S.token).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CommandHandler("setchannel", setchannel))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    app.add_handler(MessageHandler(filters.VOICE, voice_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
