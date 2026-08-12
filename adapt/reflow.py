"""Content-preserving reflow for extreme aspect ratios.

Instead of cropping (which loses content) or dropping elements, we *re-flow* the
whole composition: every discrete element is kept, text is re-wrapped to the new
width (see :mod:`adapt.textflow`), and the elements are re-stacked following the
master's own top-to-bottom reading order — so the layout logic is driven by the
source geometry, not by hardcoded element names, and generalises to other PSDs.

* portrait targets  -> vertical stack (elements top->bottom)
* landscape targets -> horizontal flow (reading order mapped left->right)

The functions here produce a :class:`~adapt.layout.LayoutPlan` in *output pixel*
coordinates rather than an image. :func:`adapt.render.render_plan` turns a plan
into the final PNG, and the web editor edits the plan in between — which is why
the layout maths lives here and the rasterisation lives in :mod:`adapt.tiles`.
"""
from __future__ import annotations

from PIL import Image

from . import tiles
from .layout import LayoutPlan, Placement, element_id as _el_id
from .render import render_plan
from .tiles import is_reflow_text          # re-exported: used by pipeline/tests


def _indexed(source):
    return list(enumerate(source.elements))


# -------------------------------------------------------------- portrait --
def _tall_specs(source, cw: int, th: int, k: float):
    """Placement specs (with measured tiles) for a vertical stack.

    ``k`` is a global size multiplier used to grow the elements so the stack
    fills the canvas instead of leaving gaps.
    """
    out = []
    for idx, el in _indexed(source):
        if is_reflow_text(el):
            line_h = max(9, round(th * 0.045 * (el.priority / 70) * k))
            tile = tiles.text_tile(el, cw, line_h, align="center")
            params = {"mode": "reflow", "line_h": line_h, "align": "center"}
        else:
            tile = tiles.fit(el.image, cw, round(0.28 * th * k))
            params = {"mode": "stretch"}
        out.append({"idx": idx, "el": el, "tile": tile, "params": params,
                    "key": el.cy, "bleed": False})
    return out


def plan_tall(source, tw: int, th: int) -> LayoutPlan:
    """Vertical stack: every element in reading order, plus a full-bleed band of
    the background imagery at the document position where the imagery lives."""
    margin = max(3, round(0.03 * tw))
    cw = tw - 2 * margin
    gap = max(2, round(0.008 * th))
    avail = th - 2 * margin

    plan = LayoutPlan(tw, th, strategy="reflow-tall",
                      base_color=tiles.base_color(source.background))

    band_h = max(24, round(0.20 * th))
    band_tile = tiles.photo_band_tile(source.background, tw, band_h,
                                      feather=0.0, focus_x=0.5, focus_y=0.5)
    band = {"idx": None, "el": None, "tile": band_tile, "key": tiles.saliency_row(
        source.background) * source.height, "bleed": True,
        "params": {"feather": 0.0, "focus_x": 0.5, "focus_y": 0.5}}

    def assemble(k):
        items = _tall_specs(source, cw, th, k) + [band]
        items.sort(key=lambda d: d["key"])
        return items

    def stack_h(items):
        return sum(d["tile"].height for d in items) + gap * (len(items) - 1)

    # Grow the elements so the stack fills the height, then back off until it fits.
    n = max(1, len(source.elements))
    tot0 = sum(d["tile"].height for d in _tall_specs(source, cw, th, 1.0)) + gap * n
    k = min(1.7, max(0.6, (avail * 0.97 - band_h) / max(1, tot0 - band_h)))
    items = assemble(k)
    for _ in range(6):
        total = stack_h(items)
        if total <= avail:
            break
        # Only the elements can shrink — the band is a fixed full-bleed strip.
        k *= max(0.5, (avail - band_h) / max(1.0, total - band_h)) * 0.99
        items = assemble(k)

    total = stack_h(items)
    extra = max(0, avail - total) / (len(items) + 1)
    y = margin + extra
    for d in items:
        tile = d["tile"]
        x = 0 if d["bleed"] else (tw - tile.width) / 2
        if d["bleed"]:
            plan.add(Placement(id="photo-band", kind="photo_band", x=x, y=y,
                               w=tile.width, h=tile.height, lock_aspect=False,
                               params=d["params"], tile=tile))
        else:
            el = d["el"]
            plan.add(Placement(id=_el_id(d["idx"], el), kind="element", x=x, y=y,
                               w=tile.width, h=tile.height, element=d["idx"],
                               uid=el.uid, name=el.name, role=el.role,
                               lock_aspect=d["params"]["mode"] == "stretch",
                               params=d["params"], tile=tile))
        y += tile.height + gap + extra
    return plan


