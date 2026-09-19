from __future__ import annotations

import asyncio
import json

import run_weekly_flow as w

c = w.c
b = w.b
r = c.r


def stage_originals(car) -> tuple[str, int]:
    """Upload source photos only. No OpenCV/local branding."""
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
        "branding": "manual-quality-edit",
        "state": "waiting_ready_photos",
    }
    r._dbx_upload(
        f"{inbox}/manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    return jid, len(photos)


def no_local_branding(*_args, **_kwargs):
    """Explicitly disable the old OpenCV pending-job processor."""
    return None


async def prepare_selected(context, car_ids: list[int]) -> None:
    owner = b.DB.get_setting("owner_user_id")
    staged = 0
    waiting = 0
    actual_errors: list[tuple[int, str]] = []

    for car_id in car_ids:
        try:
            car = b.DB.get_car(car_id)
            w._set_status(car_id, "READY")

            # Price is useful, but a temporary price/enrichment problem must not
            # turn a successfully staged photo set into a fake preparation error.
            try:
                car, _breakdown, price_error = await c.r._ensure_source_and_price(car, force_price=True)
            except Exception as exc:
                b.log.exception("Price/source prep warning for car %s", car_id)
                car = b.DB.get_car(car_id)
                price_error = str(exc)

            if not car:
                raise RuntimeError("машина не найдена в базе")

            jid, count = await asyncio.to_thread(stage_originals, car)
            if count <= 0:
                raise RuntimeError("не найдены исходные фото")

            staged += 1
            waiting += 1
            b.log.info(
                "Weekly car staged for quality edit: car=%s job=%s photos=%s price_warning=%s",
                car_id, jid, count, price_error or "none",
            )
        except Exception as exc:
            b.log.exception("Weekly staging failed for car %s", car_id)
            actual_errors.append((car_id, str(exc)))

    if owner:
        text = (
            f"✅ Выбрано: <b>{len(car_ids)}</b>\n"
            f"☁️ Исходники загружены в Dropbox: <b>{staged}/{len(car_ids)}</b>\n"
            f"⏳ Ждут качественные готовые фото: <b>{waiting}</b>\n"
            f"📅 Расписание сохранено. Публикация пойдёт только после появления готовых фото в <code>ready</code>."
        )
        if actual_errors:
            text += f"\n⚠️ Реальных ошибок загрузки: <b>{len(actual_errors)}</b>."
        await context.bot.send_message(chat_id=int(owner), text=text, parse_mode=w.ParseMode.HTML)


# Patch runtime references used by weekly flow and Dropbox poller.
r.stage_dropbox = stage_originals
r._process_pending_jobs = no_local_branding
w._prepare_selected = prepare_selected


if __name__ == "__main__":
    b.main()
