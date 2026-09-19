from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

import cv2
import numpy as np


@dataclass
class QualityBrandResult:
    wall_logo: bool = False
    plate: bool = False
    manual_review: bool = False
    reason: str | None = None

    @property
    def changed(self) -> bool:
        return self.wall_logo or self.plate

    def to_dict(self) -> dict:
        return asdict(self) | {"changed": self.changed}


def _red_mask(img: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    lo = cv2.inRange(hsv, (0, 45, 35), (14, 255, 255))
    hi = cv2.inRange(hsv, (166, 45, 35), (179, 255, 255))
    mask = cv2.bitwise_or(lo, hi)
    mask = cv2.medianBlur(mask, 5)
    return mask


def _order_box(points: np.ndarray) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([
        pts[np.argmin(s)],
        pts[np.argmin(d)],
        pts[np.argmax(s)],
        pts[np.argmax(d)],
    ], dtype=np.float32)


def _largest_red_rect(mask: np.ndarray, y0: int, y1: int, x0: int, x1: int,
                      min_area: float, min_aspect: float, max_aspect: float):
    roi = np.zeros_like(mask)
    roi[y0:y1, x0:x1] = mask[y0:y1, x0:x1]
    contours, _ = cv2.findContours(roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for cnt in contours:
        area = float(cv2.contourArea(cnt))
        if area < min_area:
            continue
        rect = cv2.minAreaRect(cnt)
        (_cx, _cy), (rw, rh), _ = rect
        if min(rw, rh) < 8:
            continue
        aspect = max(rw, rh) / max(1.0, min(rw, rh))
        if not (min_aspect <= aspect <= max_aspect):
            continue
        if best is None or area > best[0]:
            best = (area, rect, cnt)
    return best


def _text_scale(text: str, font: int, max_w: int, max_h: int, thickness: int) -> float:
    for scale in np.linspace(3.0, 0.15, 160):
        (tw, th), base = cv2.getTextSize(text, font, float(scale), thickness)
        if tw <= max_w and th + base <= max_h:
            return float(scale)
    return 0.15


def _draw_centered_text(img: np.ndarray, text: str, y: int, max_w: int, max_h: int,
                        color: tuple[int, int, int], thickness: int) -> None:
    font = cv2.FONT_HERSHEY_DUPLEX
    scale = _text_scale(text, font, max_w, max_h, thickness)
    (tw, th), _ = cv2.getTextSize(text, font, scale, thickness)
    x = max(0, (img.shape[1] - tw) // 2)
    cv2.putText(img, text, (x, y + th // 2), font, scale, color, thickness, cv2.LINE_AA)


def _brand_patch(width: int = 1000, height: int = 420, dark: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Return BGR patch + alpha with FA / FILINKOV AUTO, no rectangular background."""
    patch = np.zeros((height, width, 3), dtype=np.uint8)
    alpha = np.zeros((height, width), dtype=np.uint8)
    color = (245, 245, 245) if dark else (25, 25, 25)

    # Stylised FA mark: geometric, intentionally simple and reproducible.
    cx = width // 2
    top = int(height * 0.05)
    mark_h = int(height * 0.28)
    mark_w = int(width * 0.20)
    t = max(8, int(height * 0.035))
    pts = [
        np.array([[cx-mark_w//2, top+mark_h], [cx-mark_w//2, top], [cx, top], [cx-t, top+t*2], [cx-mark_w//2+t*2, top+t*2], [cx-mark_w//2+t*2, top+mark_h]], np.int32),
        np.array([[cx+t, top], [cx+mark_w//2, top+mark_h], [cx+mark_w//2-t*2, top+mark_h], [cx, top+t*2]], np.int32),
    ]
    for p in pts:
        cv2.fillPoly(patch, [p], color)
        cv2.fillPoly(alpha, [p], 255)

    y1 = int(height * 0.49)
    _draw_centered_text(patch, "FILINKOV", y1, int(width*0.78), int(height*0.19), color, max(2, int(height*0.012)))
    _draw_centered_text(alpha, "FILINKOV", y1, int(width*0.78), int(height*0.19), 255, max(2, int(height*0.012)))

    y2 = int(height * 0.72)
    _draw_centered_text(patch, "AUTO", y2, int(width*0.28), int(height*0.13), color, max(2, int(height*0.009)))
    _draw_centered_text(alpha, "AUTO", y2, int(width*0.28), int(height*0.13), 255, max(2, int(height*0.009)))

    line_y = int(height * 0.79)
    left1, left2 = int(width*0.20), int(width*0.40)
    right1, right2 = int(width*0.60), int(width*0.80)
    line_t = max(3, int(height*0.01))
    for a, b in ((left1, left2), (right1, right2)):
        cv2.line(patch, (a, line_y), (b, line_y), color, line_t, cv2.LINE_AA)
        cv2.line(alpha, (a, line_y), (b, line_y), 255, line_t, cv2.LINE_AA)
    return patch, alpha


def _warp_overlay(base: np.ndarray, patch: np.ndarray, alpha: np.ndarray, box: np.ndarray,
                  shadow: bool = True) -> np.ndarray:
    h, w = base.shape[:2]
    ph, pw = patch.shape[:2]
    src = np.array([[0, 0], [pw-1, 0], [pw-1, ph-1], [0, ph-1]], dtype=np.float32)
    H = cv2.getPerspectiveTransform(src, box.astype(np.float32))
    warped = cv2.warpPerspective(patch, H, (w, h), flags=cv2.INTER_CUBIC)
    a = cv2.warpPerspective(alpha, H, (w, h), flags=cv2.INTER_CUBIC)

    out = base.astype(np.float32)
    if shadow:
        shadow_alpha = cv2.GaussianBlur(a, (0, 0), 2.2)
        shadow_alpha = np.roll(shadow_alpha, 2, axis=0)
        shadow_alpha = np.roll(shadow_alpha, 2, axis=1).astype(np.float32) / 255.0 * 0.22
        out *= (1.0 - shadow_alpha[..., None])

    af = cv2.GaussianBlur(a, (0, 0), 0.65).astype(np.float32) / 255.0
    out = warped.astype(np.float32) * af[..., None] + out * (1.0 - af[..., None])
    return np.clip(out, 0, 255).astype(np.uint8)


def _replace_wall(img: np.ndarray, red: np.ndarray) -> tuple[np.ndarray, bool]:
    h, w = img.shape[:2]
    found = _largest_red_rect(
        red, 0, int(h*0.34), int(w*0.03), int(w*0.97),
        min_area=max(2500, w*h*0.0018), min_aspect=1.8, max_aspect=7.0,
    )
    if not found:
        return img, False

    _area, rect, cnt = found
    box = _order_box(cv2.boxPoints(rect))
    rw = np.linalg.norm(box[1] - box[0])
    rh = np.linalg.norm(box[3] - box[0])
    if rw < w*0.10 or rh < h*0.025:
        return img, False

    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.drawContours(mask, [cnt], -1, 255, thickness=cv2.FILLED)
    k = max(5, int(round(w*0.008)) | 1)
    mask = cv2.dilate(mask, np.ones((k, k), np.uint8), iterations=1)
    clean = cv2.inpaint(img, mask, 5, cv2.INPAINT_TELEA)

    # Expand target slightly so replacement covers the original sign footprint.
    center = box.mean(axis=0)
    box = center + (box-center)*1.05
    patch, alpha = _brand_patch(dark=False)
    return _warp_overlay(clean, patch, alpha, box, shadow=True), True


def _replace_plate(img: np.ndarray, red: np.ndarray) -> tuple[np.ndarray, bool]:
    h, w = img.shape[:2]
    found = _largest_red_rect(
        red, int(h*0.36), int(h*0.86), int(w*0.10), int(w*0.90),
        min_area=max(1200, w*h*0.0007), min_aspect=1.7, max_aspect=6.5,
    )
    if not found:
        return img, False

    _area, rect, _cnt = found
    box = _order_box(cv2.boxPoints(rect))
    center = box.mean(axis=0)
    box = center + (box-center)*1.05

    ph, pw = 260, 900
    patch = np.zeros((ph, pw, 3), dtype=np.uint8)
    patch[:] = (15, 15, 15)
    alpha = np.full((ph, pw), 255, dtype=np.uint8)
    logo, logo_a = _brand_patch(width=900, height=360, dark=True)
    logo = cv2.resize(logo, (700, 280), interpolation=cv2.INTER_AREA)
    logo_a = cv2.resize(logo_a, (700, 280), interpolation=cv2.INTER_AREA)
    y0 = max(0, (ph-280)//2)
    x0 = (pw-700)//2
    y1 = min(ph, y0+280)
    lh = y1-y0
    la = logo_a[:lh].astype(np.float32)/255.0
    patch[y0:y1, x0:x0+700] = (
        logo[:lh].astype(np.float32)*la[...,None] + patch[y0:y1, x0:x0+700].astype(np.float32)*(1-la[...,None])
    ).astype(np.uint8)
    return _warp_overlay(img, patch, alpha, box, shadow=False), True


def brand_photo_bytes(raw: bytes, filename: str = "photo.jpg") -> tuple[bytes, QualityBrandResult]:
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Unsupported or corrupt image")

    red = _red_mask(img)
    out, wall = _replace_wall(img, red)
    # Recompute after wall replacement so wall red cannot be mistaken for plate.
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
