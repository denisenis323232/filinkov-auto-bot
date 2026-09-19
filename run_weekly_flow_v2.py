from __future__ import annotations

import asyncio
import json

import run_weekly_flow as w

b = w.b
r = w.c.r


def _archive_old_queue(current_import_id: int) -> None:
    with b.DB.connect() as con:
        con.execute(
            """UPDATE cars
               SET status='ARCHIVED', updated_at=CURRENT_TIMESTAMP
               WHERE import_id<>?
                 AND status IN ('READY','APPROVED','CANDIDATE','POOL')""",
            (current_import_id,),
        )


def _stage_originals_only(car) -> tuple[str, int]:
    """Upload originals to Dropbox inbox only. Never apply OpenCV branding."""
    jid = r.job_id(car)
    inbox = f"{r.DROPBOX_ROOT}/inbox/{jid}"
    ready = f"{r.DROPBOX_ROOT}/ready/{jid}"
    r._dbx_ensure_folder(inbox)
    r._dbx_ensure_folder(ready)

    photos = r._photo_urls(car)
    for i, url in enumerate(photos, 1):
        raw, ext = r._download_source(url, b.S.http_timeout)
        name = f"{i:02d}{ext}"
        r._dbx_upload(f"{inbox}/{name}", raw, timeout=max(60, b.S.http_timeout))

    manifest = {
        "job_id": jid,
        "car_id": int(car["id"]),
        "inventory_no": car["inventory_no"],
        "model": car["model"],
        "expected": len(photos),
        "source_url": car["source_url"],
        "branding": "high-quality-external-only",
    }
    r._dbx_upload(
        f"{inbox}/manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    return jid, len(photos)


async def document_handler(update, context):
    if not await b.guard(update):
        return
    doc = update.message.document
    name = doc.file_name or "supplier.xlsx"
    if not name.lower().endswith((".xls", ".xlsx")):
        await update.message.reply_text("Нужен Excel: .xls или .xlsx")
        return

    await update.message.reply_text("⏳ Excel получил. Ищу самый дешёвый вариант каждой модели...")
    folder = b.S.data_dir / "imports"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    f = await doc.get_file()
    await f.download_to_drive(custom_path=target)

    import_id = b.DB.create_import(name)
    try:
        result = b.parse_excel(target, import_id, b.S.min_age_years, b.S.max_age_years, b.S.max_power_hp)
        _archive_old_queue(import_id)
        for car in result.cars:
            car_id = b.DB.upsert_car(car)
            current = b.DB.get_car(car_id)
            if current and current["status"] != "PUBLISHED":
                b.DB.set_car_status(car_id, car["status"], car.get("reject_reason"))
        b.DB.update_import_counts(import_id, result.total, result.accepted, result.manual, result.rejected)
        payload = w._candidate_payload(import_id, w._cars_for_import(import_id))
    except Exception as exc:
        b.log.exception("Excel parse/selection failed")
        await update.message.reply_text(f"❌ Не смог обработать Excel: {exc}")
        return

    lines = [
        f"✅ В файле: <b>{result.total}</b> машин · прошли фильтр: <b>{result.accepted}</b> · "
        f"самых дешёвых уникальных вариантов: <b>{len(payload['car_ids'])}</b>",
        "",
    ] + w._candidate_lines(payload)
    await w._send_chunks(update.message, lines)


async def _prepare_selected(context, car_ids: list[int]) -> None:
    owner = b.DB.get_setting("owner_user_id")
    ok = 0
    failed = []
    for car_id in car_ids:
        try:
            car = b.DB.get_car(car_id)
            b.DB.set_car_status(car_id, "READY")
            car, _breakdown, price_error = await r._ensure_source_and_price(car, force_price=True)
            if price_error:
                raise RuntimeError(price_error)
            await asyncio.to_thread(_stage_originals_only, car)
            b.DB.set_car_status(car_id, "APPROVED")
            ok += 1
        except Exception as exc:
            b.log.exception("Weekly prep failed for car %s", car_id)
            failed.append((car_id, str(exc)))

    if owner:
        text = (
            f"✅ Выбрано и подготовлено по данным: <b>{ok}/{len(car_ids)}</b>.\n"
            "📸 Исходные фото загружены в Dropbox без дешёвого OpenCV-брендинга. "
            "Публикация ждёт качественные готовые фото в ready."
        )
        if failed:
            text += f"\n⚠️ Ошибок подготовки: <b>{len(failed)}</b>."
        await context.bot.send_message(chat_id=int(owner), text=text, parse_mode="HTML")


# Critical: turn off the old local OpenCV path completely.
r.stage_dropbox = _stage_originals_only
r._process_pending_jobs = lambda limit=5: None
w._prepare_selected = _prepare_selected
b.document_handler = document_handler
b.text_handler = w.text_handler
b.post_init = w.post_init


if __name__ == "__main__":
    b.main()
