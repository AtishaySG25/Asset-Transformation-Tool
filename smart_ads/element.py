"""The semantic element model shared across the pipeline.

An element is one meaningful piece of the master creative (a logo, a headline,
the CTA, ...) together with its tight artwork and where it lived in the source.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Tuple

from PIL import Image


class ElementType(Enum):
    LOGO = "logo"
    HEADLINE = "headline"
    SUBTEXT = "subtext"
    CTA = "cta"
    GRAPH = "graph"          # charts, gauges, riskometers
    PRODUCT = "product"      # product / hero imagery
    DISCLAIMER = "disclaimer"
    BACKGROUND = "background"
    DECORATIVE = "decorative"  # ratings, badges, ornaments, lines
    UNKNOWN = "unknown"


# How much each role matters when space is scarce. Drives both crop importance
# and which elements survive into a cramped banner. Must-keep brand elements
# (logo, CTA, headline) rank highest; legal/decoration are dropped first.
IMPORTANCE = {
    ElementType.LOGO: 1.00,
    ElementType.CTA: 0.95,
    ElementType.HEADLINE: 0.90,
    ElementType.PRODUCT: 0.70,
    ElementType.GRAPH: 0.60,
    ElementType.SUBTEXT: 0.50,
    ElementType.DECORATIVE: 0.20,
    ElementType.DISCLAIMER: 0.20,
    ElementType.BACKGROUND: 0.05,
    ElementType.UNKNOWN: 0.30,
}


@dataclass
class SemanticElement:
    """One classified piece of the creative."""
    name: str
    image: Image.Image                    # tight RGBA artwork (alpha-trimmed)
    bbox: Tuple[int, int, int, int]       # (left, top, right, bottom) clipped to canvas
    kind: str                             # raw psd-tools layer kind
    type: ElementType = ElementType.UNKNOWN

    @property
    def importance(self) -> float:
        return IMPORTANCE.get(self.type, 0.3)

    @property
    def width(self) -> int:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> int:
        return self.bbox[3] - self.bbox[1]

    @property
    def area(self) -> int:
        return max(0, self.width) * max(0, self.height)

    @property
    def aspect(self) -> float:
        h = self.height
        return self.width / h if h > 0 else 1.0

    def __repr__(self) -> str:
        return f"<{self.type.value} {self.name!r} {self.width}x{self.height}>"