# ------------------------------------------------------------- landscape --
def _footer_text(source):
    """A very wide, low-priority text line sitting at the bottom of the master —
    e.g. the market-risk disclaimer. Detected by shape+position, not by name, so
    it generalises. Laid out as a full-width bottom strip."""
    for idx, el in _indexed(source):
        if (is_reflow_text(el) and el.cy > 0.80 * source.height
                and el.width / max(1, el.height) > 8):
            return idx, el
    return None, None


def _footer_bar(source, skip=None):
    """A wide logo lock-up sitting at the bottom of the master — rendered as a
    full-width footer band (matches the skyscraper), not a small corner element.
    Detected by shape+position, so it generalises.

    ``skip`` is the element already taken as the footer *text*: both tests are
    satisfied by one wide bottom layer classified as a logo, and an element used
    twice is drawn twice, under one id, which the editor then cannot tell apart.
    """
    for idx, el in _indexed(source):
        if (idx != skip and el.role == "logo" and el.width / max(1, el.height) > 4
                and el.cy > 0.70 * source.height):
            return idx, el
    return None, None


def _text_spec(el, slot_h: int, wbudget: int, align: str = "left"):
    """Spec + measured tile for a re-wrapped text block that fits ``slot_h``.

    An over-tall block is re-wrapped smaller (not squashed): line height and
    column width shrink together, which keeps the line breaks and scales the
    block down. Doing it this way means the block is fully described by
    ``(width, line_h)`` — so the renderer can reproduce it from the placement
    box alone, and dragging that box in the editor stays truthful.
    """
    line_h = max(7, round(slot_h * 0.52))
    tile = tiles.text_tile(el, wbudget, line_h, align=align)
    for _ in range(3):
        if tile.height <= slot_h:
            break
        f = slot_h / tile.height
        line_h = max(3, round(line_h * f))
        wbudget = max(8, round(wbudget * f))
        tile = tiles.text_tile(el, wbudget, line_h, align=align)
    return {"tile": tile,
            "params": {"mode": "reflow", "line_h": line_h, "align": align}}


def _graphic_spec(el, tw: int, max_h: int):
    """Fit a graphic to a height cap; very wide elements (a logo lock-up / footer
    bar) also get a narrow width cap so they read as a compact bar, not a slab."""
    aspect = el.width / max(1, el.height)
    max_w = (0.18 if aspect > 6 else 0.34) * tw
    return {"tile": tiles.fit(el.image, max_w, max_h), "params": {"mode": "stretch"}}


def _scale_spec(spec, el, f: float):
    """Re-render a spec ``f`` x smaller — text is genuinely re-wrapped at the
    smaller column width rather than squashed."""
    if f >= 1.0:
        return spec
    p = dict(spec["params"])
    t = spec["tile"]
    if p.get("mode") == "reflow":
        p["line_h"] = max(3, round(p["line_h"] * f))
        tile = tiles.text_tile(el, max(8, round(t.width * f)), p["line_h"],
                               align=p.get("align", "left"))
    else:
        tile = t.resize((max(1, round(t.width * f)), max(1, round(t.height * f))),
                        Image.LANCZOS)
    return {"tile": tile, "params": p}


def _pack_columns(indexed):
    """Reading-order columns. A secondary text line (subheadline) tucks under the
    preceding, higher-priority text line (headline) to form a title block; every
    other element gets its own column."""
    cols: list[list] = []
    for idx, el in sorted(indexed, key=lambda t: t[1].cy):
        prev = cols[-1] if cols else None
        if (is_reflow_text(el) and prev and len(prev) == 1
                and is_reflow_text(prev[0][1]) and prev[0][1].priority > el.priority):
            prev.append((idx, el))               # title block: headline + subheadline
        else:
            cols.append([(idx, el)])
    return cols


