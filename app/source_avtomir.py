from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

MEDIA_EXT = (".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov")
SKIP_WORDS = ("logo", "icon", "favicon", "avatar", "sprite", "qr", "wechat")


def _clean_url(base: str, value: str) -> str | None:
    value = (value or "").strip().strip('"\'')
    if not value or value.startswith(("data:", "javascript:")):
        return None
    u = urljoin(base, value)
    p = urlparse(u)
    if p.scheme not in {"http", "https"}:
        return None
    return u


def extract_media_urls(page_url: str, timeout: int = 20) -> list[str]:
    r = requests.get(
        page_url,
        timeout=timeout,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; FILINKOV-AUTO/1.0)",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
        },
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    found: list[str] = []

    for tag, attr in [("img", "src"), ("img", "data-src"), ("source", "src"), ("video", "src"), ("a", "href")]:
        for el in soup.find_all(tag):
            value = el.get(attr)
            if value:
                u = _clean_url(r.url, value)
                if u:
                    found.append(u)

    for prop in ("og:image", "og:video"):
        for el in soup.find_all("meta", attrs={"property": prop}):
            u = _clean_url(r.url, el.get("content", ""))
            if u:
                found.append(u)

    for raw in re.findall(r"https?://[^\s\"'<>]+", r.text):
        found.append(raw.replace("\\/", "/"))

    for raw in re.findall(r"[\"']([^\"']+\.(?:jpe?g|png|webp|mp4|mov)(?:\?[^\"']*)?)[\"']", r.text, re.I):
        u = _clean_url(r.url, raw.replace("\\/", "/"))
        if u:
            found.append(u)

    out, seen = [], set()
    for u in found:
        low = u.lower()
        if any(w in low for w in SKIP_WORDS):
            continue
        if not any(ext in low for ext in MEDIA_EXT):
            continue
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:60]
