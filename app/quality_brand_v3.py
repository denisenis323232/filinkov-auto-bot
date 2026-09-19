from __future__ import annotations

from pathlib import Path
import cv2
import numpy as np

from app.quality_brand import QualityBrandResult, _brand_patch, _red_mask, _warp_overlay


def _find_supplier_logo(red: np.ndarray, img: np.ndarray):
    h, w = red.shape[:2]
    # Supplier wall mark is always high on a bright showroom wall.
    y1 = int(h * 0.34)
    x0 = int(w * 0.03)
    x1 = int(w * 0.72)
    roi = red[:y1, x0:x1]

    # Merge the Chinese text + AVTOMIR letters into one compact block.
    kx = max(9, int(w * 0.018))
    ky = max(3, int(h * 0.006))
    joined = cv2.morphologyEx(roi, cv2.MORPH_CLOSE, np.ones((ky, kx), np.uint8), iterations=2)
    joined = cv2.dilate(joined, np.ones((max(3, ky), max(5, kx // 2)), np.uint8), iterations=1)

    contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    for cnt in contours:
        x, y, bw, bh = cv2.boundingRect(cnt)
        x += x0
        if bw < w * 0.06 or bw > w * 0.34:
            continue
        if bh < h * 0.025 or bh > h * 0.14:
            continue
        aspect = bw / max(1.0, bh)
        if not 1.3 <= aspect <= 6.5:
            continue

        # The sign must sit on a bright low-saturation wall. This rejects car lights,
        # dashboards, seats, reflections and other red objects.
        px = max(4, int(bw * 0.18))
        py = max(4, int(bh * 0.35))
        ax0, ay0 = max(0, x - px), max(0, y - py)
        ax1, ay1 = min(w, x + bw + px), min(h, y + bh + py)
        env = hsv[ay0:ay1, ax0:ax1]
        if env.size == 0:
            continue
        mean_v = float(env[..., 2].mean())
        mean_s = float(env[..., 1].mean())
        if mean_v < 135 or mean_s > 90:
            continue

        ink = int(cv2.countNonZero(red[y:y+bh, x:x+bw]))
        frac = ink / max(1, bw * bh)
        if frac < 0.008 or frac > 0.42:
            continue

        # Prefer marks higher on the wall and with enough real red ink.
        score = ink * (1.0 + (1.0 - y / max(1, y1)))
        if best is None or score > best[0]:
            best = (score, x, y, bw, bh)
    return best


def _replace_wall(img: np.ndarray, red: np.ndarray):
    h, w = img.shape[:2]
    found = _find_supplier_logo(red, img)
    if not found:
        return img, False
    _, x, y, bw, bh = found

    pad_x = max(5, int(bw * 0.10))
    pad_y = max(4, int(bh * 0.18))
    x0, y0 = max(0, x - pad_x), max(0, y - pad_y)
    x1, y1 = min(w - 1, x + bw + pad_x), min(h - 1, y + bh + pad_y)

    # Remove the complete original sign region, not only the red strokes.
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.rectangle(mask, (x0, y0), (x1, y1), 255, thickness=-1)
    clean = cv2.inpaint(img, mask, 5, cv2.INPAINT_TELEA)

    box = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float32)
    patch, alpha = _brand_patch(dark=False)
    return _warp_overlay(clean, patch, alpha, box, shadow=True), True


def brand_photo_bytes(raw: bytes, filename: str = "photo.jpg"):
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Unsupported or corrupt image")

    red = _red_mask(img)
    out, wall = _replace_wall(img, red)

    # Plate auto-detection is intentionally disabled until we have a reliable detector.
    # Random plate overlays are worse than leaving the original plate untouched.
    result = QualityBrandResult(wall_logo=wall, plate=False)
    if not wall:
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
