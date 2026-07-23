"""Saliency-aware smart crop -> exact target size.

Slides the largest target-aspect window across the importance map (via an
integral image, so each candidate is O(1)) and keeps the window with the most
important content, then resizes to the exact requested dimensions. The output
dimensions are always exact and there is never padding.
"""
from __future__ import annotations

from typing import Tuple

import cv2
import numpy as np
from PIL import Image

from ..config import Format


def _best_window(imap: np.ndarray, cw: int, ch: int) -> Tuple[int, int]:
    h, w = imap.shape
    integral = cv2.integral(imap)  # (h+1, w+1); integral[y, x] = sum above-left
    best_score = -1.0
    best = ((w - cw) // 2, (h - ch) // 2)
    step = max(6, min(cw, ch) // 25)
    for y in range(0, h - ch + 1, step):
        for x in range(0, w - cw + 1, step):
            score = (integral[y + ch, x + cw] - integral[y, x + cw]
                     - integral[y + ch, x] + integral[y, x])
            if score > best_score:
                best_score = score
                best = (x, y)
    return best


def smart_crop(composite_rgb: Image.Image, imap: np.ndarray, fmt: Format) -> Image.Image:
    """Crop the composite to the best target-aspect window, then resize exact."""
    w, h = composite_rgb.size
    a = fmt.aspect
    if a >= w / h:                 # target wider than source -> full width
        cw, ch = w, int(round(w / a))
    else:                          # target taller -> full height
        cw, ch = int(round(h * a)), h
    cw, ch = min(cw, w), min(ch, h)

    x, y = _best_window(imap, cw, ch)
    crop = composite_rgb.crop((x, y, x + cw, y + ch))
    return crop.resize((fmt.width, fmt.height), Image.LANCZOS), (x, y, cw, ch)
