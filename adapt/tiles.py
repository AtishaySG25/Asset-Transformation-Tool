"""Tile rendering — the single place where a placement becomes pixels.

The layout planner (:mod:`adapt.reflow`), the renderer (:mod:`adapt.render`) and
the web editor all draw through these functions, so a manually-edited layout is
rasterised by exactly the same code as the algorithmic one. Everything here is
deterministic in its arguments: given the same element and the same numbers you
always get the same tile, which is what lets a plan be stored as plain JSON and
re-rendered later.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from . import saliency, textflow
from .background import fill_background


# ------------------------------------------------------------ background --
def base_color(bg_rgb: Image.Image) -> tuple[int, int, int]:
    """Sample the dominant (light) colour from the top band of the background."""
    a = np.array(bg_rgb.convert("RGB"))
    top = a[: max(1, a.shape[0] // 20)].reshape(-1, 3)
    med = np.median(top, axis=0)
    return int(med[0]), int(med[1]), int(med[2])


_SAL_CACHE: dict[int, np.ndarray] = {}


def bg_saliency(bg_rgb: Image.Image) -> np.ndarray:
    """Background saliency at full size, computed once on a downscaled copy."""
    key = id(bg_rgb)
    if key in _SAL_CACHE:
        return _SAL_CACHE[key]
    scale = 360 / max(bg_rgb.size)
    small = bg_rgb.convert("RGB").resize(
        (max(1, round(bg_rgb.width * scale)), max(1, round(bg_rgb.height * scale))))
    sal = saliency.saliency_map(np.array(small)[:, :, ::-1].copy())
    full = np.asarray(Image.fromarray((sal * 255).astype(np.uint8))
                      .resize(bg_rgb.size, Image.BILINEAR), dtype=np.float32) / 255.0
    _SAL_CACHE[key] = full
    return full


def saliency_row(bg_rgb: Image.Image) -> float:
    """Saliency-weighted mean row (normalised 0..1) — where the imagery lives."""
    sal = bg_saliency(bg_rgb)
    rows = sal.sum(axis=1)
    ys = np.arange(sal.shape[0])
    return float((ys * rows).sum() / (rows.sum() + 1e-8)) / sal.shape[0]


def dominant_color(rgba: Image.Image) -> tuple[int, int, int]:
    """Median colour of an element's opaque pixels (e.g. a logo bar's fill)."""
    arr = np.array(rgba.convert("RGBA"))
    op = arr[arr[:, :, 3] > 200][:, :3]
    if len(op) == 0:
        return (128, 0, 64)
    m = np.median(op, axis=0)
    return int(m[0]), int(m[1]), int(m[2])


# --------------------------------------------------------------- helpers --
def fit(img: Image.Image, max_w: float, max_h: float) -> Image.Image:
    """Scale to fit inside (max_w, max_h), preserving aspect."""
    w, h = img.size
    s = min(max_w / w, max_h / h)
    return img.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)


_TEXT_CACHE: dict[int, bool] = {}


def is_reflow_text(el) -> bool:
    """A pure text block (transparent, multi-word) — safe to re-wrap.

    Badges/buttons/logos are type-backed too but have a solid fill, so the
    ink-fill test keeps them as scaled units rather than shredding them.
    """
    key = id(el)
    if key in _TEXT_CACHE:
        return _TEXT_CACHE[key]
    res = (el.is_type and textflow.ink_fill(el.image) <= 0.45
           and len(textflow.segment_words(el.image)) >= 2)
    _TEXT_CACHE[key] = res
    return res


# ----------------------------------------------------------------- tiles --
def text_tile(el, wrap_w: int, line_h: int, align: str = "left",
              ss: int = 1) -> Image.Image:
    """Re-wrap a text element into a column ``wrap_w`` wide.

    ``line_h`` drives the text size; the block's height follows from the wrapping.
    ``ss`` only supersamples the rasterisation — the wrap itself is always decided
    at 1x, so the block a plan measured is the block that gets rendered.
    """
    return textflow.reflow_to_width(el.image, max(8, int(wrap_w)),
                                    max(1, int(line_h)), align=align, ss=ss)


def graphic_tile(el, w: int, h: int) -> Image.Image:
    """Scale a graphic element to an exact box (aspect is the caller's business)."""
    return el.image.resize((max(1, int(w)), max(1, int(h))), Image.LANCZOS)


def photo_band_tile(bg: Image.Image, w: int, h: int, feather: float = 0.22,
                    focus_x: float = 0.5, focus_y: float | None = None) -> Image.Image:
    """The background imagery as a band, optionally with soft left/right edges so
    it blends into the base instead of reading as a pasted-in tile."""
    w, h = max(1, int(w)), max(1, int(h))
    if focus_y is None:
        focus_y = saliency_row(bg)
    panel = fill_background(bg, w, h, focus=(focus_x, focus_y)).convert("RGBA")
    if feather <= 0:
        return panel
    arr = np.array(panel)
    fw = max(1, int(feather * w))
    ramp = np.ones(arr.shape[1], np.float32)
    ramp[:fw] = np.linspace(0.0, 1.0, fw)
    ramp[-fw:] = np.linspace(1.0, 0.0, fw)
    arr[:, :, 3] = (arr[:, :, 3].astype(np.float32) * ramp[None, :]).astype(np.uint8)
    return Image.fromarray(arr)
