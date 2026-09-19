from __future__ import annotations

import hmac
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

LOCAL_ROOT = Path(os.getenv("INGEST_ROOT", "/tmp/filinkov_ready"))
MAX_BYTES = 12 * 1024 * 1024
ALLOWED_HOST_SUFFIXES = (
    ".dropboxusercontent.com",
    ".oaistatic.com",
    ".oaiusercontent.com",
    "files.oaiusercontent.com",
)


def _safe_inventory(value: str) -> str:
    value = re.sub(r"[^0-9A-Za-z_-]+", "", value or "")
    return value[:40]


def local_folder(inventory_no: str) -> Path:
    return LOCAL_ROOT / _safe_inventory(inventory_no)


def local_entries(inventory_no: str) -> list[dict]:
    folder = local_folder(inventory_no)
    if not folder.exists():
        return []
    found: dict[int, dict] = {}
    for path in folder.iterdir():
        if not path.is_file():
            continue
        m = re.fullmatch(r"(0[1-9]|10)\.(jpg|jpeg|png|webp)", path.name.lower())
        if not m:
            continue
        found[int(m.group(1))] = {
            ".tag": "file",
            "name": path.name,
            "local_path": str(path),
        }
    return [found[n] for n in sorted(found)]


def read_local(entry: dict) -> bytes:
    return Path(entry["local_path"]).read_bytes()


def _car_exists(db, inventory_no: str) -> bool:
    with db.connect() as con:
        row = con.execute("SELECT id FROM cars WHERE inventory_no=? LIMIT 1", (inventory_no,)).fetchone()
        return bool(row)


def _download_source(url: str, timeout: int) -> tuple[bytes, str]:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host or not any(host == suffix.lstrip(".") or host.endswith(suffix) for suffix in ALLOWED_HOST_SUFFIXES):
        raise ValueError("source host is not allowed")
    with requests.get(url, timeout=timeout, stream=True, headers={"User-Agent": "FILINKOV-AUTO-ingest/1.0"}) as resp:
        resp.raise_for_status()
        ctype = (resp.headers.get("content-type") or "image/jpeg").lower()
        if "image" not in ctype:
            raise ValueError("source is not an image")
        chunks = []
        total = 0
        for chunk in resp.iter_content(256 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_BYTES:
                raise ValueError("image too large")
            chunks.append(chunk)
    ext = ".png" if "png" in ctype else ".webp" if "webp" in ctype else ".jpg"
    return b"".join(chunks), ext


def start_ingest_server(db, logger, timeout: int = 30) -> ThreadingHTTPServer | None:
    token = os.getenv("INGEST_TOKEN", "").strip()
    port = int(os.getenv("PORT", "8080"))
    if not token:
        logger.warning("INGEST_TOKEN is not configured; HTTP ingest bridge is disabled")
        return None

    LOCAL_ROOT.mkdir(parents=True, exist_ok=True)

    class Handler(BaseHTTPRequestHandler):
        server_version = "FILINKOVIngest/1.0"

        def log_message(self, fmt, *args):
            # Never log query strings because they contain a short-lived source URL/token.
            logger.info("ingest-http %s", self.path.split("?", 1)[0])

        def _json(self, code: int, payload: dict):
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._json(200, {"ok": True, "service": "filinkov-auto-bot"})
                return
            if parsed.path != "/ingest":
                self._json(404, {"ok": False, "error": "not_found"})
                return

            q = parse_qs(parsed.query)
            supplied = (q.get("token") or [""])[0]
            if not hmac.compare_digest(supplied, token):
                self._json(403, {"ok": False, "error": "forbidden"})
                return

            inventory = _safe_inventory((q.get("inventory") or [""])[0])
            source_url = (q.get("url") or [""])[0]
            try:
                slot = int((q.get("slot") or ["0"])[0])
            except Exception:
                slot = 0
            if not inventory or slot < 1 or slot > 10 or not source_url:
                self._json(400, {"ok": False, "error": "inventory, slot 1..10 and url are required"})
                return
            if not _car_exists(db, inventory):
                self._json(404, {"ok": False, "error": "unknown_inventory"})
                return

            try:
                raw, ext = _download_source(source_url, timeout)
                folder = local_folder(inventory)
                folder.mkdir(parents=True, exist_ok=True)
                for old_ext in (".jpg", ".jpeg", ".png", ".webp"):
                    old = folder / f"{slot:02d}{old_ext}"
                    if old.exists():
                        old.unlink()
                target = folder / f"{slot:02d}{ext}"
                target.write_bytes(raw)
                count = len(local_entries(inventory))
                self._json(200, {"ok": True, "inventory": inventory, "slot": slot, "count": count, "name": target.name})
            except Exception as exc:
                logger.exception("HTTP ingest failed for %s slot %s", inventory, slot)
                self._json(502, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    thread = threading.Thread(target=server.serve_forever, name="filinkov-ingest-http", daemon=True)
    thread.start()
    logger.info("Individual-photo ingest bridge listening on port %s", port)
    return server
