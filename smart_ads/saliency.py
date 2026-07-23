"""Saliency + importance map used by the crop strategy.

We combine OpenCV's fine-grained saliency (where the eye goes) with hard boosts
on classified element boxes (where the brand-critical content actually is), so
crops keep the logo/headline/CTA rather than just the most "interesting" pixels.
"""
from __future__ import annotations

from typing import List

import cv2
import numpy as np

from .element import SemanticElement


def compute_saliency(rgb: np.ndarray) -> np.ndarray:
    """Return a HxW float saliency map in [0, 1]."""
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    if hasattr(cv2, "saliency"):
        sal = cv2.saliency.StaticSaliencyFineGrained_create()
        ok, m = sal.computeSaliency(bgr)
        if ok:
            return cv2.normalize(m.astype("float32"), None, 0, 1, cv2.NORM_MINMAX)
    # Fallback: edge energy.
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 100, 200).astype("float32")
    edges = cv2.GaussianBlur(edges, (5, 5), 0)
    return cv2.normalize(edges, None, 0, 1, cv2.NORM_MINMAX)


def build_importance_map(rgb: np.ndarray, elements: List[SemanticElement]) -> np.ndarray:
    """Saliency plus per-element importance boosts. HxW float32."""
    h, w = rgb.shape[:2]
    imap = compute_saliency(rgb).astype("float32")
    for el in elements:
        x0, y0, x1, y1 = el.bbox
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, x1), min(h, y1)
        if x1 > x0 and y1 > y0:
            imap[y0:y1, x0:x1] += el.importance * 2.0
    return imap
