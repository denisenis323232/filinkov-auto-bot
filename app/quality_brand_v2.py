from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from app.quality_brand import (
    QualityBrandResult,
    _brand_patch,
    _order_box,
    _red_mask,
    _replace_plate,
    _warp_overlay,
)


def _wall_cluster(red: np.ndarray):
    """Find red supplier wordmarks made of separate letters, not a solid rectangle."""
    h, w = red.shape[:2]
    roi = np.zeros_like(red)
    # Supplier branding in these cards sits on the showroom wall in the upper half.
    roi[: int(h * 0.48), int(w * 0.02) : int(w * 0.98)] = red[
        : int(h * 0.48), int(w * 0.02) : int(w * 0.98)
    ]

    # Join individual Chinese/AVTOMIR letters into one wordmark component.
    kx = max(13, int(w * 0.028))
    ky = max(7, int(h * 0.012))
    joined = cv2.morphologyEx(
        roi,
        cv2.MORPH_CLOSE,
        np.ones((ky, kx), np.uint8),
        iterations=2,
    )
    joined = cv2.dilate(
        joined,
        np.ones((max(3, ky // 2), max(7, kx // 2)), np.uint8),
        iterations=1,
    )

    contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for cnt in contours:
        x, y, bw, bh = cv2.boundingRect(cnt)
        if bw < w * 0.055 or bh < h * 0.018:
            continue
        if bw > w * 0.48 or bh > h * 0.22:
            continue
        aspect = bw / max(1.0, bh)
        if not (1.15 <= aspect <= 9.5):
            continue
        # Require actual red ink inside the cluster, not only dilation.
        ink = int(cv2.countNonZero(red[y : y + bh, x : x + bw]))
        if ink < max(120, int(w * h * 0.00008)):
            continue
        score = ink * (1.0 + min(aspect, 5.0) * 0.08)
        candidates.append((score, x, y, bw, bh))

    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])


def _replace_wall_v2(img: np.ndarray, red: np.ndarray) -> tuple[np.ndarray, bool]:
    h, w = img.shape[:2]
    found = _wall_cluster(red)
    if not found:
        return img, False

    _score, x, y, bw, bh = found

    # Generous mask removes the complete supplier wordmark including thin strokes.
    pad_x = max(8, int(bw * 0.13))
    pad_y = max(6, int(bh * 0.22))
    x0, y0 = max(0, x - pad_x), max(0, y - pad_y)
    x1, y1 = min(w - 1, x + bw + pad_x), min(h - 1, y + bh + pad_y)

    mask = np.zeros((h, w), dtype=np.uint8)
    local_red = red[y0 : y1 + 1, x0 : x1 + 1]
    mask[y0 : y1 + 1, x0 : x1 + 1] = local_red
    dil = max(5, (int(w * 0.006) | 1))
    mask = cv2.dilate(mask, np.ones((dil, dil), np.uint8), iterations=1)
    clean = cv2.inpaint(img, mask, 5, cv2.INPAINT_TELEA)

    # Put FILINKOV in the same physical footprint, respecting perspective enough
    # for the nearly planar showroom wall used by this supplier.
    box = np.array(
        [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
        dtype=np.float32,
    )
    center = box.mean(axis=0)
    box = center + (box - center) * 1.04
    patch, alpha = _brand_patch(dark=False)
    return _warp_overlay(clean, patch, alpha, box, shadow=True), True


def brand_photo_bytes(raw: bytes, filename: str = "photo.jpg") -> tuple[bytes, QualityBrandResult]:
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Unsupported or corrupt image")

    red = _red_mask(img)
    out, wall = _replace_wall_v2(img, red)
    red2 = _red_mask(out)
    out, plate = _replace_plate(out, red2)
    result = QualityBrandResult(wall_logo=wall, plate=plate)

    if not result.changed:
        return raw, result

    ext = Path(filename).suffix.lower()
    if ext == ".png":
        ok, enc = cv2.imencode(".png", out, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    elif ext == ".webp":
        ok, enc = cv2.imencode(".webp", out, [cv2.IMWRITE_WEBP_QUALITY, 97])
    else:
        ok, enc = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 97])
    if not ok:
        raise RuntimeError("Could not encode branded image")
    return enc.tobytes(), result
