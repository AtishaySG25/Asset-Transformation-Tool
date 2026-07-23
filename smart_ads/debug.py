"""Explainability overlays: show what the tool detected and decided."""
from __future__ import annotations

import os
from typing import List

from PIL import Image, ImageDraw, ImageFont

from .element import ElementType, SemanticElement

_COLORS = {
    ElementType.LOGO: (0, 200, 0),
    ElementType.HEADLINE: (255, 80, 0),
    ElementType.SUBTEXT: (255, 180, 0),
    ElementType.CTA: (220, 0, 0),
    ElementType.GRAPH: (0, 150, 255),
    ElementType.PRODUCT: (180, 0, 255),
    ElementType.DISCLAIMER: (120, 120, 120),
    ElementType.BACKGROUND: (60, 60, 60),
    ElementType.DECORATIVE: (0, 180, 180),
    ElementType.UNKNOWN: (255, 0, 255),
}


def _font(size: int):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()


def save_classification_overlay(composite: Image.Image,
                                elements: List[SemanticElement], path: str) -> None:
    """Draw each detected element's box + label over the master composite."""
    canvas = composite.convert("RGB").copy()
    draw = ImageDraw.Draw(canvas)
    font = _font(max(12, composite.width // 60))
    for el in elements:
        if el.type == ElementType.BACKGROUND:
            continue
        color = _COLORS.get(el.type, (255, 0, 255))
        draw.rectangle(el.bbox, outline=color, width=3)
        label = f"{el.type.value} ({el.importance:.2f})"
        tx, ty = el.bbox[0] + 2, max(0, el.bbox[1] - font.size - 2)
        draw.rectangle([tx, ty, tx + len(label) * font.size * 0.6, ty + font.size + 2],
                       fill=color)
        draw.text((tx + 1, ty), label, fill=(255, 255, 255), font=font)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    canvas.save(path)
