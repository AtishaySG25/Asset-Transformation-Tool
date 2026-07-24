"""Read the master PSD into a complete inventory of semantic elements.

Constraint from the brief: *every* element must survive into every output, so
extraction is exhaustive — nothing is filtered out. Each element keeps its tight
artwork (alpha-trimmed) and its position in the source canvas, which the (later)
layout stage needs to reflow the design into other shapes.

Group handling: a cohesive group (e.g. a "cta" = button + label + icon) is kept
as ONE element; a "logo"/"footer" band that bundles a logo with legal text is
split so each piece is addressable on its own.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from PIL import Image
from psd_tools import PSDImage

# Whole-word keyword -> role. Whole-word matching avoids false hits like 'cta'
# inside 're[cta]ngle'. Roles are advisory metadata for later layout, not filters.
_ROLE_KEYWORDS = [
    ("background", ("background", "backdrop", "bg", "base layer")),
    ("disclaimer", ("disclaimer", "legal", "footer", "terms", "market risk", "read all")),
    ("rating",     ("rating", "review", "star", "app-store", "play store", "badge")),
    ("cta",        ("cta", "invest now", "button", "btn", "apply", "know more", "click")),
    ("graph",      ("graph", "chart", "riskometer", "meter", "gauge", "plot")),
    ("logo",       ("logo", "brand", "wordmark")),
    ("subtext",    ("subheadline", "subhead", "subtitle", "subtext", "tagline")),
    ("headline",   ("headline", "scheme", "title", "heading", "hero")),
]
# Named groups of these roles are bands we split into their children.
_SPLIT_ROLES = {"logo", "disclaimer"}
_MAX_DEPTH = 3


def role_for(name: str) -> Optional[str]:
    n = (name or "").lower()
    for role, keys in _ROLE_KEYWORDS:
        if any(re.search(r"\b" + re.escape(k) + r"\b", n) for k in keys):
            return role
    return None


@dataclass
class Element:
    name: str
    image: Image.Image                 # tight RGBA artwork
    bbox: Tuple[int, int, int, int]    # (left, top, right, bottom) in source coords
    kind: str
    role: Optional[str] = None

    @property
    def size(self) -> Tuple[int, int]:
        return (self.bbox[2] - self.bbox[0], self.bbox[3] - self.bbox[1])


@dataclass
class Source:
    composite: Image.Image
    size: Tuple[int, int]
    elements: List[Element] = field(default_factory=list)


def _tight(img: Image.Image, bbox) -> Optional[Tuple[Image.Image, tuple]]:
    if img is None:
        return None
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    box = img.getbbox()
    if box is None:
        return None
    img = img.crop(box)
    l, t = bbox[0] + box[0], bbox[1] + box[1]
    return img, (l, t, l + img.width, t + img.height)


def _extract(container, depth=0) -> List[Element]:
    out: List[Element] = []
    for layer in container:
        if not layer.visible:
            continue
        role = role_for(layer.name)
        is_group = getattr(layer, "is_group", lambda: False)()
        if is_group and depth < _MAX_DEPTH and (role in _SPLIT_ROLES or role is None):
            children = _extract(layer, depth + 1)
            if children:
                out.extend(children)
                continue
        tight = _tight(layer.composite(), tuple(layer.bbox))
        if tight is None:
            continue
        img, bbox = tight
        out.append(Element(layer.name, img, bbox, str(layer.kind), role))
    return out


def load(path: str) -> Source:
    psd = PSDImage.open(path, ignore_errors=True)
    composite = psd.composite().convert("RGB")
    return Source(composite=composite, size=psd.size, elements=_extract(psd))
