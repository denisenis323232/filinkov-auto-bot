from __future__ import annotations

import asyncio
import html
import json

import run_persistent_state as p

m = p.m
w = p.w
b = p.b
r = p.r


def _bar(done: int, total: int, width: int = 10) -> str:
    if total <= 0:
        return "░" * width + " 0%"
    pct = max(0, min(100, round(done * 100 / total)))
    filled = min(width, round(width * done / total))
    return "█" * filled + "░" * (width - filled) + f" {pct}%"


def _schedule() -> list[dict]:
    try:
        return w._load_schedule() or []
    except Exception:
        return []


def _selected_cars() -> list:
    out = []
    seen = set()
    for item in _schedule():
        car_id = item.get("car_id")
        if not car_id or car_id in seen:
            continue
        car = b.DB.get_car(int(car_id))
        if car:
            out.append(car)
            seen.add(car_id)
    return out


def _job_ready_counts(car) -> tuple[bool, bool]:
    jid = r.job_id(car)
    expected = len(r._photo_urls(car))
    if expected <= 0:
        return False, False
    try:
        inbox = r._image_entries(r._dbx_list(f"{r.DROPBOX_ROOT}/inbox/{jid}"))
    except Exception:
        inbox = []
    try:
        ready = r._image_entries(r._dbx_list(f"{r.DROPBOX_ROOT}/ready/{jid}"))
    except Exception:
        ready = []
    return len(inbox) >= expected, len(ready) >= expected


def _progress_snapshot() -> dict:
    schedule = _schedule()
    cars = _selected_cars()
    total = len(cars)
    priced = sum(1 for c in cars if c["final_price_rub"])
    text_ready = sum(1 for c in cars if (c["post_text"] or "").strip())
    originals = branded = 0
    for car in cars:
        src_ok, ready_ok = _job_ready_counts(car)
        originals += int(src_ok)
        branded += int(ready_ok)
    scheduled = sum(1 for x in schedule if x.get("at"))
    published = sum(1 for x in schedule if x.get("state") == "published")
    return {
        "total": total,
        "priced": priced,
        "text_ready": text_ready,
        "originals": originals,
        "branded": branded,
        "scheduled": scheduled,
        "published": published,
    }


def _progress_text(s: dict) -> str:
    total = s["total"]
    steps = [
        ("💰 Цена рассчитана", s["priced"]),
        ("📝 Текст поста", s["text_ready"]),
        ("☁️ Исходники Dropbox", s["originals"]),
        ("🎨 Фото FILINKOV", s["branded"]),
        ("📅 Запланировано", s["scheduled"]),
        ("🚀 Опубликовано", s["published"]),
    ]
    lines = [f"📊 <b>Готовность недели — {total} авто</b>", ""]
    for label, done in steps:
        lines.append(f"{label}: <b>{done}/{total}</b>")
        lines.append(f"<code>{_bar(done, total)}</code>")
        lines.append("")
    if total:
        prep = min(s["priced"], s["text_ready"], s["originals"], s["branded"], s["scheduled"])
        lines.append(f"✅ <b>Полностью готовы к публикации: {prep}/{total}</b>")
        lines.append(f"<code>{_bar(prep, total)}</code>")
    return "\n".join(lines).strip()


async def progress_stats(message):
    snap = await asyncio.to_thread(_progress_snapshot)
    if snap["total"] <= 0:
        await message.reply_text("📊 Недельная очередь пока пустая.")
        return
    await message.reply_text(_progress_text(snap), parse_mode=w.ParseMode.HTML)


async def prepare_selected_with_progress(context, car_ids: list[int]) -> None:
    owner = b.DB.get_setting("owner_user_id")
    progress_message = None
    if owner:
        try:
            initial = {"total": len(car_ids), "priced": 0, "text_ready": 0, "originals": 0, "branded": 0, "scheduled": len(car_ids), "published": 0}
            progress_message = await context.bot.send_message(
                chat_id=int(owner), text=_progress_text(initial), parse_mode=w.ParseMode.HTML
            )
        except Exception:
            b.log.exception("Could not send weekly progress message")

    staged = 0
    actual_errors = []
    for index, car_id in enumerate(car_ids, 1):
        try:
            car = b.DB.get_car(car_id)
            w._set_status(car_id, "READY")
            try:
                car, _breakdown, price_error = await r._ensure_source_and_price(car, force_price=True)
            except Exception as exc:
                b.log.exception("Price/source prep warning for car %s", car_id)
                car = b.DB.get_car(car_id)
                price_error = str(exc)
            if not car:
                raise RuntimeError("машина не найдена в базе")
            jid, count = await asyncio.to_thread(m.stage_originals, car)
            if count <= 0:
                raise RuntimeError("не найдены исходные фото")
            staged += 1
            b.log.info("Weekly progress %s/%s: car=%s job=%s photos=%s warning=%s", index, len(car_ids), car_id, jid, count, price_error or "none")
        except Exception as exc:
            b.log.exception("Weekly staging failed for car %s", car_id)
            actual_errors.append((car_id, str(exc)))

        if progress_message:
            try:
                snap = await asyncio.to_thread(_progress_snapshot)
                await progress_message.edit_text(_progress_text(snap), parse_mode=w.ParseMode.HTML)
            except Exception:
                pass

    if owner:
        snap = await asyncio.to_thread(_progress_snapshot)
        suffix = ""
        if actual_errors:
            suffix = f"\n\n⚠️ Реальных ошибок: <b>{len(actual_errors)}</b>"
        try:
            if progress_message:
                await progress_message.edit_text(_progress_text(snap) + suffix, parse_mode=w.ParseMode.HTML)
            else:
                await context.bot.send_message(chat_id=int(owner), text=_progress_text(snap) + suffix, parse_mode=w.ParseMode.HTML)
        except Exception:
            b.log.exception("Could not finalize weekly progress")


# Base callback handler resolves b.stats dynamically.
b.stats = progress_stats
# Weekly text handler resolves its module-global _prepare_selected dynamically.
w._prepare_selected = prepare_selected_with_progress

if __name__ == "__main__":
    b.main()
