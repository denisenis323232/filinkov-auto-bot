from __future__ import annotations


def rub(value) -> str:
    if value is None:
        return "—"
    return f"{round(float(value)):,}".replace(",", " ")


def cny(value) -> str:
    if value is None:
        return "—"
    return f"¥{round(float(value)):,}".replace(",", " ")


def generate_post(car) -> str:
    model = car["model"] or "Автомобиль"
    year = car["year"] or "—"
    power = car["power_hp"] or "—"
    mileage = rub(car["mileage_km"]) if car["mileage_km"] is not None else "—"
    trim = car["trim"] or "—"
    price_line = (
        f"💸 ИТОГОВАЯ ЦЕНА — {rub(car['final_price_rub'])} рублей 💸"
        if car["final_price_rub"]
        else "💸 ИТОГОВАЯ ЦЕНА — после расчёта таможни"
    )
    return (
        f"В ПРОДАЖЕ — {model.upper()}\n\n"
        f"{year}⬇️\n\n"
        f"🚗 {model}\n"
        f"Год: {year}\n"
        f"Мощность: {power} л.с.\n"
        f"Пробег: {mileage} км\n"
        f"Комплектация: {trim}\n\n"
        f"{price_line}\n\n"
        "Хотите этот вариант или что-то похожее — пишите, найдём лучший вариант именно для вас!\n\n"
        "📞 Денис: +79164085010\n"
        "Или в личные сообщения канала"
    )


def generate_voice_script(car) -> str:
    return (
        f"Друзья, интересный вариант — {car['model']}. "
        f"{car['year']} год, {car['power_hp']} сил, пробег {rub(car['mileage_km'])} километров. "
        "Если хотите такой автомобиль или подобрать похожий вариант — напишите мне, всё посчитаем под ключ."
    )
