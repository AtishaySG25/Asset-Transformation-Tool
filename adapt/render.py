"""Plan -> pixels.

One renderer serves both the algorithmic pipeline and the web editor, so an
exported manual layout is produced by exactly the same code path as an automatic
one — and inherits the same guarantee: the result is *exactly* the target size,
with every tile clamped inside the frame (nothing clipped, nothing padded).

Rendering is supersampled by ``ss`` and downscaled once at the end, so re-wrapped
text and hard edges stay crisp instead of accumulating resample blur.
"""
from __future__ import annotations

from PIL import Image

from . import tiles
from .layout import LayoutPlan, Placement, resolve_element


def placement_tile(p: Placement, source, scale: float = 1.0) -> Image.Image | None:
    """Rasterise one placement at ``scale`` x its planned size."""
    if p.kind == "element":
        el = resolve_element(source, p)
        if el is None:
            return None
        if p.params.get("mode") == "reflow":
            # Wrap decided at plan scale, rasterised at `scale` — see textflow.
            return tiles.text_tile(el, wrap_w=round(p.w),
                                   line_h=max(1, round(p.params.get("line_h", 12))),
                                   align=p.params.get("align", "left"),
                                   ss=int(scale))
        return tiles.graphic_tile(el, round(p.w * scale), round(p.h * scale),
                                  crop=p.params.get("crop"))

    if p.kind == "photo_band":
        # A band normally shows the backdrop, but when the planner found a hero
        # element it carries that element's identity and draws it instead — the
        # product artwork reads as the centrepiece rather than as wallpaper.
        img = source.background
        if p.element is not None or p.uid:
            el = resolve_element(source, p)
            if el is not None:
                img = el.image
        return tiles.photo_band_tile(
            img, round(p.w * scale), round(p.h * scale),
            feather=float(p.params.get("feather", 0.0)),
            focus_x=float(p.params.get("focus_x", 0.5)),
            focus_y=p.params.get("focus_y"),
            fit=p.params.get("fit", "cover"),
            zoom=float(p.params.get("zoom", 1.0)))

    if p.kind == "color_bar":
        color = tuple(p.params.get("color", (0, 0, 0)))
        return Image.new("RGB", (max(1, round(p.w * scale)),
                                 max(1, round(p.h * scale))), color)

    if p.kind == "base_image":
        src = source.background if p.params.get("src") == "background" else source.composite
        crop = p.params.get("crop_px")          # source pixels, not fractions
        if crop:
            src = src.crop(tuple(int(v) for v in crop))
        w, h = max(1, round(p.w * scale)), max(1, round(p.h * scale))
        # A full-frame base image is stretched to the box unless the user has
        # reframed it by hand, which is the historical (and byte-identical) path.
        fit = p.params.get("fit", "stretch")
        if fit == "stretch":
            return src.resize((w, h), Image.LANCZOS)
        return tiles.background_tile(
            src, w, h, fit=fit, zoom=float(p.params.get("zoom", 1.0)),
            focus=(float(p.params.get("focus_x", 0.5)),
                   float(p.params.get("focus_y", 0.5))))

    return None


def _paste(base: Image.Image, tile: Image.Image, x: int, y: int):
    """Paste clamped inside the frame — a tile is never partially clipped."""
    x = max(0, min(x, base.width - tile.width))
    y = max(0, min(y, base.height - tile.height))
    if tile.mode == "RGBA":
        base.paste(tile, (x, y), tile)
    else:
        base.paste(tile, (x, y))


def render_plan(plan: LayoutPlan, source, ss: int = 2) -> Image.Image:
    """Render ``plan`` to an image of exactly ``(plan.width, plan.height)``."""
    W, H = plan.width * ss, plan.height * ss
    base = Image.new("RGB", (W, H), tuple(plan.base_color))

    for p in plan.ordered():
        if not p.visible:
            continue
        tile = p.tile if (ss == 1 and p.tile is not None) else placement_tile(p, source, ss)
        if tile is None or tile.width < 1 or tile.height < 1:
            continue
        tile = tiles.with_opacity(tile, p.opacity)
        # A re-wrapped block is only as wide as its longest line, so it is aligned
        # inside its column box rather than pinned to the left edge.
        x = round(p.x * ss)
        if p.kind == "element" and p.params.get("mode") == "reflow":
            slack = round(p.w * ss) - tile.width
            align = p.params.get("align", "left")
            x += slack // 2 if align == "center" else (slack if align == "right" else 0)
        _paste(base, tile, x, round(p.y * ss))

    return base.resize((plan.width, plan.height), Image.LANCZOS) if ss > 1 else base