def plan_wide(source, tw: int, th: int) -> LayoutPlan:
    """2-D column packing for leaderboards/banners: a light base, the background
    imagery as a soft-edged centre band, reading-order columns across the width,
    and a full-width footer bar + disclaimer strip along the bottom."""
    mx = max(3, round(0.008 * tw))
    gap = max(4, round(0.012 * tw))
    vgap = max(2, round(0.05 * th))

    plan = LayoutPlan(tw, th, strategy="reflow-wide",
                      base_color=tiles.base_color(source.background))

    # 1. Background imagery as a soft-edged central band, so text sits on clean
    #    space while the photo still "scales across".
    band_w = round(0.60 * tw)
    focus_y = tiles.saliency_row(source.background)
    plan.add(Placement(id="photo-band", kind="photo_band", x=(tw - band_w) // 2,
                       y=0, w=band_w, h=th, lock_aspect=False,
                       params={"feather": 0.22, "focus_x": 0.5, "focus_y": focus_y}))

    # 2. Reserve the bottom for the footer: a full-width logo bar plus the
    #    full-width disclaimer strip beneath it.
    f_idx, footer = _footer_text(source)
    strip_h = max(10, round(0.18 * th)) if footer is not None else 0
    b_idx, bar_el = _footer_bar(source, skip=f_idx)
    bar_h = max(10, round(0.16 * th)) if bar_el is not None else 0
    region_h = th - strip_h - bar_h
    ch = round(0.86 * region_h)                  # column height (breathing room)

    body = [(i, e) for i, e in _indexed(source) if i not in (f_idx, b_idx)]

    # 3. Render each column. The headline is rendered dominant (big title block).
    def col_specs(col):
        if len(col) == 2:                        # headline + subheadline title block
            (_, head), (_, sub) = col
            return [_text_spec(head, round(ch * 0.64), round(0.42 * tw)),
                    _text_spec(sub, round(ch * 0.30), round(0.40 * tw))]
        _, el = col[0]
        if is_reflow_text(el):
            return [_text_spec(el, ch, round(0.30 * tw))]
        return [_graphic_spec(el, tw, ch)]

    cols = _pack_columns(body)
    rendered = [col_specs(c) for c in cols]

    def col_w(specs):
        return max((s["tile"].width for s in specs), default=0)

    avail = tw - 2 * mx
    n_gap = gap * (len(rendered) - 1)
    total = sum(col_w(s) for s in rendered)
    if total + n_gap > avail:                    # overflow: shrink columns to fit
        f = max(0.3, (avail - n_gap) / max(1, total))
        rendered = [[_scale_spec(s, col[i][1], f) for i, s in enumerate(specs)]
                    for specs, col in zip(rendered, cols)]
        total = sum(col_w(s) for s in rendered)

    # 4. Spread columns across the full width; the light base / photo show through.
    slack = max(0, avail - total - n_gap)
    extra = slack / (len(rendered) + 1)
    x = mx + extra
    for specs, col in zip(rendered, cols):
        w = col_w(specs)
        colh = sum(s["tile"].height for s in specs) + vgap * (len(specs) - 1)
        y = max(0, (region_h - colh) / 2)        # vertically centred
        for (idx, el), spec in zip(col, specs):
            tile = spec["tile"]
            plan.add(Placement(id=_el_id(idx, el), kind="element",
                               x=x + (w - tile.width) / 2, y=y,
                               w=tile.width, h=tile.height, element=idx,
                               uid=el.uid, name=el.name, role=el.role,
                               lock_aspect=spec["params"]["mode"] == "stretch",
                               params=spec["params"], tile=tile))
            y += tile.height + vgap
        x += w + gap + extra

    # 5. Full-width footer logo bar (colour band spanning the whole width, with
    #    the logo lock-up on its right — the bar "expands throughout" the banner).
    if bar_el is not None:
        by = th - strip_h - bar_h
        plan.add(Placement(id="footer-bar", kind="color_bar", x=0, y=by, w=tw,
                           h=bar_h, lock_aspect=False,
                           params={"color": list(tiles.dominant_color(bar_el.image))}))
        logo = tiles.fit(bar_el.image, 0.5 * tw, bar_h)
        plan.add(Placement(id=_el_id(b_idx, bar_el), kind="element",
                           x=tw - mx - logo.width, y=by + (bar_h - logo.height) / 2,
                           w=logo.width, h=logo.height, element=b_idx,
                           uid=bar_el.uid, name=bar_el.name, role=bar_el.role,
                           params={"mode": "stretch"}, tile=logo))

    # 6. Disclaimer on a solid light strip along the very bottom.
    if footer is not None:
        plan.add(Placement(id="footer-strip", kind="color_bar", x=0, y=th - strip_h,
                           w=tw, h=strip_h, lock_aspect=False,
                           params={"color": list(plan.base_color)}))
        spec = _text_spec(footer, strip_h - 2, tw - 2 * mx)
        tile = spec["tile"]
        plan.add(Placement(id=_el_id(f_idx, footer), kind="element",
                           x=(tw - tile.width) / 2,
                           y=th - strip_h + (strip_h - tile.height) / 2,
                           w=tile.width, h=tile.height, element=f_idx,
                           uid=footer.uid, name=footer.name,
                           role=footer.role, lock_aspect=False,
                           params=spec["params"], tile=tile))
    return plan


# ----------------------------------------------------------------- entry --
def plan_for(source, tw: int, th: int) -> LayoutPlan:
    """The algorithmic plan for an extreme-ratio target."""
    return (plan_wide if tw >= th else plan_tall)(source, tw, th)


def reflow(source, tw: int, th: int, ss: int = 2) -> Image.Image:
    """Compose the banner, supersampled at ``ss``x and downscaled once, so small
    re-flowed text and edges stay crisp instead of accumulating resample blur."""
    return render_plan(plan_for(source, tw, th), source, ss=ss)
