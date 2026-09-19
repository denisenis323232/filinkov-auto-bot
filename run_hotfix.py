from __future__ import annotations

import asyncio
import json
import os
import re

import run_progress as p

b = p.b
w = p.w
r = p.r

_original_ensure = r._ensure_source_and_price
_original_weekly_tick = w.weekly_publish_tick


def _infer_engine_cc(car) -> int | None:
    text = " ".join(str(car[k] or "") for k in ("model", "trim", "source_description") if k in car.keys())
    m = re.search(r"(?<!\d)([1-6](?:[.,]\d))\s*[TtLlЛл]?\b", text)
    if m:
        cc = int(round(float(m.group(1).replace(",", ".")) * 1000))
        if 500 <= cc <= 8000:
            return cc

    t = text.casefold()
    known = [
        ("bmw 3 series", 1998),
        ("320li", 1998),
        ("bmw 1 series", 1499),
        ("120i", 1499),
        ("audi q2", 1395),
        ("35t", 1395),
        ("audi а3l", 1395),
        ("audi a3l", 1395),
        ("changan cs35plus", 1392),
        ("geely coolray", 1398),
        ("honda vezel", 1498),
        ("hyundai ix35", 1400),
        ("jetta vs5", 1395),
        ("mg5", 1490),
        ("mercedes-benz glb", 1332),
        ("glb 180", 1332),
        ("mazda cx-5", 1998),
        ("volkswagen passat", 1395),
        ("volkswagen golf", 1395),
        ("280tsi", 1395),
        ("280t", 1395),
    ]
    for needle, cc in known:
        if needle in t:
            return cc
    return None


async def ensure_source_and_price_hotfix(car, force_price: bool = True):
    car, breakdown, price_error = await _original_ensure(car, force_price=force_price)
    if not price_error or "объем двигателя" not in price_error.lower():
        return car, breakdown, price_error

    cc = _infer_engine_cc(car)
    if not cc:
        return car, breakdown, price_error

    media = json.loads(car["media_json"] or "[]")
    b.DB.set_source_card(
        car["id"], media, car["source_description"], cc, car["engine_type"] or "petrol"
    )
    refreshed = b.DB.get_car(car["id"])
    b.log.info("Engine fallback for car %s: %s cc", car["id"], cc)
    return await _original_ensure(refreshed, force_price=True)


async def quiet_poll_ready(context):
    # Intentionally no automatic albums to the owner chat. This prevents floods.
    # Ready photos stay in Dropbox and are used only when explicitly previewed/published.
    return


async def safe_weekly_tick(context):
    enabled = os.getenv("AUTO_PUBLISH", "false").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return
    await _original_weekly_tick(context)


r._ensure_source_and_price = ensure_source_and_price_hotfix
r.poll_ready = quiet_poll_ready
w.weekly_publish_tick = safe_weekly_tick

if __name__ == "__main__":
    b.main()
