import json
import logging

from telegram import InputMediaPhoto

from app import bot as b

logging.getLogger("httpx").setLevel(logging.WARNING)

_original_buttons = b.buttons


def _is_photo(url: str) -> bool:
    low = url.lower()
    return any(ext in low for ext in (".jpg", ".jpeg", ".png", ".webp"))


def _is_video(url: str) -> bool:
    low = url.lower()
    return any(ext in low for ext in (".mp4", ".mov"))


async def _send_photo_preview(message, context, photos: list[str], total_media: int, videos: list[str]):
    if not photos:
        await message.reply_text(
            f"✅ Нашёл медиа: {total_media}. Фото не распознаны как прямые ссылки."
        )
        return

    # Для модерации не заваливаем чат десятками сообщений: показываем первые 10.
    preview = photos[:10]
    caption = f"📸 Найдено фото: {len(photos)}"
    if videos:
        caption += f" • видео: {len(videos)}"
    if len(photos) > len(preview):
        caption += f"\nПоказываю первые {len(preview)}. Все ссылки сохранены для публикации."

    try:
        if len(preview) == 1:
            await context.bot.send_photo(
                chat_id=message.chat_id,
                photo=preview[0],
                caption=caption,
            )
        else:
            media = [InputMediaPhoto(media=url) for url in preview]
            media[0] = InputMediaPhoto(media=preview[0], caption=caption)
            await context.bot.send_media_group(chat_id=message.chat_id, media=media)
    except Exception as exc:
        b.log.exception("Media preview failed")
        await message.reply_text(
            "⚠️ Ссылки на фото нашёл и сохранил, но Telegram не смог загрузить их напрямую. "
            f"Ошибка: {type(exc).__name__}: {exc}"
        )


async def buttons(update, context):
    q = update.callback_query
    if q and q.data and q.data.startswith("media:"):
        if not await b.guard(update):
            return
        await q.answer()
        _, _, payload = q.data.partition(":")
        car_id = int(payload)
        car = b.DB.get_car(car_id)
        if not car or not car["source_url"]:
            await q.message.reply_text("У машины нет ссылки на карточку.")
            return

        await q.message.reply_text("⏳ Забираю оригинальные фото/видео из карточки поставщика...")
        try:
            urls = b.extract_media_urls(car["source_url"], b.S.http_timeout)
            b.DB.set_media(car_id, urls)
            if not urls:
                await q.message.reply_text(
                    "⚠️ Карточка открылась, но фото/видео не распознаны. Настроим парсер под эту карточку."
                )
                return

            photos = [u for u in urls if _is_photo(u)]
            videos = [u for u in urls if _is_video(u)]
            await _send_photo_preview(q.message, context, photos, len(urls), videos)
            await q.message.reply_text(
                f"✅ Медиа сохранены: {len(photos)} фото"
                + (f", {len(videos)} видео" if videos else "")
                + ". Теперь их не нужно пересылать мне вручную."
            )
        except Exception as exc:
            b.log.exception("Media extraction failed")
            await q.message.reply_text(
                f"⚠️ Не удалось получить карточку: {type(exc).__name__}: {exc}"
            )
        return

    return await _original_buttons(update, context)


b.buttons = buttons

if __name__ == "__main__":
    b.main()
