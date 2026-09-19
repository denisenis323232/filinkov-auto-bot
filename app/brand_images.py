from __future__ import annotations

import base64
import os

import requests

OPENAI_IMAGE_EDIT_URL = "https://api.openai.com/v1/images/edits"
MODEL = os.getenv("IMAGE_EDIT_MODEL", "gpt-image-2.5-sunburst")
QUALITY = os.getenv("IMAGE_EDIT_QUALITY", "high")

PROMPT = """Edit this exact original car photo with the smallest possible changes.
Do NOT redesign, regenerate, restyle, move, resize, recolor, clean up, retouch, or alter the car, wheels, body panels, lights, glass, interior, steering wheel, background geometry, camera angle, reflections, shadows, or lighting.
Only make these branding changes when the relevant object is visible:
1) Replace any dealer/supplier showroom wall branding or the word/logo АВТОМИР / AVTOMIR with a realistic mounted FILINKOV AUTO sign. The sign should be black/white, premium and subtle, with a stylized FA monogram and the words FILINKOV AUTO, respecting the exact wall perspective, focus, illumination and reflections.
2) Replace only the visible license plate / dealer plate area with a clean black plate reading FILINKOV AUTO in white. Preserve the exact plate position, perspective, size, blur and lighting.
If neither supplier branding nor a plate is visible, return the original photo visually unchanged.
Critical: the vehicle itself must remain the same real vehicle with the same exact geometry and details. This is a local branding edit, not a new rendering."""


class BrandingNotConfigured(RuntimeError):
    pass


def _download(url: str, timeout: int = 30) -> tuple[bytes, str]:
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 FILINKOV-AUTO/1.0"})
    r.raise_for_status()
    ct = (r.headers.get("content-type") or "image/jpeg").split(";")[0]
    if not ct.startswith("image/"):
        ct = "image/jpeg"
    return r.content, ct


def _edit_bytes(image_bytes: bytes, content_type: str, timeout: int = 180) -> bytes:
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise BrandingNotConfigured("OPENAI_API_KEY is not configured")

    ext = ".png" if "png" in content_type else ".jpg"
    files = [("image[]", (f"car{ext}", image_bytes, content_type))]
    data = {
        "model": MODEL,
        "prompt": PROMPT,
        "quality": QUALITY,
    }
    r = requests.post(
        OPENAI_IMAGE_EDIT_URL,
        headers={"Authorization": f"Bearer {key}"},
        files=files,
        data=data,
        timeout=timeout,
    )
    r.raise_for_status()
    payload = r.json()
    return base64.b64decode(payload["data"][0]["b64_json"])


def brand_first_ten(urls: list[str], timeout: int = 30) -> list[bytes]:
    """Brand at most 10 source photos. If one edit fails, keep that original photo."""
    out: list[bytes] = []
    for url in urls[:10]:
        original, ct = _download(url, timeout=timeout)
        try:
            out.append(_edit_bytes(original, ct))
        except BrandingNotConfigured:
            raise
        except Exception:
            out.append(original)
    return out
