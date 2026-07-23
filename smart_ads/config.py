"""Static configuration: the target ad formats and strategy tuning knobs.

Kept as pure data so the rest of the pipeline has a single source of truth for
"what are we producing" and "how do we decide to produce it".
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Strategy(Enum):
    """How a given output is produced from the master asset."""
    CROP = "crop"          # saliency-aware crop of the flattened composite
    RELAYOUT = "relayout"  # re-arrange semantic layers into a template


@dataclass(frozen=True)
class Format:
    """A single required output size."""
    name: str
    width: int
    height: int

    @property
    def aspect(self) -> float:
        return self.width / self.height


# The master creative is a square. Everything is reasoned about relative to this.
SOURCE_SIZE = 1080

# A target whose aspect ratio is within this multiplicative factor of the source
# (1:1) is close enough that a smart crop preserves the content. Anything more
# extreme (thin banners, tall skyscraper) must be re-laid-out instead, because a
# crop would either throw away almost everything or squash it.
CROP_ASPECT_TOLERANCE = 2.2

# The six IAB ad sizes required by the assignment.
FORMATS = [
    Format("300x250", 300, 250),  # medium rectangle
    Format("200x200", 200, 200),  # small square
    Format("160x600", 160, 600),  # skyscraper
    Format("970x90", 970, 90),    # large leaderboard
    Format("728x90", 728, 90),    # leaderboard
    Format("468x60", 468, 60),    # full banner
]

FORMATS_BY_NAME = {f.name: f for f in FORMATS}
