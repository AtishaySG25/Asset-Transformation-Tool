"""Compositing and saving of final assets."""
from __future__ import annotations

import os
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np
from PIL import Image
from sklearn.cluster import KMeans

Placement = Tuple[Image.Image, int, int]


def compose(size: Tuple[int, int], background: Optional[Image.Image],
            placements: Sequence[Placement]) -> Image.Image:
    """Paste placements onto the background. `paste` clips silently, so an
    off-by-one never raises — but the layout engine already guarantees fit."""
    canvas = background.copy() if background is not None \
        else Image.new("RGBA", size, (255, 255, 255, 255))
    for img, x, y in placements:
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        canvas.paste(img, (int(x), int(y)), img)
    return canvas


def dominant_color(rgb_img: Image.Image, k: int = 3) -> Tuple[int, int, int]:
    """Most common non-extreme color, for background fallback fills."""
    small = rgb_img.convert("RGB").resize((80, 80))
    pixels = np.array(small).reshape(-1, 3)
    mask = (pixels.sum(axis=1) > 40) & (pixels.sum(axis=1) < 720)
    pixels = pixels[mask] if mask.any() else pixels
    km = KMeans(n_clusters=min(k, len(pixels)), n_init=4, random_state=42).fit(pixels)
    _, counts = np.unique(km.labels_, return_counts=True)
    return tuple(int(c) for c in km.cluster_centers_[counts.argmax()])


def save(img: Image.Image, path: str, jpg: bool = False) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if jpg:
        img.convert("RGB").save(path, quality=95, subsampling=0, optimize=True)
    else:
        (img if img.mode == "RGBA" else img.convert("RGBA")).save(path, optimize=True)
