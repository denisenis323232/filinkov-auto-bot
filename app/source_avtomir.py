from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

MEDIA_EXT = (".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov")
SKIP_WORDS = ("logo", "icon", "favicon", "avatar", "sprite", "qr", "wechat")


@dataclass
class SourceCard:
    media_urls: list[str]
    description: str
    engine_cc: int | None
    engine_type: str | None


def _clean_url(base: str, value: str) -> str | None:
    value = (value or "").strip().strip('"\'')
    if not value or value.startswith(("data:", "javascript:")):
        return None
    u = urljoin(base, value)
    p = urlparse(u)
    if p.scheme not in {"http", "https"}:
        return None
    return u


def _page(page_url: str, timeout: int) -> tuple[requests.Response, BeautifulSoup]:
    r = requests.get(
        page_url,
        timeout=timeout,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; FILINKOV-AUTO/1.0)",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7,zh;q=0.5",
        },
    )
    r.raise_for_status()
    return r, BeautifulSoup(r.text, "lxml")


def _extract_media(r: requests.Response, soup: BeautifulSoup) -> list[str]:
    found: list[str] = []
    for tag, attr in [
        ("img", "src"), ("img", "data-src"), ("img", "data-original"),
        ("source", "src"), ("video", "src"), ("a", "href")
    ]:
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
    photos = [u for u in out if any(x in u.lower() for x in (".jpg", ".jpeg", ".png", ".webp"))][:10]
    videos = [u for u in out if any(x in u.lower() for x in (".mp4", ".mov"))][:1]
    return photos + videos


def _description(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    candidates: list[str] = []
    selectors = [
        ".car-detail", ".car-info", ".vehicle-info", ".detail", ".details",
        ".description", ".desc", ".content", "main", "article"
    ]
    for sel in selectors:
        for el in soup.select(sel):
            text = " ".join(el.stripped_strings)
            if len(text) >= 80:
                candidates.append(text)

    if not candidates:
        candidates.append(" ".join(soup.stripped_strings))

    text = max(candidates, key=len) if candidates else ""
    text = re.sub(r"\s+", " ", text).strip()
    return text[:12000]


def _engine_cc(text: str) -> int | None:
    pats = [
        r"(?:об[ъь]?[её]м|volume|排量)[^\d]{0,20}(\d{3,4})\s*(?:см3|см³|cc|cm3)",
        r"(\d{3,4})\s*(?:см3|см³|cc|cm3)",
        r"(\d(?:[.,]\d))\s*[lл]\b",
    ]
    for p in pats:
        m = re.search(p, text, re.I)
        if not m:
            continue
        v = m.group(1).replace(",", ".")
        if "." in v:
            cc = int(round(float(v) * 1000))
        else:
            cc = int(v)
        if 500 <= cc <= 8000:
            return cc
    return None


def _engine_type(text: str) -> str | None:
    t = text.lower()
    if "дизел" in t or "diesel" in t:
        return "diesel"
    if "гибрид" in t or "hybrid" in t:
        return "hybrid"
    if "электр" in t or "electric" in t:
        return "electric"
    if "бенз" in t or "gasoline" in t or "petrol" in t:
        return "petrol"
    return None


def extract_card(page_url: str, timeout: int = 20) -> SourceCard:
    r, soup = _page(page_url, timeout)
    desc = _description(soup)
    return SourceCard(
        media_urls=_extract_media(r, soup),
        description=desc,
        engine_cc=_engine_cc(desc),
        engine_type=_engine_type(desc),
    )


def extract_media_urls(page_url: str, timeout: int = 20) -> list[str]:
    return extract_card(page_url, timeout).media_urls
