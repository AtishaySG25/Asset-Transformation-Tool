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

from . import log, saliency, tiles, store
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
                       params={"src": "composite", "crop_px": [int(v) for v in box]}))
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


def plan_raw(source: Source, tw: int, th: int) -> LayoutPlan:
    """The fallback layout: no cleverness, just every layer in reading order.

    Used when a target is too extreme for the algorithm to lay out sensibly —
    the point is to give a starting arrangement that is guaranteed to contain
    every element, in order, for the user to fix by hand.
    """
    plan = LayoutPlan(tw, th, strategy="raw",
                      base_color=tiles.base_color(source.background))
    plan.add(Placement(id="backdrop", kind="base_image", x=0, y=0, w=tw, h=th,
                       lock_aspect=False, params={"src": "background"}))

    els = sorted(enumerate(source.elements), key=lambda t: (t[1].cy, t[1].cx))
    if not els:
        return plan

    down = th >= tw                       # stack vertically, else flow across
    pad = max(1, round(0.02 * min(tw, th)))
    slot = ((th - pad) / len(els) - pad) if down else ((tw - pad) / len(els) - pad)
    slot = max(1.0, slot)
    at = pad
    for idx, el in els:
        if down:
            w, h = _fit_box(el, tw - 2 * pad, slot)
            plan.add(_raw_placement(idx, el, (tw - w) / 2, at, w, h))
        else:
            w, h = _fit_box(el, slot, th - 2 * pad)
            plan.add(_raw_placement(idx, el, at, (th - h) / 2, w, h))
        at += (h if down else w) + pad
    return plan


def _fit_box(el, max_w: float, max_h: float) -> tuple[float, float]:
    s = min(max_w / max(1, el.width), max_h / max(1, el.height))
    return max(1.0, el.width * s), max(1.0, el.height * s)


def _raw_placement(idx: int, el, x, y, w, h) -> Placement:
    return Placement(id=element_id(idx, el), kind="element", x=x, y=y, w=w, h=h,
                     element=idx, name=el.name, role=el.role,
                     params={"mode": "stretch"})


# Below these, a layout exists but is not usable output. Calibrated against the
# six shipped sizes so none of them trips a warning: the tightest of them
# (468x60) legitimately runs a 4px line height and 7px elements.
MIN_ELEMENT_PX = 6
MIN_LINE_PX = 4


def review(plan: LayoutPlan, source: Source) -> list[str]:
    """Reasons this plan is not usable at this size — empty means it is fine.

    Judged on the *plan* rather than on the dimensions, so it reports what the
    layout engine actually managed to do rather than guessing in advance.
    """
    problems = []
    tiny = [p for p in plan.placements
            if p.kind == "element" and min(p.w, p.h) < MIN_ELEMENT_PX]
    if tiny:
        problems.append(
            f"{len(tiny)} element(s) shrink below {MIN_ELEMENT_PX}px: "
            f"{', '.join(sorted({p.role or p.name for p in tiny}))}")

    small_text = [p for p in plan.placements
                  if p.params.get("mode") == "reflow"
                  and p.params.get("line_h", 99) < MIN_LINE_PX]
    if small_text:
        problems.append(f"{len(small_text)} text block(s) fall below "
                        f"{MIN_LINE_PX}px line height and will be illegible")

    # The renderer clamps stray tiles into the frame, so an overflowing plan
    # does not crash — it silently stacks things on top of each other instead.
    spill = [p for p in plan.placements
             if p.x < -0.5 or p.y < -0.5
             or p.x + p.w > plan.width + 0.5 or p.y + p.h > plan.height + 0.5]
    if spill:
        problems.append(f"{len(spill)} element(s) do not fit the frame and get "
                        "pushed back inside, overlapping their neighbours")

    area = plan.width * plan.height
    used = sum(p.w * p.h for p in plan.placements if p.kind == "element")
    if area and used > 1.25 * area:
        problems.append("elements need more room than the canvas has — "
                        "they will overlap heavily")
    return problems


def plan_for_format(source: Source, fmt, importance: np.ndarray) -> LayoutPlan:
    """The algorithmic layout plan for one target format."""
    strategy = effective_strategy(source, fmt)
    with log.step(f"plan {fmt.name} ({strategy})", 1) as s:
        if strategy == "photo":
            plan = plan_photo(source, fmt.width, fmt.height, importance)
        elif strategy == "crop":
            plan = plan_fit(source, fmt.width, fmt.height)
        else:
            plan = reflow_mod.plan_for(source, fmt.width, fmt.height)
        problems = review(plan, source)
        s["note"] = (f"{len(plan.placements)} placements"
                     + (f", {len(problems)} warning(s)" if problems else ""))
    for p in problems:
        log.log(f"warning [{fmt.name}]: {p}", 2)
    return plan


# Supersampling only helps plans that *re-compose* geometry; a single full-frame
# resize gains nothing from it and would just resample twice.
_SS = {"fit": 1, "photo": 1}


def render(plan: LayoutPlan, source: Source, ss: int | None = None) -> Image.Image:
    """Rasterise a plan and apply the pipeline's final sharpening pass."""
    ss = _SS.get(plan.strategy, 2) if ss is None else ss
    with log.step(f"render {plan.name} (ss={ss})", 1):
        return _sharpen(render_plan(plan, source, ss=ss))


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
    with log.step("importance map (saliency + edges + element boxes)") as s:
        importance = saliency.importance_map(_bgr(source.composite), source.elements)
        s["note"] = f"{importance.shape[1]}x{importance.shape[0]}"
    saved = store.load(input_path, out_dir) if use_saved else {}
    if saved:
        log.log(f"manual layouts on disk: {', '.join(sorted(saved))}", 1)

    os.makedirs(out_dir, exist_ok=True)
    formats = list(FORMATS) if sizes is None else list(sizes)
    if use_saved and sizes is None:
        # Custom sizes added in the editor are part of the deliverable too.
        formats += [f for f in store.load_custom(input_path, out_dir)
                    if f.name not in {x.name for x in formats}]

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
