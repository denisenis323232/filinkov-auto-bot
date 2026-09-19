from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass
class BrandResult:
    wall_logo: bool = False
    plate: bool = False

    @property
    def changed(self) -> bool:
        return self.wall_logo or self.plate

    def to_dict(self) -> dict:
        return asdict(self) | {"changed": self.changed}


def _red_mask(img: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    low_red = cv2.inRange(hsv, (0, 45, 35), (15, 255, 255))
    high_red = cv2.inRange(hsv, (165, 45, 35), (179, 255, 255))
    return cv2.bitwise_or(low_red, high_red)


def _draw_fit_text(
    canvas: np.ndarray,
    text: str,
    center: tuple[int, int],
    max_w: int,
    max_h: int,
    color: tuple[int, int, int],
    thickness: int,
) -> None:
    font = cv2.FONT_HERSHEY_DUPLEX
    scale = 0.3
    for candidate in np.linspace(2.8, 0.2, 100):
        (tw, th), baseline = cv2.getTextSize(text, font, float(candidate), thickness)
        if tw <= max_w and th + baseline <= max_h:
            scale = float(candidate)
            break
    (tw, th), _ = cv2.getTextSize(text, font, scale, thickness)
    x = int(center[0] - tw / 2)
    y = int(center[1] + th / 2)
    cv2.putText(canvas, text, (x, y), font, scale, color, thickness, cv2.LINE_AA)


def _replace_wall_logo(img: np.ndarray) -> tuple[np.ndarray, bool]:
    """Replace the red AVTOMIR/Chinese wall sign used by the current supplier.

    Detection is intentionally conservative: a large dark-red sign must be present
    in the upper 30% of the frame. Interior photos therefore remain untouched.
    """
    h, w = img.shape[:2]
    red = _red_mask(img)
    roi = np.zeros_like(red)
    roi[: int(0.30 * h), int(0.05 * w) : int(0.95 * w)] = red[
        : int(0.30 * h), int(0.05 * w) : int(0.95 * w)
    ]

    if cv2.countNonZero(roi) < 15000:
        return img, False

    ys, xs = np.where(roi > 0)
    if len(xs) == 0:
        return img, False

    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    bw = x1 - x0 + 1
    bh = y1 - y0 + 1
    ratio = bw / max(1, bh)

    if not (
        0.12 * w <= bw <= 0.58 * w
        and 0.035 * h <= bh <= 0.20 * h
        and 2.1 <= ratio <= 5.5
    ):
        return img, False

    pad = max(8, int(0.012 * w))
    x0 = max(0, x0 - pad)
    x1 = min(w - 1, x1 + pad)
    y0 = max(0, y0 - pad)
    y1 = min(h - 1, y1 + pad)

    sign_mask = np.zeros_like(red)
    sign_mask[y0 : y1 + 1, x0 : x1 + 1] = red[y0 : y1 + 1, x0 : x1 + 1]
    kernel_size = max(7, (int(0.008 * w) // 2) * 2 + 1)
    sign_mask = cv2.dilate(
        sign_mask, np.ones((kernel_size, kernel_size), np.uint8), iterations=1
    )

    out = cv2.inpaint(img, sign_mask, 5, cv2.INPAINT_TELEA)

    # Keep the supplier's dark-red visual language so the replacement looks like
    # a physical wall sign rather than a digital banner.
    sign_color = (68, 62, 150)  # BGR
    cx = (x0 + x1) // 2
    sign_w = x1 - x0
    sign_h = y1 - y0
    thick = max(2, int(round(w / 700)))

    _draw_fit_text(
        out,
        "FILINKOV",
        (cx, int(y0 + sign_h * 0.42)),
        int(sign_w * 0.88),
        int(sign_h * 0.48),
        sign_color,
        thick,
    )
    _draw_fit_text(
        out,
        "AUTO",
        (cx, int(y0 + sign_h * 0.76)),
        int(sign_w * 0.34),
        int(sign_h * 0.25),
        sign_color,
        max(2, thick - 1),
    )
    return out, True


def _order_box(points: np.ndarray) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float32)
    sums = pts.sum(axis=1)
    diffs = np.diff(pts, axis=1).ravel()
    return np.array(
        [
            pts[np.argmin(sums)],
            pts[np.argmin(diffs)],
            pts[np.argmax(sums)],
            pts[np.argmax(diffs)],
        ],
        dtype=np.float32,
    )


def _replace_plate(img: np.ndarray) -> tuple[np.ndarray, bool]:
    """Replace the supplier's red VIP plate, preserving its perspective."""
    h, w = img.shape[:2]
    red = _red_mask(img)
    roi = np.zeros_like(red)
    roi[int(0.38 * h) : int(0.82 * h), int(0.15 * w) : int(0.85 * w)] = red[
        int(0.38 * h) : int(0.82 * h), int(0.15 * w) : int(0.85 * w)
    ]

    contours, _ = cv2.findContours(roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[tuple[float, tuple]] = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < 7000:
            continue
        rect = cv2.minAreaRect(contour)
        (cx, cy), (rw, rh), _angle = rect
        if min(rw, rh) < 10:
            continue
        aspect = max(rw, rh) / max(1.0, min(rw, rh))
        if not (1.8 <= aspect <= 5.5):
            continue
        if not (0.20 * w < cx < 0.80 * w and 0.42 * h < cy < 0.80 * h):
            continue
        candidates.append((area, rect))

    if not candidates:
        return img, False

    _area, rect = max(candidates, key=lambda item: item[0])
    box = _order_box(cv2.boxPoints(rect))
    center = box.mean(axis=0)
    box = center + (box - center) * 1.08
    box[:, 0] = np.clip(box[:, 0], 0, w - 1)
    box[:, 1] = np.clip(box[:, 1], 0, h - 1)

    patch_w, patch_h = 700, 220
    patch = np.zeros((patch_h, patch_w, 3), dtype=np.uint8)
    patch[:] = (35, 35, 120)
    cv2.rectangle(patch, (8, 8), (patch_w - 9, patch_h - 9), (72, 72, 170), 5)
    _draw_fit_text(
        patch,
        "FILINKOV AUTO",
        (patch_w // 2, patch_h // 2),
        int(patch_w * 0.86),
        int(patch_h * 0.55),
        (235, 235, 240),
        6,
    )

    src = np.array(
        [[0, 0], [patch_w - 1, 0], [patch_w - 1, patch_h - 1], [0, patch_h - 1]],
        dtype=np.float32,
    )
    transform = cv2.getPerspectiveTransform(src, box.astype(np.float32))
    warped = cv2.warpPerspective(patch, transform, (w, h))
    alpha_src = np.full((patch_h, patch_w), 255, dtype=np.uint8)
    alpha = cv2.warpPerspective(alpha_src, transform, (w, h))
    alpha = cv2.GaussianBlur(alpha, (0, 0), sigmaX=1.2).astype(np.float32) / 255.0
    alpha = alpha[..., None]

    out = (warped.astype(np.float32) * alpha + img.astype(np.float32) * (1.0 - alpha)).astype(
        np.uint8
    )
    return out, True


def brand_photo_bytes(raw: bytes, filename: str = "photo.jpg") -> tuple[bytes, BrandResult]:
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Unsupported or corrupt image")

    out, wall_changed = _replace_wall_logo(img)
    out, plate_changed = _replace_plate(out)
    result = BrandResult(wall_logo=wall_changed, plate=plate_changed)

    # Requirement: if neither target is visible, keep the file byte-for-byte unchanged.
    if not result.changed:
        return raw, result

    ext = Path(filename).suffix.lower()
    if ext in {".png"}:
        ok, encoded = cv2.imencode(".png", out, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    elif ext in {".webp"}:
        ok, encoded = cv2.imencode(".webp", out, [cv2.IMWRITE_WEBP_QUALITY, 95])
    else:
        ok, encoded = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        raise RuntimeError("Could not encode branded image")
    return encoded.tobytes(), result
