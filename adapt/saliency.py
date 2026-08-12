"""OpenCV computer-vision layer: saliency, edges/contours, importance maps.

This module carries the "must implement" CV requirements:
  * saliency maps            -> :func:`saliency_map`
  * edge detection + contour -> :func:`edge_density`, :func:`detect_objects`
  * a fused importance map used to drive content-aware cropping.
"""
from __future__ import annotations

import cv2
import numpy as np


def _normalize(m: np.ndarray) -> np.ndarray:
    m = m.astype(np.float32)
    lo, hi = float(m.min()), float(m.max())
    if hi - lo < 1e-8:
        return np.zeros_like(m)
    return (m - lo) / (hi - lo)


def saliency_map(bgr: np.ndarray) -> np.ndarray:
    """Static saliency in [0,1], same HxW as the input.

    Tries spectral-residual, then fine-grained, then a Laplacian fallback so the
    pipeline never hard-fails if a cv2.saliency backend is unavailable.
    """
    try:
        sal = cv2.saliency.StaticSaliencySpectralResidual_create()
        ok, m = sal.computeSaliency(bgr)
        if ok:
            return _normalize(m)
    except Exception:
        pass
    try:
        sal = cv2.saliency.StaticSaliencyFineGrained_create()
        ok, m = sal.computeSaliency(bgr)
        if ok:
            return _normalize(m)
    except Exception:
        pass
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    lap = np.abs(cv2.Laplacian(gray, cv2.CV_32F))
    return _normalize(cv2.GaussianBlur(lap, (0, 0), 3))


def edge_density(bgr: np.ndarray, win: int = 31) -> np.ndarray:
    """Canny edge density (local fraction of edge pixels) in [0,1].

    High where text and detailed logos live — a strong cue for "important".
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 160)
    dens = cv2.blur((edges > 0).astype(np.float32), (win, win))
    return _normalize(dens)


def detect_objects(bgr: np.ndarray, min_area_frac: float = 0.002):
    """Edge + contour object detection -> list of (l, t, r, b) boxes.

    Used for the flat-image fallback where no PSD layers exist.
    """
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 160)
    edges = cv2.dilate(edges, np.ones((7, 7), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    min_area = min_area_frac * w * h
    boxes = []
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        if cw * ch < min_area:
            continue
        boxes.append((x, y, x + cw, y + ch))
    boxes.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
    return boxes[:12]


def importance_map(bgr: np.ndarray, elements=None, max_dim: int = 1400) -> np.ndarray:
    """Fuse saliency + edge density (+ known element boxes) into [0,1].

    When semantic elements are known (PSD path) their boxes are stamped in with a
    weight proportional to role priority, so the crop keeps logo/CTA/text first.

    The map only ever drives coarse decisions — which band of the image to keep —
    so it is computed on a copy no larger than ``max_dim`` and scaled back up.
    On a 24-megapixel master that is the difference between seconds and tens of
    seconds, and the chosen crop is identical.
    """
    H, W = bgr.shape[:2]
    k = min(1.0, max_dim / max(H, W))
    small = (cv2.resize(bgr, (max(1, round(W * k)), max(1, round(H * k))),
                        interpolation=cv2.INTER_AREA) if k < 1 else bgr)

    imp = 0.5 * saliency_map(small) + 0.5 * edge_density(small)

    if elements:
        h, w = small.shape[:2]
        boost = np.zeros((h, w), np.float32)
        for el in elements:
            if el.role in ("background",):
                continue
            l, t, r, b = (round(v * k) for v in el.bbox)
            l, t = max(0, l), max(0, t)
            r, b = min(w, r), min(h, b)
            if r <= l or b <= t:
                continue
            boost[t:b, l:r] = np.maximum(boost[t:b, l:r], el.priority / 100.0)
        boost = cv2.GaussianBlur(boost, (0, 0), max(1.0, 9 * k))
        imp = imp + 1.2 * boost

    if k < 1:
        imp = cv2.resize(imp, (W, H), interpolation=cv2.INTER_LINEAR)
    return _normalize(imp)
