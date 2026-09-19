from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    token: str
    data_dir: Path
    timezone: str
    channel_id: str | None
    owner_user_id: int | None
    min_age_years: int
    max_age_years: int
    max_power_hp: int
    extra_cny: int
    delivery_rub: int
    other_rub: int
    auto_publish: bool
    publish_hours: tuple[int, ...]
    http_timeout: int


def load_settings() -> Settings:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")

    data_dir = Path(os.getenv("DATA_DIR", "./data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    hours = tuple(int(x.strip()) for x in os.getenv("PUBLISH_HOURS", "12,18").split(",") if x.strip())
    owner_raw = os.getenv("OWNER_USER_ID", "").strip()

    return Settings(
        token=token,
        data_dir=data_dir,
        timezone=os.getenv("BOT_TIMEZONE", "Europe/Moscow"),
        channel_id=os.getenv("TELEGRAM_CHANNEL_ID") or None,
        owner_user_id=int(owner_raw) if owner_raw else None,
        min_age_years=int(os.getenv("MIN_AGE_YEARS", "3")),
        max_age_years=int(os.getenv("MAX_AGE_YEARS", "5")),
        max_power_hp=int(os.getenv("MAX_POWER_HP", "160")),
        extra_cny=int(os.getenv("EXTRA_CNY", "13000")),
        delivery_rub=int(os.getenv("DELIVERY_RUB", "200000")),
        other_rub=int(os.getenv("OTHER_RUB", "140000")),
        auto_publish=_bool("AUTO_PUBLISH", False),
        publish_hours=hours,
        http_timeout=int(os.getenv("HTTP_TIMEOUT", "20")),
    )
