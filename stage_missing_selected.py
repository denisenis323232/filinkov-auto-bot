from __future__ import annotations

import hashlib
import json
import os
import pathlib
import requests

from app.source_avtomir import extract_card

DROPBOX_TOKEN = os.getenv("DROPBOX_ACCESS_TOKEN", "").strip()
DROPBOX_ROOT = os.getenv("DROPBOX_ROOT", "/FILINKOV_AUTO").rstrip("/")
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "30"))

CARS = [
    {"inventory": "536001", "model": "AUDI A3", "url": "http://avtomirhrb.ru/?car_1/2380.html"},
    {"inventory": "719370", "model": "AUDI Q3", "url": "http://avtomirhrb.ru/?car_1/2286.html"},
    {"inventory": "218111", "model": "Toyota Corolla", "url": "http://avtomirhrb.ru/?car_1/2526.html"},
]


def dbx_rpc(endpoint: str, payload: dict):
    r = requests.post(
        f"https://api.dropboxapi.com/2/{endpoint}",
        headers={"Authorization": f"Bearer {DROPBOX_TOKEN}", "Content-Type": "application/json"},
        data=json.dumps(payload).encode("utf-8"), timeout=TIMEOUT,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Dropbox {endpoint}: {r.status_code} {r.text[:300]}")
    return r.json()


def ensure_folder(path: str):
    cur = ""
    for part in [p for p in path.split("/") if p]:
        cur += "/" + part
        try:
            dbx_rpc("files/create_folder_v2", {"path": cur, "autorename": False})
        except RuntimeError as e:
            if "conflict" not in str(e).lower():
                raise


def upload(path: str, raw: bytes):
    r = requests.post(
        "https://content.dropboxapi.com/2/files/upload",
        headers={
            "Authorization": f"Bearer {DROPBOX_TOKEN}",
            "Content-Type": "application/octet-stream",
            "Dropbox-API-Arg": json.dumps({"path": path, "mode": "overwrite", "autorename": False, "mute": True}),
        },
        data=raw, timeout=max(60, TIMEOUT),
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Dropbox upload: {r.status_code} {r.text[:300]}")


def download_image(url: str):
    r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0 FILINKOV-AUTO/1.0"})
    r.raise_for_status()
    ct = (r.headers.get("content-type") or "image/jpeg").lower()
    ext = ".png" if "png" in ct else ".webp" if "webp" in ct else ".jpg"
    return r.content, ext


def main():
    if not DROPBOX_TOKEN:
        raise RuntimeError("DROPBOX_ACCESS_TOKEN missing")

    for car in CARS:
        suffix = hashlib.sha1(car["url"].encode("utf-8")).hexdigest()[:8]
        jid = f'{car["inventory"]}-{suffix}'
        inbox = f"{DROPBOX_ROOT}/inbox/{jid}"
        ensure_folder(inbox)

        card = extract_card(car["url"], TIMEOUT)
        photos = [u for u in card.media_urls if any(x in u.lower() for x in (".jpg", ".jpeg", ".png", ".webp"))][:10]
        if not photos:
            raise RuntimeError(f"No photos for {car['model']} {car['url']}")

        for i, url in enumerate(photos, 1):
            raw, ext = download_image(url)
            upload(f"{inbox}/{i:02d}{ext}", raw)

        manifest = {
            "job_id": jid,
            "inventory_no": car["inventory"],
            "model": car["model"],
            "expected": len(photos),
            "source_url": car["url"],
            "branding": "manual-quality-edit",
            "state": "waiting_ready_photos",
        }
        upload(f"{inbox}/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        print(f"STAGED {jid} {len(photos)}")


if __name__ == "__main__":
    main()
