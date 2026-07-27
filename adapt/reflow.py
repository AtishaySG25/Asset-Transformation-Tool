"""Content-preserving reflow for extreme aspect ratios.

Instead of cropping (which loses content) or dropping elements, we *re-flow* the
whole composition: every discrete element is kept, text is re-wrapped to the new
width (see :mod:`adapt.textflow`), and the elements are re-stacked following the
master's own top-to-bottom reading order — so the layout logic is driven by the
source geometry, not by hardcoded element names, and generalises to other PSDs.

* portrait targets  -> vertical stack (elements top->bottom)
* landscape targets -> horizontal flow (reading order mapped left->right)

A "hero" tile is derived from the most salient region of the background so the
imagery survives too. The canvas base is the sampled background colour, so the
result fills the exact dimensions with no padding.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from . import saliency, textflow
from .background import fill_background


# ---------------------------------------------------------------- background --
def _base_color(bg_rgb: Image.Image) -> tuple[int, int, int]:
    """Sample the dominant (light) colour from the top band of the background."""
    a = np.array(bg_rgb.convert("RGB"))
    top = a[: max(1, a.shape[0] // 20)].reshape(-1, 3)
    med = np.median(top, axis=0)
    return int(med[0]), int(med[1]), int(med[2])


_SAL_CACHE: dict[int, np.ndarray] = {}


def _bg_saliency(bg_rgb: Image.Image) -> np.ndarray:
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


def _saliency_row(bg_rgb: Image.Image) -> float:
    """Saliency-weighted mean row (normalised 0..1) — where the imagery lives."""
    sal = _bg_saliency(bg_rgb)
    rows = sal.sum(axis=1)
    ys = np.arange(sal.shape[0])
    return float((ys * rows).sum() / (rows.sum() + 1e-8)) / sal.shape[0]


# ------------------------------------------------------------------ helpers --
def _fit(img: Image.Image, max_w: float, max_h: float) -> Image.Image:
    w, h = img.size
    s = min(max_w / w, max_h / h)
    return img.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)


def _fit_h(img: Image.Image, h: int) -> Image.Image:
    w0, h0 = img.size
    s = h / h0
    return img.resize((max(1, round(w0 * s)), max(1, h)), Image.LANCZOS)


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


def _paste(base: Image.Image, tile: Image.Image, x: int, y: int):
    x = max(0, min(x, base.width - tile.width))
    y = max(0, min(y, base.height - tile.height))
    if tile.mode == "RGBA":
        base.paste(tile, (x, y), tile)
    else:
        base.paste(tile, (x, y))


# -------------------------------------------------------------- portrait --
def _tiles_tall(source, cw, th, k):
    """Render each element to a tile; ``k`` is a global size multiplier used to
    grow the elements so the stack fills the canvas instead of leaving gaps."""
    out = []
    for el in source.elements:
        if is_reflow_text(el):
            line_h = max(9, round(th * 0.045 * (el.priority / 70) * k))
            img = textflow.reflow_to_width(el.image, cw, line_h, align="center")
        else:
            img = _fit(el.image, cw, round(0.28 * th * k))
        out.append({"img": img, "key": el.cy, "bleed": False})
    return out


def layout_tall(source, tw: int, th: int) -> Image.Image:
    margin = max(3, round(0.03 * tw))
    cw = tw - 2 * margin
    base = Image.new("RGB", (tw, th), _base_color(source.background))

    # Full-bleed background band (scales edge-to-edge, centred) placed at the
    # document position where the imagery lives in the master.
    band_h = max(24, round(0.20 * th))
    band = fill_background(source.background, tw, band_h, focus=(0.5, 0.5))
    band_y = _saliency_row(source.background) * source.height

    gap = max(2, round(0.008 * th))
    avail = th - 2 * margin

    def assemble(k):
        items = _tiles_tall(source, cw, th, k)
        items.append({"img": band, "key": band_y, "bleed": True})
        items.sort(key=lambda d: d["key"])
        return items

    # Pick a size multiplier so the stack (minus the fixed band) fills the height.
    tot0 = sum(d["img"].height for d in assemble(1.0)) + gap * len(source.elements)
    k = min(1.7, max(0.6, (avail * 0.97 - band_h) / max(1, tot0 - band_h)))
    items = assemble(k)

    total = sum(d["img"].height for d in items) + gap * (len(items) - 1)
    if total > avail:
        f = avail / total
        for d in items:
            if not d["bleed"]:
                im = d["img"]
                d["img"] = im.resize((max(1, round(im.width * f)),
                                      max(1, round(im.height * f))), Image.LANCZOS)
        total = sum(d["img"].height for d in items) + gap * (len(items) - 1)

    extra = max(0, avail - total) / (len(items) + 1)
    y = margin + extra
    for d in items:
        im = d["img"]
        _paste(base, im, 0 if d["bleed"] else (tw - im.width) // 2, int(y))
        y += im.height + gap + extra
    return base


# -------------------------------------------------------------- landscape --
def _footer_text(source):
    """A very wide, low-priority text line sitting at the bottom of the master —
    e.g. the market-risk disclaimer. Detected by shape+position, not by name, so
    it generalises. Returned to be laid as a full-width bottom strip."""
    for el in source.elements:
        if (is_reflow_text(el) and el.cy > 0.80 * source.height
                and el.width / max(1, el.height) > 8):
            return el
    return None


def _render_text(el, slot_h, wbudget):
    line_h = max(7, round(slot_h * 0.52))
    blk = textflow.reflow_to_width(el.image, max(8, wbudget), line_h, align="left")
    return blk if blk.height <= slot_h else _fit_h(blk, slot_h)


def _render_graphic(el, tw, max_h):
    """Fit a graphic to a height cap; very wide elements (a logo lock-up / footer
    bar) also get a narrow width cap so they read as a compact bar, not a slab."""
    aspect = el.width / max(1, el.height)
    max_w = (0.18 if aspect > 6 else 0.34) * tw
    return _fit(el.image, max_w, max_h)


def _dominant_color(rgba):
    """Median colour of an element's opaque pixels (e.g. a logo bar's fill)."""
    arr = np.array(rgba.convert("RGBA"))
    op = arr[arr[:, :, 3] > 200][:, :3]
    if len(op) == 0:
        return (128, 0, 64)
    m = np.median(op, axis=0)
    return int(m[0]), int(m[1]), int(m[2])


def _footer_bar(elements, source_h):
    """A wide logo lock-up sitting at the bottom of the master — rendered as a
    full-width footer band (matches the skyscraper), not a small corner element.
    Detected by shape+position, so it generalises."""
    for el in elements:
        if (el.role == "logo" and el.width / max(1, el.height) > 4
                and el.cy > 0.70 * source_h):
            return el
    return None


def _photo_band(bg, w, h, feather=0.22):
    """The background imagery as a full-height band with soft left/right edges so
    it blends into the light base rather than reading as a pasted-in tile."""
    panel = fill_background(bg, max(1, w), max(1, h),
                            focus=(0.5, _saliency_row(bg))).convert("RGBA")
    arr = np.array(panel)
    fw = max(1, int(feather * w))
    ramp = np.ones(arr.shape[1], np.float32)
    ramp[:fw] = np.linspace(0.0, 1.0, fw)
    ramp[-fw:] = np.linspace(1.0, 0.0, fw)
    arr[:, :, 3] = (arr[:, :, 3].astype(np.float32) * ramp[None, :]).astype(np.uint8)
    return Image.fromarray(arr)


def _pack_columns(elements):
    """Reading-order columns. A secondary text line (subheadline) tucks under the
    preceding, higher-priority text line (headline) to form a title block; every
    other element gets its own column. Returns list of columns (list of elements).
    """
    cols: list[list] = []
    for el in sorted(elements, key=lambda e: e.cy):
        prev = cols[-1] if cols else None
        if (is_reflow_text(el) and prev and len(prev) == 1
                and is_reflow_text(prev[0]) and prev[0].priority > el.priority):
            prev.append(el)                      # title block: headline + subheadline
        else:
            cols.append([el])
    return cols


def layout_wide(source, tw: int, th: int) -> Image.Image:
    mx = max(3, round(0.008 * tw))
    gap = max(4, round(0.012 * tw))
    vgap = max(2, round(0.05 * th))

    # 1. Light base with the background imagery as a soft-edged central band, so
    #    text sits on clean space while the photo still "scales across".
    base = Image.new("RGB", (tw, th), _base_color(source.background))
    band_w = round(0.60 * tw)
    band = _photo_band(source.background, band_w, th)
    base.paste(band, ((tw - band_w) // 2, 0), band)

    # 2. Reserve the bottom for the footer: a full-width logo bar plus the
    #    full-width disclaimer strip beneath it.
    footer = _footer_text(source)
    strip_h = max(10, round(0.18 * th)) if footer is not None else 0
    bar_el = _footer_bar(source.elements, source.height)
    bar_h = max(10, round(0.16 * th)) if bar_el is not None else 0
    region_h = th - strip_h - bar_h
    ch = round(0.86 * region_h)                  # column height (breathing room)

    elements = [e for e in source.elements if e is not footer and e is not bar_el]

    # 3. Render each column. The headline is rendered dominant (big title block).
    def render_col(col):
        """Render one column to a top-to-bottom list of tiles."""
        if len(col) == 2:                        # headline + subheadline title block
            head, sub = col
            return [_render_text(head, round(ch * 0.64), round(0.42 * tw)),
                    _render_text(sub, round(ch * 0.30), round(0.40 * tw))]
        el = col[0]
        if is_reflow_text(el):
            return [_render_text(el, ch, round(0.30 * tw))]
        return [_render_graphic(el, tw, ch)]

    rendered = [render_col(c) for c in _pack_columns(elements)]

    def col_w(tiles):
        return max((t.width for t in tiles), default=0)

    avail = tw - 2 * mx
    n_gap = gap * (len(rendered) - 1)
    total = sum(col_w(t) for t in rendered)
    if total + n_gap > avail:                    # overflow: shrink columns to fit
        f = max(0.3, (avail - n_gap) / max(1, total))
        rendered = [[im.resize((max(1, round(im.width * f)), max(1, round(im.height * f))),
                               Image.LANCZOS) for im in t] for t in rendered]
        total = sum(col_w(t) for t in rendered)

    # 4. Spread columns across the full width; the light base / photo show through.
    slack = max(0, avail - total - n_gap)
    extra = slack / (len(rendered) + 1)
    x = mx + extra
    for tiles in rendered:
        w = col_w(tiles)
        colh = sum(im.height for im in tiles) + vgap * (len(tiles) - 1)
        y = max(0, (region_h - colh) // 2)       # vertically centred
        for im in tiles:
            _paste(base, im, int(x + (w - im.width) / 2), int(y))
            y += im.height + vgap
        x += w + gap + extra

    # 5. Full-width footer logo bar (colour band spanning the whole width, with
    #    the logo lock-up on its right — the bar "expands throughout" the banner).
    if bar_el is not None:
        by = th - strip_h - bar_h
        base.paste(Image.new("RGB", (tw, bar_h), _dominant_color(bar_el.image)), (0, by))
        logo = _fit(bar_el.image, 0.5 * tw, bar_h)
        _paste(base, logo, tw - mx - logo.width, by + (bar_h - logo.height) // 2)

    # 6. Disclaimer on a solid light strip along the very bottom.
    if footer is not None:
        base.paste(Image.new("RGB", (tw, strip_h), _base_color(source.background)),
                   (0, th - strip_h))
        strip = _render_text(footer, strip_h - 2, tw - 2 * mx)
        _paste(base, strip, (tw - strip.width) // 2,
               th - strip_h + (strip_h - strip.height) // 2)
    return base


def reflow(source, tw: int, th: int, ss: int = 2) -> Image.Image:
    """Compose the banner, supersampled at ``ss``x and downscaled once, so small
    re-flowed text and edges stay crisp instead of accumulating resample blur."""
    layout = layout_wide if tw >= th else layout_tall
    img = layout(source, tw * ss, th * ss)
    return img.resize((tw, th), Image.LANCZOS) if ss > 1 else img
