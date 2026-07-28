"""Target ad formats and the geometry-based strategy router.

The master asset is square (1:1). The six IAB output formats fall into two
very different geometric regimes, and each needs a different transform:

  * near-square  -> a saliency-weighted content-aware CROP is enough
  * extreme ratio -> cropping would show only a sliver, so we RE-LAYOUT the
    individual elements (logo / headline / cta / ...) into the strip.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Format:
    width: int
    height: int

    @property
    def name(self) -> str:
        return f"{self.width}x{self.height}"

    @property
    def aspect(self) -> float:
        return self.width / self.height


# The six required output formats (brief said "5" but lists these 6 IAB sizes).
FORMATS = [
    Format(970, 90),    # super-leaderboard  (10.78 : 1)
    Format(728, 90),    # leaderboard        ( 8.09 : 1)
    Format(160, 600),   # wide skyscraper    ( 0.27 : 1)
    Format(468, 60),    # full banner        ( 7.80 : 1)
    Format(200, 200),   # square             ( 1.00 : 1)
    Format(300, 250),   # medium rectangle   ( 1.20 : 1)
]

# A source-aspect-relative window inside which a plain content-aware crop keeps
# enough of the composition. Outside it, we switch to element re-layout.
CROP_ASPECT_LO = 0.62
CROP_ASPECT_HI = 1.60


# Hard bounds for user-supplied sizes: below MIN_SIDE nothing can be legible at
# any layout, above MAX_SIDE we would be rendering hundreds of megapixels at 2x
# supersampling.
MIN_SIDE = 16
MAX_SIDE = 8000


def parse(name: str) -> Format:
    """'970x90' -> Format(970, 90). Raises ValueError on anything else."""
    try:
        w, h = (int(v) for v in str(name).lower().split("x"))
    except (ValueError, TypeError):
        raise ValueError(f"not a size: {name!r}")
    return Format(w, h)


def validate(w: int, h: int) -> str | None:
    """None if (w, h) is a renderable target, else why not."""
    if not (isinstance(w, int) and isinstance(h, int)):
        return "width and height must be whole numbers"
    if w < MIN_SIDE or h < MIN_SIDE:
        return f"both sides must be at least {MIN_SIDE}px"
    if w > MAX_SIDE or h > MAX_SIDE:
        return f"sides must be at most {MAX_SIDE}px"
    return None


def strategy_for(fmt: Format, source_aspect: float = 1.0) -> str:
    """Return 'crop' or 'reflow' for a target format."""
    rel = fmt.aspect / source_aspect
    if CROP_ASPECT_LO <= rel <= CROP_ASPECT_HI:
        return "crop"
    return "reflow"
