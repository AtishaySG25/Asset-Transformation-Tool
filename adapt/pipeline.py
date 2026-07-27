"""Orchestration: master asset -> six exact-sized secondary assets.

Routes each format to the right transform (content-aware crop vs. element
re-layout), writes the PNGs, and optionally emits CV debug renders and a
contact-sheet montage.
"""
from __future__ import annotations

import os
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import saliency
from .formats import FORMATS, strategy_for
from .psd_source import load, Source
from .smartcrop import fit_all, content_aware_crop
from .reflow import reflow


def _bgr(pil_rgb: Image.Image) -> np.ndarray:
    return np.array(pil_rgb.convert("RGB"))[:, :, ::-1].copy()


def _sharpen(img: Image.Image) -> Image.Image:
    """Counteract the softening from large downscales — keeps small text crisp."""
    return img.filter(ImageFilter.UnsharpMask(radius=1.0, percent=150, threshold=1))


def _is_photo_like(source: Source) -> bool:
    """True when there are no real foreground elements to re-arrange — e.g. a
    single-photo PSD. Such assets get plain content-aware cropping instead of a
    (meaningless) element reflow."""
    canvas = source.width * source.height
    foreground = [e for e in source.elements if e.width * e.height < 0.6 * canvas]
    return len(foreground) < 2


def effective_strategy(source: Source, fmt) -> str:
    """The transform this format will actually take for this source.

    A source with no structured elements can't be re-laid-out, so it takes the
    'photo' path whatever its aspect ratio says.
    """
    if _is_photo_like(source):
        return "photo"
    return strategy_for(fmt, source.aspect)


def transform(source: Source, fmt, importance: np.ndarray) -> Image.Image:
    strategy = effective_strategy(source, fmt)
    if strategy == "photo":
        # No elements to re-arrange: fill the frame by saliency-guided crop.
        img = content_aware_crop(source.composite, importance, fmt.width, fmt.height)
    elif strategy == "crop":
        # Near-square: keep every element by resizing the whole composite to fill.
        img = fit_all(source.composite, fmt.width, fmt.height)
    else:
        img = reflow(source, fmt.width, fmt.height)
    return _sharpen(img)


def _save_debug(source: Source, importance: np.ndarray, out_dir: str):
    dbg = os.path.join(out_dir, "debug")
    os.makedirs(dbg, exist_ok=True)

    # Fused importance heatmap.
    heat = (importance * 255).astype(np.uint8)
    Image.fromarray(heat).save(os.path.join(dbg, "importance.png"))

    # Detected/known element boxes over the composite.
    overlay = source.composite.convert("RGB").copy()
    d = ImageDraw.Draw(overlay)
    for el in source.elements:
        d.rectangle(el.bbox, outline=(255, 0, 0), width=4)
        d.text((el.bbox[0] + 4, el.bbox[1] + 4), f"{el.role}", fill=(255, 255, 0))
    overlay.save(os.path.join(dbg, "elements.png"))


def _montage(results, out_dir: str):
    pad = 16
    cols = max((r[1].width for r in results), default=0)
    total_h = sum(r[1].height for r in results) + pad * (len(results) + 1)
    sheet = Image.new("RGB", (cols + 2 * pad, total_h), (32, 32, 36))
    d = ImageDraw.Draw(sheet)
    y = pad
    for name, img in results:
        sheet.paste(img, (pad, y))
        d.text((pad + 2, y - 12), name, fill=(230, 230, 230))
        y += img.height + pad
    sheet.save(os.path.join(out_dir, "montage.png"))


def run(input_path: str, out_dir: str = "output", debug: bool = True,
        sizes=None) -> list[str]:
    source = load(input_path)
    importance = saliency.importance_map(_bgr(source.composite), source.elements)

    os.makedirs(out_dir, exist_ok=True)
    formats = FORMATS if sizes is None else sizes

    results, paths = [], []
    for fmt in formats:
        img = transform(source, fmt, importance)
        assert img.size == (fmt.width, fmt.height), f"size mismatch for {fmt.name}"
        path = os.path.join(out_dir, f"{fmt.name}.png")
        img.save(path)
        paths.append(path)
        results.append((fmt.name, img))
        print(f"  [{effective_strategy(source, fmt):8s}] {fmt.name:>8s} -> {path}")

    if debug:
        _save_debug(source, importance, out_dir)
        _montage(results, out_dir)
        print(f"  debug renders + montage written to {out_dir}")

    return paths
