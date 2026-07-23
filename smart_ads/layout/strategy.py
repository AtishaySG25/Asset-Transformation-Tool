"""Pick CROP vs RELAYOUT for a format, given the source aspect ratio."""
from __future__ import annotations

from ..config import CROP_ASPECT_TOLERANCE, Format, Strategy


def choose_strategy(fmt: Format, source_aspect: float = 1.0) -> Strategy:
    """A target close to the source aspect crops cleanly; an extreme one must be
    re-laid-out (a thin banner or tall skyscraper cannot be cropped from a
    square without destroying the content)."""
    ratio = max(fmt.aspect / source_aspect, source_aspect / fmt.aspect)
    return Strategy.CROP if ratio <= CROP_ASPECT_TOLERANCE else Strategy.RELAYOUT
