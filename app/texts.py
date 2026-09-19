from __future__ import annotations

import html
import re

PHONE_DISPLAY = "+7 916 408-50-10"
PHONE_TEL = "+79164085010"


def rub(value) -> str:
    if value is None:
        return "—"
    return f"{round(float(value)):,}".replace(",", " ")


def cny(value) -> str:
    if value is None:
        return "—"
    return f"¥{round(float(value)):,}".replace(",", " ")


def _short_features(text: str | None, limit: int = 8) -> list[str]:
    """Pick only features that are actually useful in a sales post.

    We deliberately skip low-value filler such as DRLs, tyre-pressure display,
    automatic headlights and other standard equipment.
    """
    if not text:
        return []

    low = re.sub(r"\s+", " ", text).lower()
    out: list[str] = []

    # Priority order: cameras/driver assists -> steering/comfort -> interior/material -> multimedia.
    feature_groups = [
        (("360", "кругов", "панорамн камер", "around view"), "Камеры 360°"),
        (("камера зад", "rear camera", "заднего вида"), "Камера заднего вида"),
        (("парктрон", "parking sensor", "датчик парков"), "Парктроники"),
        (("слепых зон", "blind spot", "bsd", "bsm"), "Контроль слепых зон"),
        (("удержан", "lane keep", "lka", "полос"), "Удержание в полосе"),
        (("адаптивн", "adaptive cruise", "acc"), "Адаптивный круиз-контроль"),
        (("круиз", "cruise control"), "Круиз-контроль"),
        (("экстренн торм", "collision", "aeb", "предотвращен столк"), "Система предотвращения столкновений"),
        (("мультируль", "многофункциональн рул", "multifunction steering"), "Мультируль"),
        (("подогрев рул", "heated steering"), "Подогрев руля"),
        (("электросид", "электрорегулировк сид", "power seat"), "Электрорегулировка сидений"),
        (("память сид", "seat memory"), "Память сидений"),
        (("вентиляц", "ventilated seat"), "Вентиляция сидений"),
        (("подогрев сид", "heated seat"), "Подогрев сидений"),
        (("алькантар", "alcantara"), "Салон Alcantara"),
        (("кожан", "кожа", "leather"), "Кожаный салон"),
        (("тканев", "fabric seat"), "Тканевый салон"),
        (("панорамн крыш", "панорамн люк", "panoramic"), "Панорамная крыша"),
        (("бесключ", "keyless", "keyless entry"), "Бесключевой доступ"),
        (("электропривод багаж", "power tailgate", "electric tailgate"), "Электропривод багажника"),
        (("проекц", "head-up", "hud"), "Проекция на лобовое стекло"),
        (("цифровая прибор", "цифровая панель", "digital cluster"), "Цифровая приборная панель"),
        (("carplay", "apple carplay"), "Apple CarPlay"),
        (("android auto",), "Android Auto"),
        (("carlife",), "CarLife"),
        (("навига", "navigation"), "Навигация"),
        (("голосов управ", "voice control"), "Голосовое управление"),
    ]

    for needles, label in feature_groups:
        if any(n in low for n in needles) and label not in out:
            out.append(label)
        if len(out) >= limit:
            break
    return out


def _condition_line(car) -> str | None:
    parts = []
    if car["condition_text"]:
        parts.append(str(car["condition_text"]))
    src = (car["source_description"] or "").lower() if "source_description" in car.keys() else ""
    if "оригиналь" in src and "окрас" in src:
        parts.append("заводской окрас")
    if "без износ" in src:
        parts.append("салон без заметного износа")
    if not parts:
        return None
    uniq = []
    for p in parts:
        if p not in uniq:
            uniq.append(p)
    return ", ".join(uniq[:2])


def generate_post(car) -> str:
    """Generate the editable plain-text body. Contacts are added at render time."""
    model = car["model"] or "Автомобиль"
    year = car["year"] or "—"
    power = car["power_hp"] or "—"
    mileage = rub(car["mileage_km"]) if car["mileage_km"] is not None else "—"
    trim = car["trim"] or "—"
    final = car["final_price_rub"]
    desc = car["source_description"] if "source_description" in car.keys() else None
    features = _short_features(desc)
    condition = _condition_line(car)

    lines = [
        f"🚗 В ПРОДАЖЕ — {model.upper()}",
        "",
        f"{year} год • {mileage} км • {power} л.с.",
        "",
        f"{model} — интересный вариант под заказ: понятный пробег, нормальная комплектация и подробная карточка от поставщика.",
        f"Комплектация: {trim}.",
    ]

    if condition:
        lines += ["", f"По состоянию: {condition}."]

    if features:
        lines += ["", "Из полезного оснащения:"]
        lines += [f"— {x}" for x in features]

    if final:
        lines += ["", f"💸 ИТОГОВАЯ ЦЕНА В МОСКВЕ: {rub(final)} ₽"]
    else:
        lines += ["", "💸 ИТОГОВАЯ ЦЕНА В МОСКВЕ: рассчитывается"]

    lines += [
        "",
        "Если интересен именно этот вариант или нужен похожий автомобиль — напишите. Подберём и посчитаем всё под ключ.",
    ]
    return "\n".join(lines)


def render_channel_post(car, owner_user_id: str | int | None) -> str:
    """Escape editable body and append clickable contact footer for Telegram HTML."""
    body = car["post_text"] or generate_post(car)
    body_html = html.escape(str(body))

    footer = [
        "",
        f'📞 Денис: <a href="tel:{PHONE_TEL}">{PHONE_DISPLAY}</a>',
    ]
    if owner_user_id:
        footer.append(f'💬 <a href="tg://user?id={int(owner_user_id)}">Личные сообщения</a>')
    else:
        footer.append("💬 Личные сообщения")
    return body_html + "\n" + "\n".join(footer)


def generate_voice_script(car) -> str:
    model = car["model"] or "этот автомобиль"
    return (
        f"Друзья, посмотрите на {model}. "
        f"{car['year']} год, {car['power_hp']} сил, пробег {rub(car['mileage_km'])} километров. "
        "По карточке поставщика состояние и оснащение выглядят интересно. "
        "Если хотите такой вариант, напишите мне — посчитаем конечную цену в Москве и привезём под заказ."
    )
