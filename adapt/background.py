"""Background fill: cover the exact target canvas with no padding.

We scale the source background so it fully covers the target (scale = the larger
of the two axis ratios) and center-crop the overflow. The result is exactly
target-sized with no letterbox bars.
"""
from __future__ import annotations

from PIL import Image


def fill_background(bg_rgb: Image.Image, tw: int, th: int,
                    focus: tuple[float, float] | None = None) -> Image.Image:
    """Return a tw x th RGB image fully covered by ``bg_rgb``.

    ``focus`` is an optional (fx, fy) in [0,1] to bias the center-crop toward the
    most interesting part of the background.
    """
    sw, sh = bg_rgb.size
    scale = max(tw / sw, th / sh)
    nw, nh = max(tw, round(sw * scale)), max(th, round(sh * scale))
    resized = bg_rgb.resize((nw, nh), Image.LANCZOS)

    if focus is None:
        left = (nw - tw) // 2
        top = (nh - th) // 2
    else:
        fx, fy = focus
        left = int(round(fx * nw - tw / 2))
        top = int(round(fy * nh - th / 2))
        left = max(0, min(left, nw - tw))
        top = max(0, min(top, nh - th))

    return resized.crop((left, top, left + tw, top + th)).convert("RGB")
