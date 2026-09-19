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


def _short_features(text: str | None, limit: int = 7) -> list[str]:
    if not text:
        return []
    t = re.sub(r"\s+", " ", text)
    dictionary = [
        ("кож", "Кожаный салон"),
        ("подогрев", "Подогрев сидений"),
        ("камера", "Камера заднего вида"),
        ("климат", "Климат-контроль"),
        ("круиз", "Круиз-контроль"),
        ("навига", "Навигация"),
        ("carplay", "Apple CarPlay"),
        ("carlife", "CarLife"),
        ("панорам", "Панорамная крыша"),
        ("электросид", "Электрорегулировка сидений"),
        ("электрическ", "Электроприводы"),
        ("голосов", "Голосовое управление"),
        ("датчик давления", "Контроль давления в шинах"),
        ("дневн", "Дневные ходовые огни"),
        ("автоматическ", "Автоматические функции"),
    ]
    out = []
    low = t.lower()
    for needle, label in dictionary:
        if needle in low and label not in out:
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
        lines += ["", "Что есть в машине:"]
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
