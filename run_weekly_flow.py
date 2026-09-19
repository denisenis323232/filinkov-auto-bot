from __future__ import annotations

import asyncio
import html
import json
import re
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo

from telegram import InputMediaPhoto
from telegram.constants import ParseMode

import run_current_price as c

b = c.r.b
TZ = ZoneInfo(b.S.timezone)
_original_text_handler = b.text_handler
_original_post_init = b.post_init


def _norm_model(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def _set_status(car_id: int, status: str) -> None:
    b.DB.set_car_status(car_id, status)


def _cars_for_import(import_id: int):
    with b.DB.connect() as con:
        return con.execute(
            "SELECT * FROM cars WHERE import_id=? ORDER BY id",
            (import_id,),
        ).fetchall()


def _candidate_payload(import_id: int, cars) -> dict:
    cheapest: dict[str, object] = {}
    for car in cars:
        if car["status"] != "READY":
            continue
        key = _norm_model(car["model"])
        if not key:
            continue
        old = cheapest.get(key)
        if old is None or float(car["price_cny"] or 10**18) < float(old["price_cny"] or 10**18):
            cheapest[key] = car

    selected = sorted(
        cheapest.values(),
        key=lambda x: (_norm_model(x["model"]), float(x["price_cny"] or 10**18)),
    )
    ids = [int(car["id"]) for car in selected]

    # Keep the full accepted pool out of the normal queue. Only the cheapest
    # representative of each model is offered for selection.
    for car in cars:
        if car["status"] == "READY":
            _set_status(int(car["id"]), "POOL")
    for car_id in ids:
        _set_status(car_id, "CANDIDATE")

    payload = {"import_id": import_id, "car_ids": ids}
    b.DB.set_setting("selection_candidates", json.dumps(payload, ensure_ascii=False))
    return payload


def _candidate_lines(payload: dict) -> list[str]:
    lines = [
        "🚗 <b>Самые дешёвые варианты по каждой модели</b>",
        "",
        "Я убрал дубли и оставил по одному — самому дешёвому автомобилю каждой модели.",
        "",
    ]
    for i, car_id in enumerate(payload.get("car_ids", []), 1):
        car = b.DB.get_car(int(car_id))
        if not car:
            continue
        year = car["year"] or "—"
        price = int(round(float(car["price_cny"] or 0)))
        power = f" · {car['power_hp']} л.с." if car["power_hp"] else ""
        lines.append(
            f"<b>{i}.</b> {html.escape(car['model'] or '—')} · {year}{power} · <b>{price:,} ¥</b>".replace(",", " ")
        )
    lines += [
        "",
        "Напиши номера, которые берём. Например: <code>1, 2, 5, 7, 10</code>",
        "Можно выбрать сразу 14 машин на неделю.",
    ]
    return lines


async def _send_chunks(message, lines: list[str]) -> None:
    chunk = ""
    for line in lines:
        candidate = chunk + ("\n" if chunk else "") + line
        if len(candidate) > 3800:
            await message.reply_text(chunk, parse_mode=ParseMode.HTML)
            chunk = line
        else:
            chunk = candidate
    if chunk:
        await message.reply_text(chunk, parse_mode=ParseMode.HTML)


async def document_handler(update, context):
    if not await b.guard(update):
        return
    doc = update.message.document
    name = doc.file_name or "supplier.xlsx"
    if not name.lower().endswith((".xls", ".xlsx")):
        await update.message.reply_text("Нужен Excel: .xls или .xlsx")
        return

    await update.message.reply_text("⏳ Excel получил. Фильтрую и ищу самый дешёвый вариант каждой модели...")
    folder = b.S.data_dir / "imports"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    f = await doc.get_file()
    await f.download_to_drive(custom_path=target)

    import_id = b.DB.create_import(name)
    try:
        result = b.parse_excel(target, import_id, b.S.min_age_years, b.S.max_age_years, b.S.max_power_hp)
        for car in result.cars:
            b.DB.upsert_car(car)
        b.DB.update_import_counts(import_id, result.total, result.accepted, result.manual, result.rejected)
        payload = _candidate_payload(import_id, _cars_for_import(import_id))
    except Exception as exc:
        b.log.exception("Excel parse/selection failed")
        await update.message.reply_text(f"❌ Не смог обработать Excel: {exc}")
        return

    lines = [
        f"✅ В файле: <b>{result.total}</b> машин · прошли фильтр: <b>{result.accepted}</b> · "
        f"уникальных дешёвых вариантов: <b>{len(payload['car_ids'])}</b>",
        "",
    ] + _candidate_lines(payload)
    await _send_chunks(update.message, lines)


def _parse_choices(text: str, maximum: int) -> list[int]:
    values = []
    for raw in re.findall(r"\d+", text or ""):
        n = int(raw)
        if 1 <= n <= maximum and n not in values:
            values.append(n)
    return values


def _first_week_slot(now: datetime) -> datetime:
    local = now.astimezone(TZ)
    # Plan a clean Monday-Sunday content week. If it is Monday before noon,
    # use today; otherwise begin next Monday.
    if local.weekday() == 0 and local.time() < dt_time(12, 0):
        monday = local.date()
    else:
        days = (7 - local.weekday()) % 7
        if days == 0:
            days = 7
        monday = (local + timedelta(days=days)).date()
    return datetime.combine(monday, dt_time(12, 0), tzinfo=TZ)


def _make_schedule(car_ids: list[int]) -> list[dict]:
    start = _first_week_slot(datetime.now(TZ))
    slots: list[datetime] = []
    day = start.date()
    while len(slots) < len(car_ids):
        slots.append(datetime.combine(day, dt_time(12, 0), tzinfo=TZ))
        if len(slots) < len(car_ids):
            slots.append(datetime.combine(day, dt_time(18, 0), tzinfo=TZ))
        day += timedelta(days=1)
    return [
        {"car_id": int(car_id), "at": slots[i].isoformat(), "state": "scheduled"}
        for i, car_id in enumerate(car_ids)
    ]


def _save_schedule(schedule: list[dict]) -> None:
    b.DB.set_setting("weekly_schedule", json.dumps(schedule, ensure_ascii=False))


def _load_schedule() -> list[dict]:
    raw = b.DB.get_setting("weekly_schedule", "[]") or "[]"
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except Exception:
        return []


def _schedule_summary(schedule: list[dict]) -> str:
    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    lines = ["📅 <b>План публикаций</b>", ""]
    for i, item in enumerate(schedule, 1):
        car = b.DB.get_car(int(item["car_id"]))
        at = datetime.fromisoformat(item["at"]).astimezone(TZ)
        lines.append(
            f"{i}. {weekdays[at.weekday()]} {at:%d.%m} в {at:%H:%M} — {html.escape(car['model'] if car else 'машина')}"
        )
    return "\n".join(lines)


async def _prepare_selected(context, car_ids: list[int]) -> None:
    owner = b.DB.get_setting("owner_user_id")
    ok = 0
    failed = []
    for index, car_id in enumerate(car_ids, 1):
        try:
            car = b.DB.get_car(car_id)
            _set_status(car_id, "READY")
            car, _breakdown, price_error = await c.r._ensure_source_and_price(car, force_price=True)
            if price_error:
                raise RuntimeError(price_error)
            await asyncio.to_thread(c.r.stage_dropbox, car)
            _set_status(car_id, "APPROVED")
            ok += 1
        except Exception as exc:
            b.log.exception("Weekly prep failed for car %s", car_id)
            failed.append((car_id, str(exc)))

    if owner:
        text = f"✅ Подготовка недели завершена: <b>{ok}/{len(car_ids)}</b> машин готовы."
        if failed:
            text += f"\n⚠️ Ошибок: <b>{len(failed)}</b>. Они не будут опубликованы, пока не подготовятся."
        await context.bot.send_message(chat_id=int(owner), text=text, parse_mode=ParseMode.HTML)


async def text_handler(update, context):
    if not await b.guard(update):
        return

    # Preserve edit-text flow from the base bot.
    if "await_text_for" in context.user_data or "await_customs_for" in context.user_data:
        return await _original_text_handler(update, context)

    payload_raw = b.DB.get_setting("selection_candidates")
    if payload_raw:
        try:
            payload = json.loads(payload_raw)
        except Exception:
            payload = {}
        ids = [int(x) for x in payload.get("car_ids", [])]
        choices = _parse_choices(update.message.text or "", len(ids))
        if choices:
            chosen_ids = [ids[n - 1] for n in choices]
            schedule = _make_schedule(chosen_ids)
            _save_schedule(schedule)
            b.DB.set_setting("selection_candidates", "")
            for car_id in chosen_ids:
                _set_status(car_id, "READY")

            await update.message.reply_text(
                f"✅ Выбрано машин: <b>{len(chosen_ids)}</b>. Ставлю их в подготовку: цена → фото → пост → очередь.",
                parse_mode=ParseMode.HTML,
            )
            await update.message.reply_text(_schedule_summary(schedule), parse_mode=ParseMode.HTML)
            context.application.create_task(_prepare_selected(context, chosen_ids), name="prepare-weekly-cars")
            return

    await _original_text_handler(update, context)


async def _publish_one(context, car_id: int) -> bool:
    car = b.DB.get_car(car_id)
    if not car or car["status"] == "PUBLISHED":
        return True
    if not car["final_price_rub"]:
        return False
    photos = b._photo_sources(car)
    branded = json.loads(car["branded_media_json"] or "[]") if "branded_media_json" in car.keys() else []
    if not branded:
        return False

    channel = b.DB.get_setting("channel_id", b.S.channel_id)
    if not channel:
        return False

    caption = b._caption_html(b.render_channel_post(car, b.DB.get_setting("owner_user_id")))
    try:
        album = [InputMediaPhoto(media=photos[0], caption=caption, parse_mode=ParseMode.HTML)]
        album.extend(InputMediaPhoto(media=u) for u in photos[1:10])
        await context.bot.send_media_group(chat_id=channel, media=album)
        media = json.loads(car["media_json"] or "[]")
        videos = [u for u in media if any(x in u.lower() for x in (".mp4", ".mov"))][:1]
        if videos:
            await context.bot.send_video(chat_id=channel, video=videos[0])
        if car["voice_file_id"]:
            await context.bot.send_voice(chat_id=channel, voice=car["voice_file_id"])
        b.DB.mark_published(car_id)
        return True
    except Exception:
        b.log.exception("Weekly scheduled publish failed for car %s", car_id)
        return False


async def weekly_publish_tick(context):
    schedule = _load_schedule()
    if not schedule:
        return
    now = datetime.now(TZ)
    changed = False
    owner = b.DB.get_setting("owner_user_id")
    for item in schedule:
        if item.get("state") == "published":
            continue
        try:
            at = datetime.fromisoformat(item["at"]).astimezone(TZ)
        except Exception:
            continue
        if at > now:
            continue
        car_id = int(item["car_id"])
        if await _publish_one(context, car_id):
            item["state"] = "published"
            changed = True
        elif owner and item.get("warned") != True:
            car = b.DB.get_car(car_id)
            await context.bot.send_message(
                chat_id=int(owner),
                text=f"⚠️ Не публикую слот {at:%d.%m %H:%M}: {html.escape(car['model'] if car else str(car_id))} ещё не полностью готова. Повторю автоматически.",
                parse_mode=ParseMode.HTML,
            )
            item["warned"] = True
            changed = True
    if changed:
        _save_schedule(schedule)


async def post_init(app):
    await _original_post_init(app)
    app.job_queue.run_repeating(weekly_publish_tick, interval=30, first=10, name="weekly-publish-tick")


b.document_handler = document_handler
b.text_handler = text_handler
b.post_init = post_init


if __name__ == "__main__":
    b.main()
