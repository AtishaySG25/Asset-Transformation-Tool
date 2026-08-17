"""Semantic elements extracted from the master asset.

An :class:`Element` is one identifiable object (logo, headline, CTA button,
riskometer, ...) with its pixels (RGBA, tight-cropped) and its bounding box on
the source canvas. Roles carry a visual-hierarchy priority that decides which
elements survive when a target format is too small to hold them all.
"""
from __future__ import annotations

from dataclasses import dataclass
from PIL import Image

# Higher priority == more important; kept first in constrained layouts.
ROLE_PRIORITY = {
    "logo": 100,        # brand mark — must always appear
    "cta": 90,          # call to action — the point of an ad
    "scheme": 80,       # product / scheme name
    "headline": 70,
    "object": 62,       # generic salient object (flat-image fallback)
    "subheadline": 55,
    "riskometer": 40,   # regulatory graphic
    "rating": 25,
    "disclaimer": 15,   # fine print
    "background": 0,
}


def classify(name: str) -> str | None:
    """Map a PSD layer/group name to a semantic role (or None if unknown)."""
    n = name.lower()
    if "subheadline" in n or "sub-head" in n or "subhead" in n:
        return "subheadline"
    if "headline" in n:
        return "headline"
    if "scheme" in n:
        return "scheme"
    if "cta" in n or "invest now" in n:
        return "cta"
    if "risk" in n:
        return "riskometer"
    if "rating" in n:          # before "logo": "app-rating-logo" is a rating badge
        return "rating"
    if "logo" in n:
        return "logo"
    if "disclaimer" in n or "market risk" in n or "subject to market" in n:
        return "disclaimer"
    if "background" in n:
        return "background"
    return None


@dataclass
class Element:
    role: str
    name: str
    image: Image.Image           # tight-cropped RGBA
    bbox: tuple[int, int, int, int]  # (l, t, r, b) on the source canvas
    is_type: bool = False        # backed by a PSD type layer (candidate for reflow)
    uid: str = ""                # stable identity — see `assign_uids`
    is_hero: bool = False        # promoted out of a background group — the
                                 # artwork the composition is built around

    @property
    def priority(self) -> int:
        return ROLE_PRIORITY.get(self.role, 50)

    @property
    def width(self) -> int:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> int:
        return self.bbox[3] - self.bbox[1]

    @property
    def cx(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2

    @property
    def cy(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2


def assign_uids(elements: list[Element]) -> list[Element]:
    """Give every element an identity that survives being re-extracted.

    A saved layout has to say *which* element each of its boxes draws, and the
    obvious handles are all properties of the extraction rather than of the PSD:
    the index shifts whenever a layer is consumed as the background or split in
    two, and the role changes whenever :func:`classify` does. The layer name is
    the one thing the designer controls — but names repeat (a PSD can hold five
    layers called "Vector Smart Object"), so the name alone identifies a group,
    not a layer. Numbering the repeats in document order makes it unique without
    making it volatile.
    """
    seen: dict[str, int] = {}
    for el in elements:
        n = seen[el.name] = seen.get(el.name, 0) + 1
        el.uid = el.name if n == 1 else f"{el.name}#{n}"
    return elements
