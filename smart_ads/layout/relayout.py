"""Template relayout engine for extreme aspect ratios (banners + skyscraper).

The failure mode last time was elements overflowing the canvas. This engine
makes overflow *impossible* by construction:

  * every element is placed with `_contain` — scaled to fit INSIDE a bounded box
    (preserving aspect), so it can never exceed that box on either axis;
  * `linear_stack` hands each element a slot whose sizes sum to <= the available
    space, so the placed elements always fit with non-negative gaps.

A placement is a (image, x, y) tuple in canvas coordinates.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageFilter

from ..config import Format
from ..element import ElementType, SemanticElement

Placement = Tuple[Image.Image, int, int]


# --------------------------------------------------------------------------- #
# Fit primitives
# --------------------------------------------------------------------------- #
def _contain(img: Image.Image, box_w: float, box_h: float) -> Optional[Image.Image]:
    """Scale img to fit entirely within (box_w, box_h), preserving aspect."""
    box_w, box_h = int(max(1, box_w)), int(max(1, box_h))
    iw, ih = img.size
    if iw <= 0 or ih <= 0:
        return None
    scale = min(box_w / iw, box_h / ih)
    nw, nh = max(1, int(round(iw * scale))), max(1, int(round(ih * scale)))
    return img.resize((nw, nh), Image.LANCZOS)


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """Scale to fully cover (w, h) then center-crop to exactly (w, h)."""
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    nw, nh = max(w, int(round(iw * scale))), max(h, int(round(ih * scale)))
    r = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return r.crop((left, top, left + w, top + h))


def _focus_logo(img: Image.Image) -> Image.Image:
    """Isolate a logo mark that sits on a solid band.

    Some PSDs bundle the logo into a full-width ribbon (logo + colored bar). Naive
    contain-fit then shrinks the real mark to a speck. When the artwork is an
    extreme band (aspect > 5), crop to the region whose pixels deviate from the
    uniform band color -- i.e. the actual mark -- so it can be placed at a usable
    size.
    """
    iw, ih = img.size
    if ih == 0 or max(iw / ih, ih / iw) < 5:
        return img
    arr = np.array(img.convert("RGBA"))
    rgb, alpha = arr[:, :, :3].astype(np.int16), arr[:, :, 3]
    opaque = alpha > 12
    if opaque.sum() < 20:
        return img
    frame = np.zeros(alpha.shape, bool)
    frame[:3, :] = frame[-3:, :] = frame[:, :3] = frame[:, -3:] = True
    ref_src = rgb[opaque & frame]
    ref = np.median(ref_src if len(ref_src) else rgb[opaque], axis=0)
    content = opaque & (np.abs(rgb - ref).sum(axis=2) > 60)
    if content.sum() < 20:
        return img
    ys, xs = np.where(content)
    px, py = int((xs.max() - xs.min()) * 0.08), int((ys.max() - ys.min()) * 0.15)
    box = (max(0, xs.min() - px), max(0, ys.min() - py),
           min(iw, xs.max() + 1 + px), min(ih, ys.max() + 1 + py))
    crop = img.crop(box)
    return crop if crop.width * crop.height < 0.9 * iw * ih else img


def linear_stack(items: List[Tuple[Image.Image, float]], main: int, cross: int,
                 pad: int, axis: str) -> List[Placement]:
    """Lay images along `axis` inside a (main x cross) region at origin (0, 0).

    axis='h': main is width, cross is height. axis='v': main is height, cross is
    width. Space along the main axis is divided among items by weight; each item
    is contain-fit into its slot, guaranteeing the whole row/column fits. Items
    are centered on the cross axis and spaced evenly (space-around) on the main.
    Returns placements relative to the region origin.
    """
    if not items:
        return []
    n = len(items)
    total_weight = sum(max(w, 1e-6) for _, w in items)
    available = main - pad * (n + 1)
    if available <= 0:
        available, pad = main, 0

    fitted: List[Image.Image] = []
    for img, weight in items:
        slot = available * (max(weight, 1e-6) / total_weight)
        box = (slot, cross - 2 * pad) if axis == "h" else (cross - 2 * pad, slot)
        f = _contain(img, box[0], box[1])
        if f is not None:
            fitted.append(f)
    if not fitted:
        return []

    used = sum((f.width if axis == "h" else f.height) for f in fitted)
    gap = max(0, (main - used) / (len(fitted) + 1))

    placements: List[Placement] = []
    cursor = gap
    for f in fitted:
        if axis == "h":
            x = cursor
            y = (cross - f.height) / 2
            cursor += f.width + gap
        else:
            y = cursor
            x = (cross - f.width) / 2
            cursor += f.height + gap
        placements.append((f, int(round(x)), int(round(y))))
    return placements


# --------------------------------------------------------------------------- #
# Element selection
# --------------------------------------------------------------------------- #
def _largest(elements, etype) -> Optional[SemanticElement]:
    cands = [e for e in elements if e.type == etype]
    return max(cands, key=lambda e: e.area) if cands else None


def select_roles(elements: List[SemanticElement]) -> Dict[str, SemanticElement]:
    """Pick one element per layout role from the classified set."""
    roles: Dict[str, SemanticElement] = {}
    logo = _largest(elements, ElementType.LOGO)
    cta = _largest(elements, ElementType.CTA)
    headlines = sorted([e for e in elements if e.type == ElementType.HEADLINE],
                       key=lambda e: e.area, reverse=True)
    subtext = _largest(elements, ElementType.SUBTEXT)
    visual = _largest(elements, ElementType.PRODUCT) or _largest(elements, ElementType.GRAPH)

    if logo:
        roles["logo"] = logo
    if cta:
        roles["cta"] = cta
    if headlines:
        roles["headline"] = headlines[0]
        # A second headline (e.g. product/scheme name) becomes secondary copy.
        if len(headlines) > 1 and "subtext" not in roles:
            subtext = subtext or headlines[1]
    if subtext:
        roles["subtext"] = subtext
    if visual:
        roles["visual"] = visual
    return roles


def make_background(size: Tuple[int, int], elements, fallback_rgb) -> Image.Image:
    """A soft, legible backdrop for relayout.

    Re-laid-out text carries colors tuned to the ORIGINAL background, so we keep
    the brand's background tone but blur away its detail (and the text baked into
    it) and lighten it, restoring contrast for the text we place on top.
    """
    w, h = size
    bg = _largest(elements, ElementType.BACKGROUND)
    if bg is not None:
        base = _cover(bg.image.convert("RGB"), w, h)
        base = base.filter(ImageFilter.GaussianBlur(radius=max(4, min(w, h) // 12)))
        base = Image.blend(base, Image.new("RGB", (w, h), (255, 255, 255)), 0.55)
    else:
        base = Image.new("RGB", (w, h), tuple(fallback_rgb))
    return base.convert("RGBA")


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
def _role_image(role: str, el: SemanticElement) -> Image.Image:
    """The artwork to place for a role (logo marks get band-focused first)."""
    return _focus_logo(el.image) if role == "logo" else el.image


def _skyscraper(fmt: Format, roles) -> List[Placement]:
    """Tall format: logo pinned top, CTA pinned bottom, key content centered
    between them. Pinning the anchors reads as intentional (vs. content drifting
    on even spacing) and keeps the brand + action always visible."""
    w, h = fmt.width, fmt.height
    pad = max(4, int(w * 0.05))
    inner_w = w - 2 * pad
    placements: List[Placement] = []
    y_top, y_bot = pad, h - pad

    if "logo" in roles:
        f = _contain(_focus_logo(roles["logo"].image), inner_w, int(h * 0.12))
        if f:
            placements.append((f, (w - f.width) // 2, y_top))
            y_top += f.height + pad

    if "cta" in roles:
        f = _contain(roles["cta"].image, inner_w, int(h * 0.14))
        if f:
            placements.append((f, (w - f.width) // 2, y_bot - f.height))
            y_bot -= f.height + pad

    # Middle band: headline, a readable visual, then supporting copy.
    mid: List[Tuple[Image.Image, float]] = []
    if "headline" in roles:
        mid.append((roles["headline"].image, 1.6))
    visual = roles.get("visual")
    # A wide chart is illegible in a narrow column; only keep product-style art
    # or any visual when the column is wide enough to show it.
    if visual and (visual.type == ElementType.PRODUCT or w >= 220):
        mid.append((visual.image, 2.2))
    if "subtext" in roles:
        mid.append((roles["subtext"].image, 1.0))

    region_h = y_bot - y_top
    if region_h > 0 and mid:
        for f, xr, yr in linear_stack(mid, main=region_h, cross=w, pad=pad, axis="v"):
            placements.append((f, xr, y_top + yr))
    return placements


def _wide_banner(fmt: Format, roles) -> List[Placement]:
    """Wide format: logo | headline+subtext | cta, arranged left to right."""
    w, h = fmt.width, fmt.height
    pad = max(3, int(h * 0.14))
    inner_h = h - 2 * pad
    thin = h < 70 or w < 500

    placements: List[Placement] = []

    logo = roles.get("logo")
    cta = roles.get("cta")
    headline = roles.get("headline")
    subtext = roles.get("subtext")

    logo_budget = int(w * 0.18) if logo else 0
    cta_budget = int(w * 0.22) if cta else 0

    # Logo, hard-left, vertically centered.
    x_after_logo = pad
    if logo:
        f = _contain(_focus_logo(logo.image), logo_budget, inner_h)
        if f:
            placements.append((f, pad, (h - f.height) // 2))
            x_after_logo = pad + logo_budget + pad

    # CTA, hard-right, vertically centered.
    cta_fit = _contain(cta.image, cta_budget, int(inner_h * 0.95)) if cta else None
    right_limit = w - pad - (cta_fit.width + pad if cta_fit else 0)

    # Center column: headline over subtext (subtext dropped on thin banners).
    center_left = x_after_logo
    center_w = max(1, right_limit - center_left)
    center_items = []
    if headline:
        center_items.append((headline.image, 2.0))
    if subtext and not thin:
        center_items.append((subtext.image, 1.0))
    for f, xr, yr in linear_stack(center_items, main=inner_h, cross=center_w,
                                  pad=max(1, pad // 2), axis="v"):
        placements.append((f, center_left + xr, pad + yr))

    if cta_fit:
        placements.append((cta_fit, w - pad - cta_fit.width, (h - cta_fit.height) // 2))

    return placements


def relayout(fmt: Format, elements: List[SemanticElement]) -> List[Placement]:
    """Produce foreground placements for a relayout format."""
    roles = select_roles(elements)
    if fmt.height > fmt.width:
        return _skyscraper(fmt, roles)
    return _wide_banner(fmt, roles)
