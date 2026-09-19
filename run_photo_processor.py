from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import requests

from app.quality_brand import brand_photo_bytes

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | photo-processor | %(message)s")
log = logging.getLogger("photo-processor")

TOKEN = os.getenv("DROPBOX_ACCESS_TOKEN", "").strip()
ROOT = os.getenv("DROPBOX_ROOT", "/FILINKOV_AUTO").rstrip("/")
POLL_SECONDS = max(10, int(os.getenv("PHOTO_PROCESSOR_POLL_SECONDS", "20")))


def rpc(endpoint: str, payload: dict[str, Any], timeout: int = 30):
    if not TOKEN:
        raise RuntimeError("DROPBOX_ACCESS_TOKEN is missing")
    r = requests.post(
        f"https://api.dropboxapi.com/2/{endpoint}",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        data=json.dumps(payload).encode("utf-8"), timeout=timeout,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Dropbox {endpoint}: {r.status_code} {r.text[:500]}")
    return r.json()


def list_folder(path: str):
    try:
        data = rpc("files/list_folder", {"path": path, "recursive": False, "include_deleted": False})
    except RuntimeError as exc:
        if "not_found" in str(exc).lower():
            return []
        raise
    out = list(data.get("entries", []))
    while data.get("has_more"):
        data = rpc("files/list_folder/continue", {"cursor": data["cursor"]})
        out.extend(data.get("entries", []))
    return out


def download(path: str, timeout: int = 60) -> bytes:
    r = requests.post(
        "https://content.dropboxapi.com/2/files/download",
        headers={"Authorization": f"Bearer {TOKEN}", "Dropbox-API-Arg": json.dumps({"path": path})},
        timeout=timeout,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Dropbox download: {r.status_code} {r.text[:500]}")
    return r.content


def upload(path: str, raw: bytes, timeout: int = 60):
    r = requests.post(
        "https://content.dropboxapi.com/2/files/upload",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/octet-stream",
            "Dropbox-API-Arg": json.dumps({"path": path, "mode": "overwrite", "autorename": False, "mute": True}),
        },
        data=raw, timeout=timeout,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Dropbox upload: {r.status_code} {r.text[:500]}")
    return r.json()


def ensure_folder(path: str):
    cur = ""
    for part in [x for x in path.split("/") if x]:
        cur += "/" + part
        try:
            rpc("files/create_folder_v2", {"path": cur, "autorename": False})
        except RuntimeError as exc:
            if "conflict" not in str(exc).lower():
                raise


def images(entries):
    result = [e for e in entries if e.get(".tag") == "file" and str(e.get("name", "")).lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
    result.sort(key=lambda e: e.get("name", ""))
    return result


def process_job(jid: str) -> tuple[int, int]:
    inbox = f"{ROOT}/inbox/{jid}"
    ready = f"{ROOT}/ready/{jid}"
    ensure_folder(ready)

    try:
        manifest = json.loads(download(f"{inbox}/manifest.json").decode("utf-8"))
    except Exception as exc:
        log.warning("skip %s: no manifest: %s", jid, exc)
        return 0, 0

    expected = int(manifest.get("expected") or 0)
    if expected <= 0:
        return 0, 0

    originals = images(list_folder(inbox))[:expected]
    existing = {e.get("name") for e in images(list_folder(ready))}
    if len(existing) >= expected:
        return expected, expected

    reports = []
    done = 0
    for entry in originals:
        name = str(entry.get("name") or "")
        if name in existing:
            done += 1
            continue
        raw = download(entry["path_lower"])
        branded, result = brand_photo_bytes(raw, name)
        upload(f"{ready}/{name}", branded)
        rep = result.to_dict()
        rep["file"] = name
        reports.append(rep)
        done += 1
        log.info("%s %s/%s %s wall=%s plate=%s", jid, done, expected, name, result.wall_logo, result.plate)

    report = {
        "processor": "filinkov-quality-compositor-v1",
        "job_id": jid,
        "expected": expected,
        "processed": done,
        "files": reports,
    }
    upload(f"{ready}/quality_report.json", json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
    return done, expected


def scan_once():
    inbox_root = f"{ROOT}/inbox"
    folders = [e for e in list_folder(inbox_root) if e.get(".tag") == "folder"]
    total = len(folders)
    completed = 0
    for idx, entry in enumerate(folders, 1):
        jid = str(entry.get("name") or "")
        if not jid:
            continue
        try:
            done, expected = process_job(jid)
            if expected and done >= expected:
                completed += 1
            log.info("queue %s/%s jobs, current=%s photos=%s/%s", idx, total, jid, done, expected)
        except Exception:
            log.exception("job failed: %s", jid)
    return completed, total


def main():
    if not TOKEN:
        raise SystemExit("DROPBOX_ACCESS_TOKEN is missing")
    ensure_folder(ROOT)
    ensure_folder(f"{ROOT}/inbox")
    ensure_folder(f"{ROOT}/ready")
    log.info("processor started, root=%s poll=%ss", ROOT, POLL_SECONDS)
    while True:
        try:
            completed, total = scan_once()
            log.info("scan complete: %s/%s jobs have ready photos", completed, total)
        except Exception:
            log.exception("scan failed")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
