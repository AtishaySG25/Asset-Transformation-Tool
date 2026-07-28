"""Orchestration: master asset -> six exact-sized secondary assets.

Routes each format to the right transform (content-aware crop vs. element
re-layout), writes the PNGs, and optionally emits CV debug renders and a
contact-sheet montage.

Every format now goes through a :class:`~adapt.layout.LayoutPlan`: the strategy
decides *what plan to build*, and :func:`adapt.render.render_plan` rasterises it.
That indirection is what lets the web editor adjust a layout by hand and export
it through the identical renderer — and it is why a saved plan (see
:mod:`adapt.store`) can simply be substituted for the algorithmic one.
"""
from __future__ import annotations

import os
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import saliency, tiles, store
from .formats import FORMATS, strategy_for
from .layout import LayoutPlan, Placement, element_id
from .psd_source import load, Source
from .render import render_plan
from .smartcrop import crop_box
from . import reflow as reflow_mod


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


# ------------------------------------------------------------ plan builders --
def plan_photo(source: Source, tw: int, th: int, importance: np.ndarray) -> LayoutPlan:
    """No elements to arrange: one saliency-chosen crop of the composite,
    scaled to fill the frame."""
    box = crop_box(importance, tw, th)
    plan = LayoutPlan(tw, th, strategy="photo",
                      base_color=tiles.base_color(source.background))
    plan.add(Placement(id="photo", kind="base_image", x=0, y=0, w=tw, h=th,
                       lock_aspect=False,
                       params={"src": "composite", "crop": [int(v) for v in box]}))
    return plan


def plan_fit(source: Source, tw: int, th: int) -> LayoutPlan:
    """Near-square target: keep every element by scaling the whole flat composite
    to fill the frame (no crop, no letterbox). The aspect change is small, so the
    distortion is minor — and because it is the *flattened* render, PSD layer
    effects (strokes, shadows) are preserved exactly."""
    plan = LayoutPlan(tw, th, strategy="fit",
                      base_color=tiles.base_color(source.background))
    plan.add(Placement(id="composite", kind="base_image", x=0, y=0, w=tw, h=th,
                       lock_aspect=False, params={"src": "composite"}))
    return plan


def plan_explode(source: Source, tw: int, th: int) -> LayoutPlan:
    """The same near-square composition broken into *separate* boxes — the
    background stretched edge-to-edge with every element at its proportional
    position on top — so each one is individually adjustable in the editor.

    Not the default for the crop formats: per-layer extraction loses PSD layer
    effects (e.g. the CTA pill's stroke), so :func:`plan_fit` is more faithful
    until the user actually wants to re-arrange.
    """
    sx, sy = tw / source.width, th / source.height
    plan = LayoutPlan(tw, th, strategy="explode",
                      base_color=tiles.base_color(source.background))
    plan.add(Placement(id="backdrop", kind="base_image", x=0, y=0, w=tw, h=th,
                       lock_aspect=False, params={"src": "background"}))
    for idx, el in enumerate(source.elements):
        l, t, r, b = el.bbox
        plan.add(Placement(id=element_id(idx, el), kind="element",
                           x=l * sx, y=t * sy, w=max(1.0, (r - l) * sx),
                           h=max(1.0, (b - t) * sy), element=idx,
                           name=el.name, role=el.role,
                           params={"mode": "stretch"}))
    return plan


def plan_for_format(source: Source, fmt, importance: np.ndarray) -> LayoutPlan:
    """The algorithmic layout plan for one target format."""
    strategy = effective_strategy(source, fmt)
    if strategy == "photo":
        return plan_photo(source, fmt.width, fmt.height, importance)
    if strategy == "crop":
        return plan_fit(source, fmt.width, fmt.height)
    return reflow_mod.plan_for(source, fmt.width, fmt.height)


# Supersampling only helps plans that *re-compose* geometry; a single full-frame
# resize gains nothing from it and would just resample twice.
_SS = {"fit": 1, "photo": 1}


def render(plan: LayoutPlan, source: Source, ss: int | None = None) -> Image.Image:
    """Rasterise a plan and apply the pipeline's final sharpening pass."""
    return _sharpen(render_plan(plan, source,
                                ss=_SS.get(plan.strategy, 2) if ss is None else ss))


def transform(source: Source, fmt, importance: np.ndarray) -> Image.Image:
    return render(plan_for_format(source, fmt, importance), source)


# ------------------------------------------------------------------ debug --
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
        sizes=None, use_saved: bool = False) -> list[str]:
    """Render every format. With ``use_saved``, any layout hand-edited in the web
    editor replaces the algorithmic one for that format."""
    source = load(input_path)
    importance = saliency.importance_map(_bgr(source.composite), source.elements)
    saved = store.load(input_path, out_dir) if use_saved else {}

    os.makedirs(out_dir, exist_ok=True)
    formats = FORMATS if sizes is None else sizes

    results, paths = [], []
    for fmt in formats:
        manual = saved.get(fmt.name)
        plan = manual or plan_for_format(source, fmt, importance)
        img = render(plan, source)
        assert img.size == (fmt.width, fmt.height), f"size mismatch for {fmt.name}"
        path = os.path.join(out_dir, f"{fmt.name}.png")
        img.save(path)
        paths.append(path)
        results.append((fmt.name, img))
        label = "manual" if manual else plan.strategy
        print(f"  [{label:11s}] {fmt.name:>8s} -> {path}")

    if debug:
        _save_debug(source, importance, out_dir)
        _montage(results, out_dir)
        print(f"  debug renders + montage written to {out_dir}")

    return paths
