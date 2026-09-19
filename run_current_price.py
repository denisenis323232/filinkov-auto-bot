from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import run_dropbox as r


def kb_car(car_id: int):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Изменить текст", callback_data=f"edit:{car_id}"),
            InlineKeyboardButton("🎙 Голос", callback_data=f"voice:{car_id}"),
        ],
        [
            InlineKeyboardButton("✅ Одобрить", callback_data=f"approve:{car_id}"),
            InlineKeyboardButton("🚀 Сейчас", callback_data=f"publish:{car_id}"),
        ],
        [
            InlineKeyboardButton("⏭ Пропустить", callback_data=f"skip:{car_id}"),
            InlineKeyboardButton("➡️ Следующая", callback_data=f"next:{car_id}"),
        ],
    ])


def current_cbr_rates():
    # CBR publishes official daily rates. Fetch them each time a car is opened,
    # so the calculation never depends on a manual refresh button or stale cache.
    return r.fetch_cbr_rates(r.b.S.http_timeout)


r.kb_car = kb_car
r.b.kb_car = kb_car
r._rates = current_cbr_rates
r.b.buttons = r._original_buttons


if __name__ == "__main__":
    r.b.main()
