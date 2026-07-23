"""Semantic classification of layers.

Hybrid strategy, most reliable signal first:
  1. Layer NAME keywords  (designers name their groups: "cta", "headline", ...)
  2. Layer KIND           (psd-tools: type -> text, smartobject -> logo-ish)
  3. GEOMETRY / position  (full-canvas -> background, wide+short -> text, ...)

`name_hint` is the cheap name-only pass. The loader uses it to decide whether a
group is a cohesive unit or a band it should split (see loader.py). `classify`
is the full pass that also uses kind + geometry and is applied to the final
extracted elements.
"""
from __future__ import annotations

import re
from typing import Optional

import cv2
import numpy as np

from .element import ElementType, SemanticElement

# Keyword tables. Order of the checks below matters: the first hit wins, so more
# specific / higher-precedence roles are tested before generic ones. Notably
# SUBTEXT is tested before HEADLINE because "subheadline" contains "headline".
_KEYWORDS = [
    (ElementType.BACKGROUND, ("background", "backdrop", "bg", "base layer")),
    (ElementType.DISCLAIMER, ("disclaimer", "legal", "footer", "terms", "t&c", "tnc",
                              "subject to market", "market risk", "read all")),
    (ElementType.DECORATIVE, ("rating", "review", "star", "app-store", "play-store",
                              "play store", "badge", "ornament")),
    (ElementType.CTA,        ("cta", "invest now", "button", "btn", "apply", "know more",
                              "buy now", "shop", "sign up", "register", "subscribe",
                              "get started", "download", "click")),
    (ElementType.GRAPH,      ("graph", "chart", "riskometer", "meter", "gauge", "plot",
                              "diagram", "performance")),
    (ElementType.LOGO,       ("logo", "brand", "wordmark")),
    (ElementType.SUBTEXT,    ("subheadline", "subhead", "subtitle", "subtext", "tagline",
                              "description")),
    (ElementType.HEADLINE,   ("headline", "scheme", "title", "heading", "hero", "header")),
]


def _matches(name: str, keyword: str) -> bool:
    """Whole-word match so short keys (cta, bg, btn) don't fire inside other
    words (e.g. 'cta' must not match inside 're[cta]ngle')."""
    return re.search(r"\b" + re.escape(keyword) + r"\b", name) is not None


def name_hint(name: str) -> Optional[ElementType]:
    """Name-only classification. Returns None if no keyword matches."""
    n = (name or "").lower()
    for etype, keys in _KEYWORDS:
        if any(_matches(n, k) for k in keys):
            return etype
    return None


def _classify_by_geometry(el: SemanticElement, sw: int, sh: int) -> ElementType:
    x0, y0, x1, y1 = el.bbox
    w, h = el.width, el.height
    if w <= 0 or h <= 0:
        return ElementType.UNKNOWN
    area_ratio = (w * h) / float(sw * sh)
    aspect = el.aspect
    cy = (y0 + y1) / 2.0

    # Fills (most of) the canvas -> background.
    if area_ratio > 0.7:
        return ElementType.BACKGROUND

    if el.kind == "type":
        # Tall text block or upper third -> headline, else supporting copy.
        big = h > 0.06 * sh
        return ElementType.HEADLINE if (big and cy < 0.55 * sh) else ElementType.SUBTEXT

    # A wide, short strip of high edge density is almost certainly rasterized text.
    if aspect > 3.0 and h < 0.12 * sh:
        arr = np.array(el.image.convert("RGB"))
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 100, 200)
        if edges.mean() > 6:
            return ElementType.HEADLINE if cy < 0.5 * sh else ElementType.SUBTEXT

    # Small, compact, upper area -> likely a logo mark.
    if area_ratio < 0.12 and 0.4 < aspect < 6 and cy < 0.4 * sh:
        return ElementType.LOGO

    # Small strip pinned to the bottom -> disclaimer.
    if cy > 0.85 * sh and aspect > 4:
        return ElementType.DISCLAIMER

    # A mid-sized image is most likely product / hero art.
    if 0.02 < area_ratio < 0.5:
        return ElementType.PRODUCT

    return ElementType.UNKNOWN


def classify(el: SemanticElement, source_w: int, source_h: int) -> ElementType:
    """Full classification: name -> kind -> geometry."""
    hint = name_hint(el.name)
    if hint is not None:
        return hint
    return _classify_by_geometry(el, source_w, source_h)
